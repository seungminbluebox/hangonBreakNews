"""Production GNews collector and safe one-shot preview.

The production worker preserves the existing public ``breaking_news``
contract while storing the provider body in a private column. Provider
metadata is not mixed into the user-facing title or summary.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import partial
import json
import os
import time

from gnews_adapter import (
    GNewsClient,
    ScheduledHeadlineCollector,
    collect_default_headlines,
)
from news_selector import NEWS_SELECTION_RESPONSE_FORMAT, select_and_summarize
from news_pipeline import (
    SELECTION_RESPONSE_FORMAT,
    SUMMARY_RESPONSE_FORMAT,
    run_two_stage_pipeline,
)
from openrouter_budget import OpenRouterBudgetError, is_free_model
from cycle_logging import cycle_scope, current_context, log_event


DEFAULT_GNEWS_AI_MODEL = "openrouter/free"
DEFAULT_GNEWS_AI_BACKUP_MODEL = "openrouter/free"
DEFAULT_CYCLE_SECONDS = 300
DEFAULT_DAILY_REQUEST_LIMIT = 950
DEFAULT_GNEWS_MAX_TOKENS = 8192
DEFAULT_AI_BATCH_SIZE = 10
RECENT_DUPLICATE_WINDOW_HOURS = 24
RECENT_DUPLICATE_LIMIT = 300
MAX_QUALITY_RETRIES = 3
MAX_CARRIED_QUALITY_RETRIES_PER_CYCLE = 10
PENDING_EXPIRY = timedelta(hours=3)


@dataclass
class TrackerState:
    """Process-local work queue.

    Failed AI or database work stays in ``pending`` for the next cycle.
    Successfully rejected or stored URLs stay in the bounded evaluated cache,
    while the database remains the durable duplicate source across restarts.
    """

    pending: dict[str, dict] = field(default_factory=dict)
    evaluated_urls: dict[str, None] = field(default_factory=dict)
    quality_retry_counts: dict[str, int] = field(default_factory=dict)
    evaluated_limit: int = 5000

    def remember_evaluated(self, url: str) -> None:
        self.quality_retry_counts.pop(url, None)
        self.evaluated_urls.pop(url, None)
        self.evaluated_urls[url] = None
        while len(self.evaluated_urls) > self.evaluated_limit:
            oldest_url = next(iter(self.evaluated_urls))
            self.evaluated_urls.pop(oldest_url, None)


class DailyRequestBudget:
    """Count every attempted GNews HTTP request and stop before hard quota."""

    def __init__(self, limit=DEFAULT_DAILY_REQUEST_LIMIT, clock=None):
        self.limit = int(limit)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.day = None
        self.used = 0

    def consume(self) -> None:
        current_day = self.clock().astimezone(timezone.utc).date()
        if current_day != self.day:
            self.day = current_day
            self.used = 0
        if self.used >= self.limit:
            raise RuntimeError(
                f"GNews daily safety limit reached ({self.limit} requests)."
            )
        self.used += 1


def normalize_importance(value) -> int:
    """Round half-up and guarantee the integer range accepted by storage."""
    if isinstance(value, bool):
        raise ValueError("importance_score must be numeric")
    try:
        rounded = int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("importance_score must be numeric") from error
    return min(10, max(7, rounded))


def to_breaking_news_row(news_item: dict) -> dict:
    """Map one selected article to the existing breaking_news DB schema."""
    raw_content = news_item.get("raw_content")
    source_content = (
        raw_content
        if isinstance(raw_content, str) and raw_content.strip()
        else None
    )
    return {
        "title": news_item["normalized_title"],
        "content": news_item["normalized_content"],
        "importance_score": normalize_importance(news_item["importance_score"]),
        "category": news_item["category"],
        "original_url": news_item["original_url"],
        "source_content": source_content,
    }


class SupabaseBreakingNewsRepository:
    """Exact-URL idempotency and writes for the existing breaking_news table."""

    def __init__(self, client, *, query_chunk_size=100):
        self.client = client
        self.query_chunk_size = query_chunk_size

    def existing_urls(self, urls) -> set[str]:
        unique_urls = list(dict.fromkeys(url for url in urls if url))
        existing = set()
        for start in range(0, len(unique_urls), self.query_chunk_size):
            chunk = unique_urls[start : start + self.query_chunk_size]
            if not chunk:
                continue
            response = (
                self.client.table("breaking_news")
                .select("original_url")
                .in_("original_url", chunk)
                .execute()
            )
            existing.update(
                row["original_url"]
                for row in (response.data or [])
                if row.get("original_url")
            )
        return existing

    def recent_news(self, since: datetime, limit=RECENT_DUPLICATE_LIMIT) -> list[dict]:
        response = (
            self.client.table("breaking_news")
            .select("title,content,created_at")
            .gte("created_at", since.isoformat())
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [
            {
                "title": row.get("title") or "",
                "content": row.get("content") or "",
                "created_at": row.get("created_at"),
            }
            for row in (response.data or [])
            if row.get("title") or row.get("content")
        ]

    def save(self, news_item: dict) -> bool:
        url = news_item["original_url"]
        if self.existing_urls([url]):
            return False
        self.client.table("breaking_news").insert(
            to_breaking_news_row(news_item)
        ).execute()
        return True


def publish_breaking_news(news_item: dict, *, revalidate, push) -> None:
    """Preserve the existing page refresh and notification contract."""
    score = normalize_importance(news_item["importance_score"])
    if score >= 9:
        prefix = "🚨[긴급 속보]"
        target_categories = ("breaking_news", "important_breaking_news")
    else:
        prefix = "[주요 경제 소식]"
        target_categories = ("breaking_news",)

    revalidate("/live")
    revalidate("/")
    push(
        title=f"{prefix} {news_item['normalized_title']}",
        body=news_item["normalized_content"],
        url="/live",
        categories=target_categories,
    )


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _pending_age(item: dict, now: datetime) -> timedelta | None:
    """Return age, treating missing or malformed provider dates as expired."""
    value = item.get("published_at")
    if not isinstance(value, str) or not value.strip():
        return PENDING_EXPIRY + timedelta(seconds=1)
    try:
        created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return PENDING_EXPIRY + timedelta(seconds=1)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)


def _filter_pipeline_candidates(items, *, state=None):
    """Apply the deterministic source filters shared by production and preview."""
    from news_selector import (
        EXCLUDED_FEED_SOURCE_IDS,
        _is_low_value_item,
        _is_minor_card_product_change,
        _is_obvious_analysis_title,
        _is_repackaged_old_event,
    )

    filtered = []
    excluded_urls = set()
    for item in items:
        url = item.get("original_url")
        if (
            item.get("source_id") in EXCLUDED_FEED_SOURCE_IDS
            or _is_obvious_analysis_title(item.get("raw_title") or "")
            or _is_minor_card_product_change(item)
            or _is_repackaged_old_event(item)
            or _is_low_value_item(item)
        ):
            if url:
                excluded_urls.add(url)
                if state is not None:
                    state.remember_evaluated(url)
        else:
            filtered.append(item)
    return filtered, excluded_urls


def run_cycle(
    client,
    generator,
    repository,
    publisher,
    state: TrackerState,
    *,
    collector=collect_default_headlines,
    selector=select_and_summarize,
    clock=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    output=print,
    batch_size=DEFAULT_AI_BATCH_SIZE,
):
    """Run one non-overlapping fetch, AI selection, save, and push cycle."""
    stats = {
        "fetched": 0,
        "fetch_failures": 0,
        "selected": 0,
        "saved": 0,
        "duplicates": 0,
        "rejected": 0,
        "quality_retries": 0,
        "quality_retry_exhausted": 0,
        "ai_failures": 0,
        "db_failures": 0,
        "ai_blocked": 0,
        "expired": 0,
    }

    fetched_at = clock()
    try:
        fetched_articles = collector(
            client,
            fetched_at=fetched_at,
            sleeper=sleeper,
        )
    except Exception as error:
        fetched_articles = []
        stats["fetch_failures"] += 1
        output(f"GNews fetch failed; existing pending work will continue: {error}")
    stats["fetched"] = len(fetched_articles)

    for item in fetched_articles:
        url = item.get("original_url")
        if not url or url in state.evaluated_urls:
            continue
        state.pending.setdefault(url, item)

    for url, item in list(state.pending.items()):
        age = _pending_age(item, fetched_at)
        if age is not None and age > PENDING_EXPIRY:
            state.pending.pop(url, None)
            state.quality_retry_counts.pop(url, None)
            state.remember_evaluated(url)
            stats["expired"] += 1

    try:
        duplicate_urls = repository.existing_urls(state.pending.keys())
    except Exception as error:
        output(f"DB duplicate check failed; pending work retained: {error}")
        stats["db_failures"] += 1
        stats["pending"] = len(state.pending)
        return stats

    for url in duplicate_urls:
        state.pending.pop(url, None)
        state.remember_evaluated(url)
    stats["duplicates"] = len(duplicate_urls)

    try:
        recent_news = repository.recent_news(
            fetched_at - timedelta(hours=RECENT_DUPLICATE_WINDOW_HOURS),
            limit=RECENT_DUPLICATE_LIMIT,
        )
    except Exception as error:
        recent_news = []
        stats["db_failures"] += 1
        output(f"Recent-news duplicate context unavailable; continuing: {error}")

    fresh_articles = [
        item
        for url, item in state.pending.items()
        if url not in state.quality_retry_counts
    ]
    carried_retries = [
        item
        for url, item in state.pending.items()
        if url in state.quality_retry_counts
    ][:MAX_CARRIED_QUALITY_RETRIES_PER_CYCLE]
    processing_batches = [
        *_chunks(fresh_articles, batch_size),
        *_chunks(carried_retries, batch_size),
    ]

    def save_selected(selected_by_url):
        for url, selected_item in selected_by_url.items():
            try:
                selected_item = selected_item.copy()
                selected_item["importance_score"] = normalize_importance(
                    selected_item["importance_score"]
                )
                was_saved = repository.save(selected_item)
            except Exception as error:
                stats["db_failures"] += 1
                output(f"DB insert failed; article retained for retry: {error}")
                continue

            state.pending.pop(url, None)
            state.remember_evaluated(url)
            if not was_saved:
                stats["duplicates"] += 1
                continue

            stats["saved"] += 1
            recent_news.append(
                {
                    "title": selected_item["normalized_title"],
                    "content": selected_item["normalized_content"],
                }
            )
            try:
                publisher(selected_item)
            except Exception as error:
                output(f"News saved, but notification failed: {error}")

    for batch in processing_batches:
        try:
            selected_articles = selector(
                batch,
                generator,
                recent_news=recent_news,
            )
        except OpenRouterBudgetError as error:
            stats["ai_blocked"] += 1
            partial_by_url = {
                item["original_url"]: item
                for item in getattr(error, "partial_results", ())
                if item.get("original_url") in state.pending
            }
            stats["selected"] += len(partial_by_url)
            save_selected(partial_by_url)
            output(
                f"AI requests blocked ({type(error).__name__}); "
                f"{len(batch) - len(partial_by_url)} "
                "article(s) retained for a later cycle."
            )
            break
        except Exception as error:
            stats["ai_failures"] += 1
            output(f"AI selection failed; {len(batch)} article(s) retained: {error}")
            continue

        retryable_urls = {
            url
            for url in getattr(selected_articles, "retryable_urls", frozenset())
            if url in state.pending
        }
        selected_by_url = {
            item["original_url"]: item
            for item in selected_articles
            if item.get("original_url") in state.pending
        }
        stats["selected"] += len(selected_by_url)

        for source in batch:
            url = source["original_url"]
            if url in selected_by_url:
                continue
            if url in retryable_urls:
                retry_count = state.quality_retry_counts.get(url, 0) + 1
                stats["quality_retries"] += 1
                if retry_count >= MAX_QUALITY_RETRIES:
                    state.pending.pop(url, None)
                    state.remember_evaluated(url)
                    stats["quality_retry_exhausted"] += 1
                else:
                    state.quality_retry_counts[url] = retry_count
                continue
            state.pending.pop(url, None)
            state.remember_evaluated(url)
            stats["rejected"] += 1

        save_selected(selected_by_url)

    stats["pending"] = len(state.pending)
    output(
        "GNews cycle: "
        f"fetched={stats['fetched']} selected={stats['selected']} "
        f"saved={stats['saved']} rejected={stats['rejected']} "
        f"quality_retries={stats['quality_retries']} "
        f"quality_retry_exhausted={stats['quality_retry_exhausted']} "
        f"duplicates={stats['duplicates']} expired={stats['expired']} "
        f"ai_blocked={stats['ai_blocked']} pending={stats['pending']}"
    )
    return stats


def _run_simple_cycle(
    client,
    generator,
    repository,
    publisher,
    state: TrackerState,
    *,
    collector=collect_default_headlines,
    clock=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    output=print,
    _stats=None,
):
    """Process only the current fetch with one selection and one summary stage."""
    defaults = {
        "fetched": 0,
        "candidates": 0,
        "selected": 0,
        "saved": 0,
        "duplicates": 0,
        "rejected": 0,
        "ai_failures": 0,
        "ai_blocked": 0,
        "db_failures": 0,
        "quality_failed": 0,
        "notify_failures": 0,
        "fetch_failures": 0,
        "cut": 0,
    }
    stats = _stats if _stats is not None else {}
    for key, value in defaults.items():
        stats.setdefault(key, value)
    state.pending.clear()
    state.quality_retry_counts.clear()
    fetched_at = clock()
    try:
        fetched_articles = collector(client, fetched_at=fetched_at, sleeper=sleeper)
    except Exception as error:
        stats["fetch_failures"] += 1
        context = current_context()
        if context is not None:
            context.failure_stage = "fetch"
            context.failure_reason = type(error).__name__
        log_event("gnews_fetch_failed", level=40, stage="fetch", error=type(error).__name__)
        return stats
    stats["fetched"] = len(fetched_articles)
    articles_by_url = {
        item.get("original_url"): item
        for item in fetched_articles
        if item.get("original_url") and item.get("original_url") not in state.evaluated_urls
    }
    try:
        context = current_context()
        if context is not None:
            context.set_stage("context")
        duplicate_urls = repository.existing_urls(articles_by_url)
        recent_news = repository.recent_news(
            fetched_at - timedelta(hours=RECENT_DUPLICATE_WINDOW_HOURS),
            limit=RECENT_DUPLICATE_LIMIT,
        )
    except Exception as error:
        stats["db_failures"] += 1
        context = current_context()
        if context is not None:
            context.failure_stage = "context"
            context.failure_reason = type(error).__name__
        log_event("db_context_failed", level=40, stage="context", error=type(error).__name__)
        return stats
    for url in duplicate_urls:
        articles_by_url.pop(url, None)
        state.remember_evaluated(url)
    stats["duplicates"] = len(duplicate_urls)
    candidates = list(articles_by_url.values())
    filtered, excluded_urls = _filter_pipeline_candidates(candidates, state=state)
    stats["candidates"] = len(filtered)
    stats["rejected"] += len(excluded_urls)
    if not filtered:
        return stats
    try:
        result = run_two_stage_pipeline(filtered, generator, recent_news=recent_news)
    except OpenRouterBudgetError as error:
        if not stats.get("ai_blocked"):
            stats["ai_blocked"] += 1
        context = current_context()
        if context is not None:
            context.failure_stage = "ai"
            context.failure_reason = "budget_blocked"
        return stats
    except Exception as error:
        stats["ai_failures"] += 1
        context = current_context()
        if context is not None:
            context.failure_stage = "ai"
            context.failure_reason = type(error).__name__
        return stats
    for url in result.evaluated_urls:
        state.remember_evaluated(url)
    stats["cut"] = len(result.cut_urls)
    stats["selected"] = len(result.selected)
    stats["quality_failed"] = getattr(result, "quality_failed", 0)
    stats["rejected"] += len(result.evaluated_urls)
    context = current_context()
    if context is not None and result.selected:
        context.set_stage("save")
    for item in result.selected:
        try:
            normalized = item.copy()
            normalized["importance_score"] = normalize_importance(
                normalized["importance_score"]
            )
            saved = repository.save(normalized)
        except Exception as error:
            stats["db_failures"] += 1
            context = current_context()
            if context is not None:
                context.failure_stage = "save"
                context.failure_reason = type(error).__name__
            log_event("db_insert_failed", level=40, stage="save", error=type(error).__name__)
            continue
        state.remember_evaluated(item["original_url"])
        if not saved:
            stats["duplicates"] += 1
            continue
        stats["saved"] += 1
        try:
            context = current_context()
            if context is not None:
                context.set_stage("notify")
            publisher(normalized)
        except Exception as error:
            stats["notify_failures"] += 1
            context = current_context()
            if context is not None:
                context.failure_stage = "notify"
                context.failure_reason = type(error).__name__
            log_event("notification_failed", level=40, stage="notify", error=type(error).__name__)
    return stats


def run_simple_cycle(*args, **kwargs):
    """Run one current-fetch cycle and emit exactly one structured summary."""
    output_sink = kwargs.get("output")
    if output_sink is print:
        output_sink = None
    with cycle_scope(output=output_sink) as context:
        try:
            stats = _run_simple_cycle(*args, _stats=context.stats, **kwargs)
        except Exception as error:
            context.failure_stage = context.stage
            context.failure_reason = type(error).__name__
            log_event("cycle_failed", level=40, stage=context.stage, error=type(error).__name__)
            log_event(
                "cycle_trace",
                level=10,
                stage=context.stage,
                location="gnews_tracker.run_simple_cycle",
            )
            raise
        if context.stats.get("ai_blocked"):
            context.set_status("blocked")
        elif context.stats.get("fetch_failures"):
            context.set_status("fetch_failed")
        elif (
            context.stats.get("ai_failures")
            or context.stats.get("db_failures")
            or context.stats.get("notify_failures")
        ):
            context.set_status("failed")
        elif context.stats.get("quality_failed"):
            context.set_status("partial")
        elif not context.stats.get("candidates"):
            context.set_status("empty")
        else:
            context.set_status("ok")
        return stats


def next_cycle_delay(started_at, finished_at, *, interval_seconds=DEFAULT_CYCLE_SECONDS):
    """Return only the unused part of a start-to-start interval."""
    return max(0, interval_seconds - (finished_at - started_at))


def run_forever(
    cycle,
    *,
    interval_seconds=DEFAULT_CYCLE_SECONDS,
    monotonic=time.monotonic,
    sleeper=time.sleep,
    output=print,
):
    """Run one cycle at a time; a slow cycle never overlaps the next one."""
    output(f"GNews production tracker started ({interval_seconds}s interval).")
    while True:
        started_at = monotonic()
        try:
            cycle()
        except KeyboardInterrupt:
            raise
        except Exception as error:
            log_event("cycle_failed", level=40, stage="cycle", error=type(error).__name__)
        finished_at = monotonic()
        delay = next_cycle_delay(
            started_at,
            finished_at,
            interval_seconds=interval_seconds,
        )
        if delay:
            sleeper(delay)


def run_dry_run(
    api_key,
    generator,
    *,
    client_factory=GNewsClient,
    collector=collect_default_headlines,
    selector=select_and_summarize,
    clock=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    output=print,
    batch_size=DEFAULT_AI_BATCH_SIZE,
):
    """Fetch once and print fields useful for reviewing source quality."""
    output(
        "GNews dry run: fetching Korea, US, and world business headlines "
        "plus world headlines..."
    )
    client = client_factory(api_key=api_key)
    collected_articles = collector(
        client,
        fetched_at=clock(),
        sleeper=sleeper,
    )
    if selector is select_and_summarize:
        filtered, _ = _filter_pipeline_candidates(collected_articles)
        result = run_two_stage_pipeline(filtered, generator)
        articles = result.selected
        preview = [
            {
                "market_scope": item["market_scope"],
                "source_name": item["source_name"],
                "published_at": item["published_at"],
                "title": item["normalized_title"],
                "content": item["normalized_content"],
                "importance_score": item["importance_score"],
                "category": item["category"],
                "news_type": item["news_type"],
                "selection_reason": item["selection_reason"],
                "raw_title": item["raw_title"],
                "original_url": item["original_url"],
            }
            for item in articles
        ]
        output(json.dumps(preview, ensure_ascii=False, indent=2))
        return articles

    # Keep the injectable legacy selector contract for existing callers.
    batches = list(_chunks(collected_articles, batch_size))
    output(
        f"GNews dry run: fetched={len(collected_articles)} "
        f"ai_batches={len(batches)}"
    )
    articles = []
    failed_batches = 0
    for batch_number, batch in enumerate(batches, start=1):
        output(
            f"GNews dry run: processing AI batch "
            f"{batch_number}/{len(batches)} ({len(batch)} articles)..."
        )
        try:
            articles.extend(selector(batch, generator))
        except Exception as error:
            failed_batches += 1
            output(
                f"GNews dry run: AI batch {batch_number}/{len(batches)} "
                f"failed and was discarded: {error}"
            )
    if failed_batches:
        output(
            f"GNews dry run warning: {failed_batches}/{len(batches)} AI batch(es) "
            "failed. No failed batch result will be stored."
        )
    preview = [
        {
            "market_scope": item["market_scope"],
            "source_name": item["source_name"],
            "published_at": item["published_at"],
            "title": item["normalized_title"],
            "content": item["normalized_content"],
            "importance_score": item["importance_score"],
            "category": item["category"],
            "news_type": item["news_type"],
            "selection_reason": item["selection_reason"],
            "raw_title": item["raw_title"],
            "original_url": item["original_url"],
        }
        for item in articles
    ]
    output(json.dumps(preview, ensure_ascii=False, indent=2))
    return articles


def run_production(
    api_key,
    generator,
    *,
    environment=os.environ,
    interval_seconds=DEFAULT_CYCLE_SECONDS,
    output=print,
):
    """Build live dependencies lazily, then start the production loop."""
    from supabase import create_client
    from push_notification import send_push_notification
    from revalidate import revalidate_path

    supabase = create_client(
        environment["SUPABASE_URL"],
        environment["SUPABASE_KEY"],
    )
    repository = SupabaseBreakingNewsRepository(supabase)
    publisher = partial(
        publish_breaking_news,
        revalidate=revalidate_path,
        push=send_push_notification,
    )
    state = TrackerState()
    request_budget = DailyRequestBudget(
        limit=int(environment.get("GNEWS_DAILY_SAFETY_LIMIT", DEFAULT_DAILY_REQUEST_LIMIT))
    )
    client = GNewsClient(
        api_key=api_key,
        before_request=request_budget.consume,
    )

    cycle = partial(
        run_simple_cycle,
        client,
        generator,
        repository,
        publisher,
        state,
        collector=ScheduledHeadlineCollector(),
        output=output,
    )
    return run_forever(
        cycle,
        interval_seconds=interval_seconds,
        output=output,
    )


class GNewsStageGenerator:
    supports_stage_options = True

    def __init__(self, *, model_name, backup_model_name, selection_max_tokens, summary_max_tokens):
        from llm_helper import safe_generate_content

        self._safe_generate_content = safe_generate_content
        self.model_name = model_name
        self.backup_model_name = backup_model_name
        self.selection_max_tokens = selection_max_tokens
        self.summary_max_tokens = summary_max_tokens
        self.func = safe_generate_content
        self.keywords = {
            "max_retries": 1,
            "model_name": model_name,
            "backup_model_name": backup_model_name,
            "response_format": SELECTION_RESPONSE_FORMAT,
            "provider_preferences": {
                "require_parameters": True,
                "allow_fallbacks": True,
            },
            "request_timeout": 60,
        }

    def __call__(self, prompt, *, stage="selection"):
        return self._safe_generate_content(
            prompt,
            max_retries=1,
            model_name=self.model_name,
            backup_model_name=self.backup_model_name,
            response_format=(
                SELECTION_RESPONSE_FORMAT
                if stage == "selection"
                else SUMMARY_RESPONSE_FORMAT
            ),
            provider_preferences={
                "require_parameters": True,
                "allow_fallbacks": True,
            },
            request_timeout=60,
            max_tokens=(
                self.selection_max_tokens
                if stage == "selection"
                else self.summary_max_tokens
            ),
        )


def _build_generator(environment):
    from llm_helper import safe_generate_content

    model_name = environment.get("GNEWS_AI_MODEL_NAME", DEFAULT_GNEWS_AI_MODEL)
    backup_model_name = environment.get(
        "GNEWS_AI_BACKUP_MODEL", DEFAULT_GNEWS_AI_BACKUP_MODEL
    )
    if not is_free_model(model_name) or not is_free_model(backup_model_name):
        raise ValueError("GNews AI models must be free OpenRouter models.")

    def positive_int(name):
        value = int(environment.get(name, DEFAULT_GNEWS_MAX_TOKENS))
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    selection_max_tokens = positive_int("GNEWS_SELECTION_MAX_TOKENS")
    summary_max_tokens = positive_int("GNEWS_SUMMARY_MAX_TOKENS")
    return GNewsStageGenerator(
        model_name=model_name,
        backup_model_name=backup_model_name,
        selection_max_tokens=selection_max_tokens,
        summary_max_tokens=summary_max_tokens,
    )


def main(
    environment=os.environ,
    generator=None,
    runner=None,
    *,
    dry_run=False,
    dry_run_runner=run_dry_run,
    production_runner=run_production,
):
    """Validate configuration and run production by default."""
    for key_name in ("GNEWS_API_KEY", "OPENROUTER_API_KEY"):
        if not environment.get(key_name):
            raise SystemExit(f"{key_name} is not configured.")

    # ``runner`` is retained for callers of the earlier one-shot interface.
    if not dry_run and runner is None:
        for key_name in ("SUPABASE_URL", "SUPABASE_KEY"):
            if not environment.get(key_name):
                raise SystemExit(f"{key_name} is not configured.")

    if generator is None:
        generator = _build_generator(environment)

    api_key = environment["GNEWS_API_KEY"]
    if runner is not None:
        return runner(api_key, generator)
    if dry_run:
        return dry_run_runner(api_key, generator)
    return production_runner(api_key, generator, environment=environment)


def cli(argv=None):
    parser = argparse.ArgumentParser(description="GNews breaking-news tracker")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and print one preview without database or push side effects",
    )
    args = parser.parse_args(argv)
    return main(dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass

    cli()
