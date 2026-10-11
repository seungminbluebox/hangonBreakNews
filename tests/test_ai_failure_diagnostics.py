"""Offline transport/contract diagnostics; fixture values are synthetic."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llm_helper import AIRequestError, _response_diagnostics, safe_generate_content
from news_pipeline import run_two_stage_pipeline
from openrouter_budget import OpenRouterBudget


class AiFailureDiagnosticsTests(unittest.TestCase):
    def test_projection_omits_invalid_token_limits(self):
        for invalid in ("SYNTHETIC_PRIVATE_TOKEN_LIMIT", True, -1, 10_000_001):
            with self.subTest(value=invalid):
                fields = _response_diagnostics({}, "openrouter/free", invalid)
                self.assertIsNone(fields["max_tokens"])

    def call_helper(self, envelope, *, opt_in=True):
        response = SimpleNamespace(
            status_code=200, text="PRIVATE_PROVIDER_BODY", headers={},
            raise_for_status=lambda: None, json=lambda: envelope,
        )
        with tempfile.TemporaryDirectory() as directory:
            budget = OpenRouterBudget(path=os.path.join(directory, "budget.sqlite3"), limit=990)
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "SYNTHETIC_KEY"}), patch(
                "llm_helper.requests.post", return_value=response
            ), self.assertLogs("hangon.cycle", level="INFO") as logs:
                try:
                    result = safe_generate_content(
                        "PRIVATE_PROMPT", max_retries=1, max_tokens=8192,
                        request_budget=budget, raise_on_failure=opt_in,
                    )
                    failure = None
                except AIRequestError as error:
                    result, failure = None, error.reason
            return result, failure, "\n".join(logs.output), budget.snapshot()["used"]

    def test_truncation_reports_lengths_and_token_counts_without_response_text(self):
        _, reason, logs, used = self.call_helper({
            "model": "nvidia/synthetic:free",
            "choices": [{"finish_reason": "length", "message": {
                "content": "", "reasoning": "PRIVATE_REASONING",
            }}],
            "usage": {"completion_tokens": 8192,
                      "completion_tokens_details": {"reasoning_tokens": 8192}},
        })
        self.assertEqual(reason, "output_truncated")
        self.assertEqual(used, 1)
        for field in ("event=ai_response_metadata", "finish_reason=length", "output_chars=0",
                      "max_tokens=8192", "completion_tokens=8192", "reasoning_tokens=8192",
                      "response_model=nvidia/synthetic:free"):
            self.assertIn(field, logs)
        for private in ("PRIVATE_REASONING", "PRIVATE_PROMPT", "PRIVATE_PROVIDER_BODY", "SYNTHETIC_KEY"):
            self.assertNotIn(private, logs)

    def test_transport_schema_error_identifies_envelope_boundary(self):
        _, reason, logs, used = self.call_helper({"choices": [], "model": "provider/synthetic"})
        self.assertEqual(reason, "schema_error")
        self.assertEqual(used, 1)
        self.assertIn("event=ai_response_failure", logs)
        self.assertIn("detail=invalid_choices", logs)

    def test_untrusted_metadata_is_not_logged_and_invalid_counts_are_omitted(self):
        result, failure, logs, _ = self.call_helper({
            "model": "PRIVATE_MODEL\nAuthorization: PRIVATE_KEY",
            "choices": [{"finish_reason": "PRIVATE_FINISH", "message": {"content": "[]"}}],
            "usage": {"completion_tokens": "PRIVATE_COUNT",
                      "completion_tokens_details": {"reasoning_tokens": True}},
        })
        self.assertEqual(result.text, "[]")
        self.assertIsNone(failure)
        self.assertIn("response_model=unknown", logs)
        self.assertIn("finish_reason=unknown", logs)
        self.assertNotIn("completion_tokens=", logs)
        self.assertNotIn("reasoning_tokens=", logs)
        self.assertNotIn("PRIVATE_", logs)

    def test_legacy_default_does_not_add_response_metadata_logging(self):
        _, reason, logs, _ = self.call_helper({"choices": []}, opt_in=False)
        self.assertIsNone(reason)
        self.assertNotIn("event=ai_response_metadata", logs)
        self.assertNotIn("event=ai_response_failure", logs)

    def test_selection_schema_error_is_separate_from_transport_schema(self):
        article = {"provider_article_id": "article-0", "original_url": "https://example.com/0",
                   "raw_title": "Synthetic factory expansion", "published_at": "2026-10-11T10:00:00Z"}
        invalid = {"temp_id": 0, "source_ref": "wrong-ref", "importance_score": 8,
                   "category": "corporate", "news_type": "new_development", "selection_reason": "확장"}
        replies = iter([json.dumps([invalid]), json.dumps([invalid])])
        with self.assertLogs("hangon.cycle", level="INFO") as capture:
            result = run_two_stage_pipeline([article], lambda prompt: SimpleNamespace(text=next(replies)))
        logs = "\n".join(capture.output)
        self.assertEqual(result.selected, [])
        self.assertEqual(result.unevaluated_urls, {article["original_url"]})
        self.assertIn("event=ai_contract_failed", logs)
        self.assertIn("detail=selection_fields", logs)
        self.assertNotIn("wrong-ref", logs)

    def test_non_array_contract_is_not_confused_with_envelope_failure(self):
        article = {"provider_article_id": "article-0", "original_url": "https://example.com/0"}
        with self.assertLogs("hangon.cycle", level="INFO") as capture:
            result = run_two_stage_pipeline([article], lambda prompt: SimpleNamespace(text="{}"))
        self.assertEqual(result.selected, [])
        self.assertIn("detail=non_array", "\n".join(capture.output))


if __name__ == "__main__":
    unittest.main()
