from datetime import datetime, timezone
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gnews_adapter import ScheduledHeadlineCollector
from gnews_tracker import main, run_dry_run, run_production


class GNewsDryRunTests(unittest.TestCase):
    def test_prints_safe_preview_without_exposing_api_key(self):
        fetched_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
        article = {
            "provider": "gnews",
            "provider_article_id": "us-article-1",
            "source_id": "example.com",
            "source_name": "Example News",
            "source_tier": "unrated",
            "source_url": "https://example.com",
            "source_country": "us",
            "original_url": "https://example.com/economy/story",
            "published_at": "2026-08-03T01:00:00+00:00",
            "updated_at": None,
            "fetched_at": "2026-08-03T02:00:00+00:00",
            "original_timezone": "UTC",
            "original_language": "en",
            "market_scope": "us",
            "raw_title": "US economic headline — market update",
            "raw_description": "A short description of the economic event.",
            "raw_content": "A longer article body supplied by the API.",
            "normalized_title": None,
            "normalized_content": None,
            "image_url": None,
        }
        selected_article = {
            **article,
            "normalized_title": "미국 경제 새 소식 📊",
            "normalized_content": "새롭게 확인된 경제 사실을 발표했다.",
            "importance_score": 6,
            "category": "indicator",
            "news_type": "official_announcement",
            "selection_reason": "새로운 경제 수치가 공식 발표됨",
        }
        client = object()
        generator = Mock()
        client_factory = Mock(return_value=client)
        collector = Mock(return_value=[article])
        selector = Mock(return_value=[selected_article])
        sleeper = Mock()
        outputs = []

        articles = run_dry_run(
            "private-test-key",
            generator,
            client_factory=client_factory,
            collector=collector,
            selector=selector,
            clock=lambda: fetched_at,
            sleeper=sleeper,
            output=outputs.append,
        )

        self.assertEqual(articles, [selected_article])
        client_factory.assert_called_once_with(api_key="private-test-key")
        collector.assert_called_once_with(
            client,
            fetched_at=fetched_at,
            sleeper=sleeper,
        )
        selector.assert_called_once_with([article], generator)
        self.assertEqual(
            json.loads(outputs[-1]),
            [
                {
                    "market_scope": "us",
                    "source_name": "Example News",
                    "published_at": "2026-08-03T01:00:00+00:00",
                    "title": "미국 경제 새 소식 📊",
                    "content": "새롭게 확인된 경제 사실을 발표했다.",
                    "importance_score": 6,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "새로운 경제 수치가 공식 발표됨",
                    "raw_title": "US economic headline — market update",
                    "original_url": "https://example.com/economy/story",
                }
            ],
        )
        self.assertIn("fetching", outputs[0])
        self.assertIn("fetched=1", outputs[1])
        self.assertIn("미국 경제 새 소식", outputs[-1])
        all_output = "\n".join(outputs)
        self.assertNotIn("private-test-key", all_output)
        self.assertNotIn(article["raw_content"], all_output)
        outputs[-1].encode("utf-8")

    def test_dry_run_keeps_later_batch_results_when_one_ai_batch_fails(self):
        base_article = {
            "provider": "gnews",
            "source_id": "example.com",
            "source_name": "Example News",
            "source_tier": "unrated",
            "source_url": "https://example.com",
            "source_country": "us",
            "published_at": "2026-08-03T01:00:00+00:00",
            "updated_at": None,
            "fetched_at": "2026-08-03T02:00:00+00:00",
            "original_timezone": "UTC",
            "original_language": "en",
            "market_scope": "us",
            "raw_description": "A new economic development.",
            "raw_content": "The source article content.",
            "normalized_title": None,
            "normalized_content": None,
            "image_url": None,
        }
        collected = [
            {
                **base_article,
                "provider_article_id": f"article-{index}",
                "original_url": f"https://example.com/article-{index}",
                "raw_title": f"Economic headline {index}",
            }
            for index in range(11)
        ]
        selected_article = {
            **collected[10],
            "normalized_title": "경제 정책 발표",
            "normalized_content": "새로운 경제 정책이 발표됐습니다.",
            "importance_score": 8,
            "category": "indicator",
            "news_type": "official_announcement",
            "selection_reason": "새 정책이 공식 발표됨",
        }
        selector = Mock(
            side_effect=[
                ValueError("unsafe first batch"),
                [selected_article],
            ]
        )
        outputs = []

        result = run_dry_run(
            "private-test-key",
            Mock(),
            client_factory=Mock(return_value=object()),
            collector=Mock(return_value=collected),
            selector=selector,
            clock=lambda: datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
            sleeper=Mock(),
            output=outputs.append,
        )

        self.assertEqual(result, [selected_article])
        self.assertEqual(selector.call_count, 2)
        self.assertEqual(len(selector.call_args_list[0].args[0]), 10)
        self.assertEqual(len(selector.call_args_list[1].args[0]), 1)
        self.assertIn("batch 1/2 failed", "\n".join(outputs))
        self.assertEqual(json.loads(outputs[-1])[0]["title"], "경제 정책 발표")

    def test_main_reuses_openrouter_generator_and_runs_once(self):
        generator = Mock()
        selected = [{"provider_article_id": "article-1"}]
        runner = Mock(return_value=selected)
        environment = {
            "GNEWS_API_KEY": "gnews-test-key",
            "OPENROUTER_API_KEY": "openrouter-test-key",
        }

        result = main(
            environment=environment,
            generator=generator,
            runner=runner,
        )

        self.assertEqual(result, selected)
        runner.assert_called_once_with("gnews-test-key", generator)

    def test_main_stops_before_network_calls_when_a_required_key_is_missing(self):
        for environment, missing_name in (
            ({"OPENROUTER_API_KEY": "openrouter-key"}, "GNEWS_API_KEY"),
            ({"GNEWS_API_KEY": "gnews-key"}, "OPENROUTER_API_KEY"),
        ):
            with self.subTest(missing_name=missing_name):
                with self.assertRaisesRegex(SystemExit, missing_name):
                    main(
                        environment=environment,
                        generator=Mock(),
                        runner=Mock(),
                    )

    def test_main_uses_free_auto_router_with_structured_output_by_default(self):
        runner = Mock(return_value=[])
        environment = {
            "GNEWS_API_KEY": "gnews-test-key",
            "OPENROUTER_API_KEY": "openrouter-test-key",
        }

        with patch("llm_helper.safe_generate_content") as safe_generate_content:
            main(environment=environment, runner=runner)

        generator = runner.call_args.args[1]
        self.assertIs(generator.func, safe_generate_content)
        self.assertEqual(generator.keywords["max_retries"], 3)
        self.assertEqual(generator.keywords["request_timeout"], 60)
        self.assertEqual(
            generator.keywords["model_name"],
            "openrouter/free",
        )
        self.assertEqual(
            generator.keywords["backup_model_name"],
            "openrouter/free",
        )
        self.assertEqual(
            generator.keywords["response_format"]["type"],
            "json_schema",
        )
        self.assertEqual(
            generator.keywords["provider_preferences"],
            {"require_parameters": True, "allow_fallbacks": True},
        )

    def test_main_runs_production_by_default_with_existing_database_config(self):
        production_runner = Mock(return_value="stopped")
        generator = Mock()
        environment = {
            "GNEWS_API_KEY": "gnews-test-key",
            "OPENROUTER_API_KEY": "openrouter-test-key",
            "SUPABASE_URL": "https://database.example",
            "SUPABASE_KEY": "supabase-test-key",
        }

        result = main(
            environment=environment,
            generator=generator,
            production_runner=production_runner,
        )

        self.assertEqual(result, "stopped")
        production_runner.assert_called_once_with(
            "gnews-test-key",
            generator,
            environment=environment,
        )

    def test_dry_run_never_requires_database_configuration(self):
        dry_run_runner = Mock(return_value=[])
        generator = Mock()
        environment = {
            "GNEWS_API_KEY": "gnews-test-key",
            "OPENROUTER_API_KEY": "openrouter-test-key",
        }

        result = main(
            environment=environment,
            generator=generator,
            dry_run=True,
            dry_run_runner=dry_run_runner,
        )

        self.assertEqual(result, [])
        dry_run_runner.assert_called_once_with("gnews-test-key", generator)


class GNewsProductionTests(unittest.TestCase):
    def test_uses_essential_plan_collection_schedule(self):
        fake_supabase = SimpleNamespace(create_client=Mock(return_value=object()))
        fake_push = SimpleNamespace(send_push_notification=Mock())
        fake_revalidate = SimpleNamespace(revalidate_path=Mock())
        environment = {
            "SUPABASE_URL": "https://database.example",
            "SUPABASE_KEY": "supabase-test-key",
        }

        with patch.dict(
            sys.modules,
            {
                "supabase": fake_supabase,
                "push_notification": fake_push,
                "revalidate": fake_revalidate,
            },
        ), patch("gnews_tracker.run_forever", return_value="stopped") as run_forever:
            result = run_production(
                "gnews-test-key",
                Mock(),
                environment=environment,
                output=Mock(),
            )

        self.assertEqual(result, "stopped")
        production_cycle = run_forever.call_args.args[0]
        self.assertIsInstance(
            production_cycle.keywords.get("collector"),
            ScheduledHeadlineCollector,
        )


if __name__ == "__main__":
    unittest.main()
