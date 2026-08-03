import unittest
from unittest.mock import Mock, patch

from llm_helper import safe_generate_content


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


if __name__ == "__main__":
    unittest.main()
