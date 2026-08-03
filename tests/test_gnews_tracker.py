from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, patch

from gnews_tracker import main, run_dry_run


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
            json.loads(outputs[0]),
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
        self.assertNotIn("private-test-key", outputs[0])
        outputs[0].encode("cp949")

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


if __name__ == "__main__":
    unittest.main()
