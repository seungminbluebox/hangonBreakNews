import os
import tempfile
import unittest
import requests
from unittest.mock import Mock, patch

from llm_helper import AIRequestError, safe_generate_content
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

    @patch("llm_helper.time.sleep")
    @patch("llm_helper.requests.post")
    def test_gnews_opt_in_reports_fixed_failure_reasons_and_caps_retries(self, post, sleep):
        def response(*, body, status=200, finish_reason="stop"):
            item = Mock(status_code=status, text=body, headers={})
            item.raise_for_status.return_value = None
            if body == "not-json":
                item.json.side_effect = ValueError("secret-json")
            else:
                item.json.return_value = body
            item._finish_reason = finish_reason
            return item

        cases = [
            ("timeout", lambda: requests.Timeout("secret-timeout")),
            ("network_error", lambda: requests.ConnectionError("secret-network")),
            ("http_error", lambda: requests.HTTPError(response=response(body={}, status=503))),
            ("empty_response", lambda: response(body={"choices": [{"message": {"content": ""}}]})),
            ("invalid_json", lambda: response(body="not-json")),
            ("output_truncated", lambda: response(body={"choices": [{"finish_reason": "length", "message": {"content": "[]"}}]})),
            ("schema_error", lambda: response(body={"choices": []})),
            ("unexpected_error", lambda: RuntimeError("secret-unexpected")),
        ]

        for reason, make_failure in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                item = make_failure()
                if isinstance(item, Exception):
                    post.side_effect = [item, item]
                else:
                    post.side_effect = [item, item]
                budget = OpenRouterBudget(
                    path=os.path.join(directory, "budget.sqlite3"), limit=990
                )
                with patch.dict(
                    "os.environ",
                    {"OPENROUTER_API_KEY": "test-key"},
                    clear=False,
                ):
                    with self.assertRaises(AIRequestError) as raised:
                        safe_generate_content(
                            "select news",
                            max_retries=2,
                            request_budget=budget,
                            raise_on_failure=True,
                        )
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(post.call_count, 2)
                post.reset_mock()
                sleep.reset_mock()

    @patch("llm_helper.requests.post")
    def test_default_failure_contract_still_returns_none(self, post):
        response = Mock(status_code=200, text="", headers={})
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": ""}}]}
        post.return_value = response
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            result = safe_generate_content(
                "select news", max_retries=1, request_budget=Mock()
            )
        self.assertIsNone(result)



if __name__ == "__main__":
    unittest.main()
