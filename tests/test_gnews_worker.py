from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call

from gnews_tracker import (
    DailyRequestBudget,
    SupabaseBreakingNewsRepository,
    TrackerState,
    next_cycle_delay,
    normalize_importance,
    publish_breaking_news,
    run_cycle,
    to_breaking_news_row,
)


def article(article_id="article-1", url="https://example.com/article-1"):
    return {
        "provider": "gnews",
        "provider_article_id": article_id,
        "source_id": "example.com",
        "source_name": "Example News",
        "source_tier": "unrated",
        "source_url": "https://example.com",
        "source_country": "us",
        "original_url": url,
        "published_at": "2026-08-03T01:00:00+00:00",
        "updated_at": None,
        "fetched_at": "2026-08-03T02:00:00+00:00",
        "original_timezone": "UTC",
        "original_language": "en",
        "market_scope": "us",
        "raw_title": f"Raw headline {article_id}",
        "raw_description": f"Raw description {article_id}",
        "raw_content": f"Raw content {article_id}",
        "normalized_title": None,
        "normalized_content": None,
        "image_url": None,
    }


def selected(source, score=8):
    return {
        **source,
        "normalized_title": "미국 경제지표 발표",
        "normalized_content": "미국에서 새로운 경제지표가 발표됐습니다.",
        "importance_score": score,
        "category": "indicator",
        "news_type": "official_announcement",
        "selection_reason": "새 경제지표가 공식 발표됨",
    }


class FakeRepository:
    def __init__(self, existing_urls=(), failures=0, recent_news=()):
        self.existing = set(existing_urls)
        self.failures = failures
        self.recent = list(recent_news)
        self.recent_queries = []
        self.saved = []

    def existing_urls(self, urls):
        return self.existing.intersection(urls)

    def recent_news(self, since, limit=100):
        self.recent_queries.append((since, limit))
        return [item.copy() for item in self.recent]

    def save(self, news_item):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("database unavailable")
        if news_item["original_url"] in self.existing:
            return False
        self.existing.add(news_item["original_url"])
        self.saved.append(news_item.copy())
        return True


class ImportanceNormalizationTests(unittest.TestCase):
    def test_rounds_half_up_and_clamps_to_storage_range(self):
        cases = (
            (6.1, 7),
            (7.2, 7),
            (8.5, 9),
            (9.5, 10),
            (11, 10),
        )

        for value, expected in cases:
            with self.subTest(value=value):
                result = normalize_importance(value)
                self.assertEqual(result, expected)
                self.assertIsInstance(result, int)


