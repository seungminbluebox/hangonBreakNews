from datetime import datetime, timezone, timedelta
import io
import json
import logging
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gnews_tracker import GNewsStageGenerator
from news_pipeline import run_two_stage_pipeline
from cycle_logging import cycle_scope


def article(index, *, published_at=None):
    published_at = published_at or datetime(
        2026, 9, 13, 12, 0, tzinfo=timezone.utc
    ).isoformat()
    return {
        "provider_article_id": f"article-{index}",
        "original_url": f"https://example.com/{index}",
        "raw_title": f"Economic event {index}",
        "raw_description": f"A concrete new economic event {index}.",
        "raw_content": f"The source reports a concrete economic event {index}.",
        "published_at": published_at,
        "source_name": "Example News",
        "source_id": "example.com",
        "market_scope": "us",
    }


class FakeGenerator:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, SimpleNamespace):
            return response
        return SimpleNamespace(text=response)


def selection(index, score=8):
    return {
        "temp_id": index,
        "source_ref": f"article-{index}",
        "importance_score": score,
        "category": "corporate",
        "news_type": "new_development",
        "selection_reason": "구체적인 새 경제 사건이 확인됐습니다.",
    }


def summary(index):
    labels = ("가", "나", "다", "라", "마", "바", "사", "아", "자", "차", "카")
    events = (
        "공장 확장", "합병 계약", "실적 발표", "수출 관세", "고용 계획",
        "제품 리콜", "채권 발행", "항만 폐쇄", "세금 판결", "원유 선적", "은행 파산",
    )
    details = (
        "수도권 생산시설 증설을 확정했습니다.",
        "경쟁사 인수 계약을 체결했습니다.",
        "분기 영업이익을 공시했습니다.",
        "해외 판매품의 관세율을 변경했습니다.",
        "연말 신규 채용 일정을 공개했습니다.",
        "결함 제품의 회수 계획을 발표했습니다.",
        "장기 회사채 발행 계획을 확정했습니다.",
        "주요 항만의 운영 중단을 공지했습니다.",
        "법원의 세금 관련 판결을 받았습니다.",
        "중동산 원유의 선적 계약을 맺었습니다.",
        "법원에 회생 절차를 신청했습니다.",
    )
    return {
        "temp_id": index,
        "source_ref": f"article-{index}",
        "title": f"{labels[index]}기업 {events[index]} 발표",
        "content": f"{labels[index]}기업이 {details[index]}",
    }


