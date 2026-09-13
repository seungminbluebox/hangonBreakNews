import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from llm_helper import safe_generate_content
from openrouter_budget import OpenRouterRequestBlocked
from openrouter_budget import OpenRouterBudget


class LlmHelperTests(unittest.TestCase):
    @patch("llm_helper.requests.post")
    def test_supports_model_override_and_structured_response_format(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}}]
        }
        post.return_value = response
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "news_selection",
                "strict": True,
                "schema": {"type": "array", "items": {"type": "object"}},
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            request_budget = OpenRouterBudget(
                path=os.path.join(directory, "budget.sqlite3"),
                limit=900,
            )
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
                result = safe_generate_content(
                    "select news",
                    max_retries=1,
                    model_name="google/gemma-test:free",
                    backup_model_name="nvidia/backup-test:free",
                    response_format=response_format,
                    provider_preferences={
                        "require_parameters": True,
                        "allow_fallbacks": True,
                    },
                    request_timeout=30,
                    request_budget=request_budget,
                )

        self.assertEqual(result.text, "[]")
        request = post.call_args
        self.assertEqual(request.kwargs["timeout"], 30)
        self.assertEqual(request.kwargs["json"]["model"], "google/gemma-test:free")
        self.assertEqual(request.kwargs["json"]["response_format"], response_format)
        self.assertEqual(
            request.kwargs["json"]["provider"],
            {"require_parameters": True, "allow_fallbacks": True},
        )
        self.assertNotIn("test-key", request.kwargs["json"])

    @patch("llm_helper.requests.post")
    def test_reserves_each_free_http_attempt(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "[]"}}]}
        post.return_value = response
        budget = Mock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            safe_generate_content("select news", max_retries=2, request_budget=budget)

        budget.reserve.assert_called_once_with("openrouter/free")

    @patch("llm_helper.requests.post")
    def test_daily_429_stops_retry_and_persists_breaker(self, post):
        response = Mock(status_code=429, text='{"error":"free-models-per-day"}')
        response.headers = {}
        response.raise_for_status.side_effect = Exception("429")
        post.return_value = response
        budget = Mock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            with self.assertRaises(OpenRouterRequestBlocked):
                safe_generate_content("select news", max_retries=3, request_budget=budget)

        post.assert_called_once()
        budget.record_rate_limit.assert_called_once()

    @patch("llm_helper.time.sleep")
    @patch("llm_helper.requests.post")
    def test_paid_model_429_retries_without_free_breaker(self, post, sleep):
        import requests

        response = Mock(status_code=429, text="rate limited", headers={})
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        post.return_value = response
        budget = Mock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            result = safe_generate_content(
                "select news",
                max_retries=2,
                model_name="google/gemini-2.5-flash",
                backup_model_name="google/gemini-2.5-flash",
                request_budget=budget,
            )

        self.assertIsNone(result)
        self.assertEqual(post.call_count, 2)
        budget.record_rate_limit.assert_not_called()



if __name__ == "__main__":
    unittest.main()
