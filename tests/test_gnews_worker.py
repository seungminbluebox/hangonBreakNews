from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call

from gnews_tracker import (
    DailyRequestBudget,
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
    def __init__(self, existing_urls=(), failures=0):
        self.existing = set(existing_urls)
        self.failures = failures
        self.saved = []

    def existing_urls(self, urls):
        return self.existing.intersection(urls)

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

    def test_revalidates_existing_pages_and_uses_existing_push_categories(self):
        revalidate = Mock()
        push = Mock()

        publish_breaking_news(
            selected(article(), score=9),
            revalidate=revalidate,
            push=push,
        )

        self.assertEqual(revalidate.call_args_list, [call("/live"), call("/")])
        push.assert_called_once_with(
            title="🚨[초긴급] 미국 경제지표 발표",
            body="미국에서 새로운 경제지표가 발표됐습니다.",
            url="/live",
            category="important_breaking_news",
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