class TwoStagePipelineTests(unittest.TestCase):
    def test_runs_selection_then_top_ten_summary_and_keeps_metadata_in_code(self):
        articles = [article(index) for index in range(11)]
        source_labels = (
            "Alpha factory expansion", "Beta merger agreement", "Gamma earnings release",
            "Delta export tariff", "Epsilon hiring plan", "Zeta product recall",
            "Eta bond issuance", "Theta port closure", "Iota tax ruling",
            "Kappa oil shipment", "Lambda bank failure",
        )
        for index, item in enumerate(articles):
            item["raw_title"] = source_labels[index]
            item["raw_description"] = f"{source_labels[index]} announced a new economic change."
        scores = [10, 10, 10, 10, 9, 9, 9, 9, 8, 8, 7]
        generator = FakeGenerator(
            [
                json.dumps([selection(index, score=scores[index]) for index in range(11)]),
                json.dumps([summary(index) for index in (0, 1, 4)]),
            ]
        )

        result = run_two_stage_pipeline(articles, generator)

        self.assertEqual(len(result.selected), 3)
        self.assertIn('"source_ref": "article-0"', generator.prompts[1])
        self.assertNotIn('"source_ref": "article-10"', generator.prompts[1])
        self.assertEqual(result.cut_urls, set())
        self.assertNotIn("https://example.com/10", result.evaluated_urls)
        self.assertNotIn("normalized_title", generator.prompts[0])
        self.assertNotIn("https://example.com/0", generator.prompts[0])

    def test_uses_one_common_retry_and_stops_after_three_total_attempts(self):
        source = article(0)
        valid_selection = json.dumps([selection(0)])
        valid_summary = json.dumps([summary(0)])

        generator = FakeGenerator(["invalid", valid_selection, valid_summary])
        result = run_two_stage_pipeline([source], generator)

        self.assertEqual(len(result.selected), 1)
        self.assertEqual(len(generator.prompts), 3)

        generator = FakeGenerator([valid_selection, "invalid", "still invalid"])
        result = run_two_stage_pipeline([source], generator)
        self.assertEqual(result.selected, [])
        self.assertEqual(result.unevaluated_urls, {source["original_url"]})
        self.assertEqual(len(generator.prompts), 3)

    def test_zero_candidates_makes_no_ai_request(self):
        generator = FakeGenerator([])
        result = run_two_stage_pipeline([], generator)

        self.assertEqual(result.selected, [])
        self.assertEqual(generator.prompts, [])

    def test_truncated_response_uses_the_single_shared_retry(self):
        source = article(0)
        generator = FakeGenerator(
            [
                SimpleNamespace(text="[]", finish_reason="length"),
                json.dumps([selection(0)]),
                json.dumps([summary(0)]),
            ]
        )

        result = run_two_stage_pipeline([source], generator)

        self.assertEqual(len(result.selected), 1)
        self.assertEqual(len(generator.prompts), 3)

    def test_summary_identity_mismatch_logs_schema_error_and_keeps_unresolved(self):
        source = article(0)
        invalid_summary = {
            **summary(0),
            "source_ref": "wrong-provider-id",
        }
        generator = FakeGenerator([
            json.dumps([selection(0)]),
            json.dumps([invalid_summary]),
        ])
        import cycle_logging
        logger = logging.getLogger("hangon.cycle")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(cycle_logging.sys, "stdout", stdout), patch.object(
            cycle_logging.sys, "stderr", stderr
        ):
            with cycle_scope(cycle_id="identity-mismatch", slow_seconds=60):
                result = run_two_stage_pipeline([source], generator)

        self.assertEqual(result.selected, [])
        self.assertIn(source["original_url"], result.unevaluated_urls)
        self.assertIn("event=cycle_summary", stdout.getvalue())
        self.assertIn("event=ai_stage_failed", stderr.getvalue())
        self.assertIn("reason=schema_error", stderr.getvalue())

    @patch("llm_helper.requests.post")
    def test_real_helper_posts_once_per_stage_with_stage_schema_and_tokens(self, post):
        responses = []
        for payload in ([selection(0)], [summary(0)]):
            response = Mock(status_code=200, headers={})
            response.raise_for_status.return_value = None
            response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}]}
            response.text = json.dumps(payload)
            responses.append(response)
        post.side_effect = responses
        source = article(0)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_BUDGET_PATH": os.path.join(directory, "budget.sqlite3"),
            },
        ):
            generator = GNewsStageGenerator(
                model_name="openrouter/free",
                backup_model_name="openrouter/free",
                selection_max_tokens=8192,
                summary_max_tokens=8192,
            )
            result = run_two_stage_pipeline([source], generator)

        self.assertEqual(len(result.selected), 1)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["max_tokens"], 8192)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 8192)
        self.assertEqual(
            post.call_args_list[0].kwargs["json"]["response_format"]["json_schema"]["name"],
            "news_selection_shortlist",
        )
        self.assertEqual(
            post.call_args_list[1].kwargs["json"]["response_format"]["json_schema"]["name"],
            "news_summary",
        )

    @patch("llm_helper.requests.post")
    def test_real_helper_shares_one_retry_across_stages(self, post):
        responses = []
        for payload, finish_reason in (
            ([], "length"),
            ([selection(0)], "stop"),
            ([summary(0)], "stop"),
        ):
            response = Mock(status_code=200, headers={})
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "choices": [{
                    "message": {"content": json.dumps(payload)},
                    "finish_reason": finish_reason,
                }]
            }
            response.text = json.dumps(payload)
            responses.append(response)
        post.side_effect = responses
        source = article(0)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_BUDGET_PATH": os.path.join(directory, "budget.sqlite3"),
            },
        ):
            generator = GNewsStageGenerator(
                model_name="openrouter/free",
                backup_model_name="openrouter/free",
                selection_max_tokens=8192,
                summary_max_tokens=8192,
            )
            result = run_two_stage_pipeline([source], generator)

        self.assertEqual(len(result.selected), 1)
        self.assertEqual(post.call_count, 3)
        self.assertTrue(all(
            call.kwargs["json"]["max_tokens"] == 8192
            for call in post.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