class GNewsCycleTests(unittest.TestCase):
    def test_supplies_recent_twenty_four_hour_news_to_duplicate_selection(self):
        source = article()
        recent_item = {
            "title": "FAA, 보잉 MAX 인증 발급",
            "content": "미 연방항공청이 보잉 MAX 인증을 발급했습니다.",
            "created_at": "2026-08-03T01:00:00+00:00",
        }
        repository = FakeRepository(recent_news=[recent_item])
        selector = Mock(return_value=[])
        generator = Mock()
        fetched_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)

        run_cycle(
            object(),
            generator,
            repository,
            Mock(),
            TrackerState(),
            collector=Mock(return_value=[source]),
            selector=selector,
            clock=lambda: fetched_at,
            sleeper=Mock(),
            output=Mock(),
        )

        self.assertEqual(
            repository.recent_queries,
            [(fetched_at - timedelta(hours=24), 300)],
        )
        selector.assert_called_once_with(
            [source],
            generator,
            recent_news=[recent_item],
        )

    def test_adds_saved_news_to_recent_context_for_later_ai_batches(self):
        first = article("article-1", "https://example.com/article-1")
        second = article("article-2", "https://example.com/article-2")
        first_selected = selected(first)
        selector_recent_contexts = []

        def select_batch(batch, generator, *, recent_news):
            selector_recent_contexts.append([item.copy() for item in recent_news])
            if batch[0]["provider_article_id"] == "article-1":
                return [first_selected]
            return []

        run_cycle(
            object(),
            Mock(),
            FakeRepository(),
            Mock(),
            TrackerState(),
            collector=Mock(return_value=[first, second]),
            selector=select_batch,
            clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
            sleeper=Mock(),
            output=Mock(),
            batch_size=1,
        )

        self.assertEqual(selector_recent_contexts[0], [])
        self.assertEqual(
            selector_recent_contexts[1],
            [
                {
                    "title": first_selected["normalized_title"],
                    "content": first_selected["normalized_content"],
                }
            ],
        )

    def test_skips_exact_url_already_saved_in_breaking_news(self):
        duplicate = article()
        repository = FakeRepository(existing_urls=[duplicate["original_url"]])
        selector = Mock()
        publisher = Mock()
        state = TrackerState()

        run_cycle(
            object(),
            Mock(),
            repository,
            publisher,
            state,
            collector=Mock(return_value=[duplicate]),
            selector=selector,
            clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
            sleeper=Mock(),
            output=Mock(),
        )

        selector.assert_not_called()
        publisher.assert_not_called()
        self.assertEqual(repository.saved, [])
        self.assertEqual(state.pending, {})

    def test_keeps_article_pending_when_ai_fails_then_retries_next_cycle(self):
        source = article()
        repository = FakeRepository()
        selector = Mock(side_effect=[RuntimeError("AI unavailable"), [selected(source)]])
        collector = Mock(side_effect=[[source], []])
        publisher = Mock()
        state = TrackerState()

        for _ in range(2):
            run_cycle(
                object(),
                Mock(),
                repository,
                publisher,
                state,
                collector=collector,
                selector=selector,
                clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
                sleeper=Mock(),
                output=Mock(),
            )

        self.assertEqual(selector.call_count, 2)
        self.assertEqual(repository.saved[0]["original_url"], source["original_url"])
        publisher.assert_called_once()
        self.assertEqual(state.pending, {})

    def test_processes_pending_article_even_when_next_fetch_fails(self):
        source = article()
        state = TrackerState(pending={source["original_url"]: source})
        repository = FakeRepository()
        publisher = Mock()

        run_cycle(
            object(),
            Mock(),
            repository,
            publisher,
            state,
            collector=Mock(side_effect=RuntimeError("GNews unavailable")),
            selector=Mock(return_value=[selected(source)]),
            clock=lambda: datetime(2026, 8, 3, 2, 5, tzinfo=timezone.utc),
            sleeper=Mock(),
            output=Mock(),
        )

        self.assertEqual(len(repository.saved), 1)
        publisher.assert_called_once()
        self.assertEqual(state.pending, {})

    def test_does_not_send_successfully_rejected_article_to_ai_again(self):
        source = article()
        selector = Mock(return_value=[])
        collector = Mock(side_effect=[[source], [source]])
        state = TrackerState()

        for _ in range(2):
            run_cycle(
                object(),
                Mock(),
                FakeRepository(),
                Mock(),
                state,
                collector=collector,
                selector=selector,
                clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
                sleeper=Mock(),
                output=Mock(),
            )

        selector.assert_called_once()
        self.assertIn(source["original_url"], state.evaluated_urls)
        self.assertEqual(state.pending, {})

    def test_keeps_selected_article_pending_until_database_insert_succeeds(self):
        source = article()
        repository = FakeRepository(failures=1)
        selector = Mock(return_value=[selected(source, score=7.2)])
        collector = Mock(side_effect=[[source], []])
        publisher = Mock()
        state = TrackerState()

        run_cycle(
            object(), Mock(), repository, publisher, state,
            collector=collector, selector=selector,
            clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
            sleeper=Mock(), output=Mock(),
        )
        self.assertIn(source["original_url"], state.pending)
        publisher.assert_not_called()

        run_cycle(
            object(), Mock(), repository, publisher, state,
            collector=collector, selector=selector,
            clock=lambda: datetime(2026, 8, 3, 2, 5, tzinfo=timezone.utc),
            sleeper=Mock(), output=Mock(),
        )

        self.assertEqual(repository.saved[0]["importance_score"], 7)
        publisher.assert_called_once()
        self.assertEqual(state.pending, {})


