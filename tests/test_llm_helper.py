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



class GeminiGenerateContentTests(unittest.TestCase):
    FORMAT = {"type": "json_schema", "json_schema": {"name": "news_selection", "strict": True,
                                                     "schema": {"type": "array"}}}

    @patch("llm_helper.requests.post")
    def test_sends_the_schema_to_ai_studio_and_returns_the_json_text(self, post):
        from llm_helper import GEMINI_CHAT_URL, gemini_generate_content

        post.return_value = Mock(status_code=200, json=Mock(return_value={
            "model": "gemini-3.1-flash-lite",
            "choices": [{"finish_reason": "stop", "message": {"content": "```json\n[]\n```"}}],
        }))
        result = gemini_generate_content("select news", model_name="gemini-3.1-flash-lite",
                                         api_key="gemini-test-key", response_format=self.FORMAT,
                                         request_timeout=60, max_tokens=8192)

        self.assertEqual(result.text, "[]")
        self.assertEqual(post.call_args.args[0], GEMINI_CHAT_URL)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gemini-3.1-flash-lite")
        self.assertEqual(body["response_format"], self.FORMAT)
        self.assertEqual(body["max_tokens"], 8192)
        self.assertNotIn("provider", body)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer gemini-test-key")
        self.assertNotIn("gemini-test-key", str(body))

    @patch("llm_helper.requests.post")
    def test_quota_errors_timeouts_and_truncation_raise_typed_failures(self, post):
        from llm_helper import gemini_generate_content

        cases = (
            (Mock(status_code=429), ("http_error", 429)),
            (requests.exceptions.Timeout(), ("timeout", None)),
            (Mock(status_code=200, json=Mock(return_value={
                "choices": [{"finish_reason": "length", "message": {"content": "[{"}}]})),
             ("output_truncated", 200)),
        )
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                post.reset_mock(side_effect=True, return_value=True)
                if isinstance(outcome, Exception):
                    post.side_effect = outcome
                else:
                    post.return_value = outcome
                with self.assertRaises(AIRequestError) as raised:
                    gemini_generate_content("select news", model_name="gemini-3.1-flash-lite",
                                            api_key="gemini-test-key")
                self.assertEqual((raised.exception.reason, raised.exception.status_code), expected)


if __name__ == "__main__":
    unittest.main()