class ExistingContractTests(unittest.TestCase):
    def test_reads_only_recent_duplicate_context_from_existing_table(self):
        query = Mock()
        query.select.return_value = query
        query.gte.return_value = query
        query.order.return_value = query
        query.limit.return_value = query
        query.execute.return_value = Mock(
            data=[
                {
                    "title": "기존 뉴스",
                    "content": "기존 뉴스 내용입니다.",
                    "created_at": "2026-08-03T01:00:00+00:00",
                }
            ]
        )
        client = Mock()
        client.table.return_value = query
        repository = SupabaseBreakingNewsRepository(client)
        since = datetime(2026, 8, 2, 23, 0, tzinfo=timezone.utc)

        result = repository.recent_news(since, limit=100)

        self.assertEqual(result[0]["title"], "기존 뉴스")
        client.table.assert_called_once_with("breaking_news")
        query.select.assert_called_once_with("title,content,created_at")
        query.gte.assert_called_once_with("created_at", since.isoformat())
        query.order.assert_called_once_with("created_at", desc=True)
        query.limit.assert_called_once_with(100)

    def test_maps_selected_article_to_existing_breaking_news_columns_only(self):
        row = to_breaking_news_row(selected(article(), score=9))

        self.assertEqual(
            row,
            {
                "title": "미국 경제지표 발표",
                "content": "미국에서 새로운 경제지표가 발표됐습니다.",
                "importance_score": 9,
                "category": "indicator",
                "original_url": "https://example.com/article-1",
            },
        )

    def test_regular_news_targets_realtime_news_subscribers(self):
        for score in (7, 8):
            with self.subTest(score=score):
                push = Mock()

                publish_breaking_news(
                    selected(article(), score=score),
                    revalidate=Mock(),
                    push=push,
                )

                push.assert_called_once_with(
                    title="[주요 경제 소식] 미국 경제지표 발표",
                    body="미국에서 새로운 경제지표가 발표됐습니다.",
                    url="/live",
                    categories=("breaking_news",),
                )

    def test_urgent_news_targets_realtime_and_important_subscribers(self):
        revalidate = Mock()
        for score in (9, 10):
            with self.subTest(score=score):
                push = Mock()
                revalidate.reset_mock()

                publish_breaking_news(
                    selected(article(), score=score),
                    revalidate=revalidate,
                    push=push,
                )

                self.assertEqual(
                    revalidate.call_args_list,
                    [call("/live"), call("/")],
                )
                push.assert_called_once_with(
                    title="🚨[긴급 속보] 미국 경제지표 발표",
                    body="미국에서 새로운 경제지표가 발표됐습니다.",
                    url="/live",
                    categories=("breaking_news", "important_breaking_news"),
                )


class SchedulerAndQuotaTests(unittest.TestCase):
    def test_five_minute_schedule_waits_only_for_remaining_time(self):
        self.assertEqual(next_cycle_delay(100, 220, interval_seconds=300), 180)
        self.assertEqual(next_cycle_delay(100, 401, interval_seconds=300), 0)

    def test_daily_budget_blocks_request_951_and_resets_on_next_utc_day(self):
        current = [datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc)]
        budget = DailyRequestBudget(limit=950, clock=lambda: current[0])

        for _ in range(950):
            budget.consume()
        with self.assertRaisesRegex(RuntimeError, "950"):
            budget.consume()

        current[0] += timedelta(minutes=2)
        budget.consume()
        self.assertEqual(budget.used, 1)


if __name__ == "__main__":
    unittest.main()
