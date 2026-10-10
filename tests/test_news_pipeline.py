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
from news_pipeline import HEADLINE_RESPONSE_FORMAT, run_two_stage_pipeline
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
        "source_excerpt": f"The source reports a concrete economic event {index}.",
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

    def test_rejects_headline_that_ends_mid_clause(self):
        source = article(0)
        incomplete = summary(0)
        incomplete["title"] = "가기업 공장 확장을 위해"
        generator = FakeGenerator([
            json.dumps([selection(0)], ensure_ascii=False),
            json.dumps([incomplete], ensure_ascii=False),
        ])

        result = run_two_stage_pipeline([source], generator)

        self.assertEqual(result.selected, [])
        self.assertEqual(result.quality_failed, 1)
        self.assertIn(source["original_url"], result.unevaluated_urls)

    def test_generates_only_headline_and_keeps_body_empty(self):
        source = article(0)
        complete_content = (
            "가기업은 공급망 안정과 생산 능력 확대를 위해 수도권 공장 증설 계획을 "
            "확정했으며, 신규 설비 도입과 인력 충원을 거쳐 내년부터 생산량을 단계적으로 "
            "늘리고 주요 고객사 납품 일정도 안정적으로 운영할 예정이라고 밝혔습니다."
        )
        self.assertGreater(len(complete_content), 110)
        complete = summary(0)
        generator = FakeGenerator([
            json.dumps([selection(0)], ensure_ascii=False),
            json.dumps([complete], ensure_ascii=False),
        ])

        result = run_two_stage_pipeline([source], generator)
        content_schema = HEADLINE_RESPONSE_FORMAT["json_schema"]["schema"][
            "items"
        ]["properties"]

        self.assertNotIn("content", content_schema)
        self.assertEqual(result.selected[0]["normalized_content"], "")
        self.assertIn("Do not generate a body or summary", generator.prompts[1])
        self.assertIn(source["raw_content"], generator.prompts[1])

    def test_preserves_disaster_count_roles_and_rejects_affected_as_deaths(self):
        source = article(0)
        source.update(raw_title="Thailand floods affect 3,158,615 people; 53 dead",
                      raw_description="Floods affected 3,158,615 people and killed 53 people.",
                      raw_content="Officials report 3,158,615 people affected and 53 deaths.")
        for title, accepted in (
            ("태국 홍수, 3,158,615명 피해·53명 사망", True),
            ("태국 홍수, 315만8615명 피해·53명 사망", True),
            ("태국 홍수로 3,158,615명 사망", False),
            ("태국 홍수로 315만8615명 사망", False),
            ("태국 홍수, 사망자 3,158,615명", False),
            ("태국 홍수, 3,158,615명 피해·54명 사망", False),
            ("미국 홍수, 3,158,615명 피해·53명 사망", False),
        ):
            with self.subTest(title=title):
                generator = FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title, "source_excerpt": source["raw_content"]}], ensure_ascii=False),
                ])
                result = run_two_stage_pipeline([source], generator)
                self.assertEqual(bool(result.selected), accepted)

    def test_preserves_uncertainty_in_headline(self):
        source = article(0)
        source.update(raw_title="US may intervene in yen market",
                      raw_description="Possible US intervention is under consideration.",
                      raw_content="Officials are considering intervention; no decision has been made.")
        for title, accepted in (("미국, 엔화 시장 개입 검토", True),
                                ("미국, 엔화 시장 개입 확정", False)):
            with self.subTest(title=title):
                result = run_two_stage_pipeline([source], FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title, "source_excerpt": source["raw_content"]}], ensure_ascii=False),
                ]))
                self.assertEqual(bool(result.selected), accepted)

    def test_casualty_count_roles_cover_inflections_particles_and_unknown_phrases(self):
        source = article(0)
        source.update(raw_title="Thailand floods affect 3,158,615 people; 53 dead",
                      raw_description="Floods affected 3,158,615 people and killed 53 people.",
                      raw_content="Officials report 3,158,615 people affected and 53 deaths.")
        for title, accepted in (
            ("태국 홍수, 315만8615명 숨져", False),
            ("태국 홍수로3,158,615명이목숨을잃어", False),
            ("태국 홍수, 315만8615명 숨졌다", False),
            ("태국 홍수, 315만8615명이 생명을 잃었다", False),
            ("태국 홍수, 315만8615명 숨을 거둬", False),
            ("태국 홍수, 315만8615명 유명을 달리해", False),
            ("태국 홍수, 315만8615명은 사망", False),
            ("태국 홍수, 53명 다쳐", False),
            ("태국 홍수, 53명 긴급 대피", False),
            ("태국 홍수, 315만8615명 희생", False),
            ("태국 홍수, 315만8615명", False),
            ("태국 홍수, 315만8615명 피해·315만8615명 희생", False),
            ("태국 홍수, 사망자 3,158,615", False),
            ("태국 홍수로 3,158,615여 명 숨져", False),
            ("태국 홍수로 315만8615여명 숨져", False),
            ("태국 홍수, 사망자 거의 3,158,615", False),
            ("태국 홍수, 315만8615명 피해·53명 숨져", True),
            ("태국 홍수, 315만8615명 피해·53명이 목숨을 잃어", True),
            ("태국 홍수, 315만8615명 피해·53명은 사망", True),
            ("태국 홍수, 315만8615명은 피해·53명 숨져", True),
            ("태국 홍수, 사망자 53", True),
            ("태국 홍수, 53명의 사망자가 발생", True),
        ):
            with self.subTest(title=title):
                result = run_two_stage_pipeline([source], FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title,
                                 "source_excerpt": source["raw_content"]}], ensure_ascii=False),
                ]))
                self.assertEqual(bool(result.selected), accepted)

    def test_casualty_count_parser_does_not_treat_damage_amount_as_people(self):
        source = article(0)
        source.update(raw_title="Thailand floods: 53 deaths",
                      raw_description="Thailand floods caused 53 deaths.",
                      raw_content="Officials report 피해 2조 원 and 53 deaths.")
        result = run_two_stage_pipeline([source], FakeGenerator([
            json.dumps([selection(0)]),
            json.dumps([{**summary(0), "title": "태국 홍수 피해 2조 원·53명 사망",
                         "source_excerpt": source["raw_content"]}], ensure_ascii=False),
        ]))
        self.assertEqual(len(result.selected), 1)

    def test_preserves_reporting_period_and_numeric_direction(self):
        source = article(0)
        source.update(raw_title="US July unemployment rate rises to 4.3%",
                      raw_description="US July unemployment rate rises to 4.3%.",
                      raw_content="The US July unemployment rate increased to 4.3%.")
        for title, accepted in (("미국 7월 실업률 4.3%로 상승", True),
                                ("미국 7월 실업률 4.3%로 하락", False),
                                ("미국 6월 실업률 4.3%로 상승", False),
                                ("미국 실업률 4.3%로 상승", False)):
            with self.subTest(title=title):
                result = run_two_stage_pipeline([source], FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title, "source_excerpt": source["raw_content"]}], ensure_ascii=False),
                ]))
                self.assertEqual(bool(result.selected), accepted)

    def test_preserves_explicit_reporting_year_over_historical_body_comparison(self):
        source = article(0)
        source.update(raw_title="US July 2026 unemployment rate rises to 4.3%",
                      raw_description="US July 2026 unemployment rate rises to 4.3%.",
                      raw_content="US July 2026 unemployment rate increased to 4.3%; in July 2025 it was 4.1%.")
        for title, accepted in (
            ("미국 2026년 7월 실업률 4.3%로 상승", True),
            ("미국 2025년 7월 실업률 4.3%로 상승", False),
            ("미국 7월 실업률 4.3%로 상승", False),
        ):
            with self.subTest(title=title):
                result = run_two_stage_pipeline([source], FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title,
                                 "source_excerpt": source["raw_description"]}], ensure_ascii=False),
                ]))
                self.assertEqual(bool(result.selected), accepted)

    def test_preserves_annual_reporting_year_without_month_or_quarter(self):
        source = article(0)
        source.update(raw_title="US 2026 GDP growth announced at 3.4%",
                      raw_description="US 2026 GDP growth is 3.4%.",
                      raw_content="US 2026 GDP growth is 3.4%; growth in 2025 was 3.1%.")
        for title, accepted in (
            ("미국 2026년 GDP 성장률 3.4% 발표", True),
            ("미국 2025년 GDP 성장률 3.4% 발표", False),
            ("미국 GDP 성장률 3.4% 발표", False),
        ):
            with self.subTest(title=title):
                result = run_two_stage_pipeline([source], FakeGenerator([
                    json.dumps([selection(0)]),
                    json.dumps([{**summary(0), "title": title,
                                 "source_excerpt": source["raw_description"]}], ensure_ascii=False),
                ]))
                self.assertEqual(bool(result.selected), accepted)

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
            "news_headlines",
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


class GroundedPipelineTests(unittest.TestCase):
    """Synthetic fixtures exercise transport and validation, not live model quality."""
    def run_one(self, source, generated):
        generator = FakeGenerator([
            json.dumps([selection(0)], ensure_ascii=False),
            json.dumps(generated, ensure_ascii=False),
        ])
        return run_two_stage_pipeline([source], generator), generator

    def test_requires_literal_provider_evidence_not_generated_reason(self):
        source = article(0)
        generated = summary(0)
        generated["source_excerpt"] = "The invented source says something entirely different."
        result, generator = self.run_one(source, [generated])
        self.assertEqual(result.selected, [])
        self.assertEqual(result.quality_reasons, {"invalid_source_excerpt": 1})
        self.assertIn(source["original_url"], result.unevaluated_urls)
        self.assertEqual(len(generator.prompts), 2)

    def test_summary_prompt_contains_source_anchors_but_no_ai_selection_reason(self):
        source = article(0)
        source["raw_title"] = "Zambia approves copper mine expansion"
        result, generator = self.run_one(source, [])
        payload = json.loads(generator.prompts[1].split("ITEMS:\n", 1)[1])
        self.assertNotIn("selection_reason", payload[0])
        self.assertIn({"source": "Zambia", "korean_names": ["잠비아"]}, payload[0]["source_entities"])
        self.assertIn("source_excerpt", HEADLINE_RESPONSE_FORMAT["json_schema"]["schema"]["items"]["required"])

    def test_quality_failure_can_use_complete_korean_source_headline(self):
        source = article(0)
        source.update(raw_title="가기업 수도권 공장 증설 확정", raw_description="가기업이 수도권 공장 증설을 확정했습니다.", raw_content="가기업이 수도권 공장 증설을 확정했습니다.")
        generated = {**summary(0), "title": "가기업 공장을 증설하기 위해", "source_excerpt": source["raw_description"]}
        result, generator = self.run_one(source, [generated])
        self.assertEqual(result.selected[0]["normalized_title"], source["raw_title"])
        self.assertEqual(result.selected[0]["normalized_content"], "")
        self.assertEqual(result.source_fallbacks, 1)
        self.assertEqual(result.quality_failed, 0)
        self.assertEqual(len(generator.prompts), 2)

    def test_missing_summary_is_explicit_quality_failure_or_source_fallback(self):
        source = article(0)
        result, _ = self.run_one(source, [])
        self.assertEqual(result.quality_failed, 1)
        self.assertEqual(result.quality_reasons, {"headline_omitted": 1})
        source.update(raw_title="가기업 공장 증설 확정", raw_description="가기업이 공장 증설을 확정했습니다.")
        result, _ = self.run_one(source, [])
        self.assertEqual(result.source_fallbacks, 1)
        self.assertEqual(len(result.selected), 1)

    def test_truncated_body_is_not_published_when_complete_source_headline_is_available(self):
        source = article(0)
        source.update(raw_title="가기업 공장 증설 확정", raw_description="가기업이 공장 증설을 확정하고", raw_content="가기업이 공장 증설을 확정하고 [123 chars]")
        result, _ = self.run_one(source, [])
        self.assertEqual(result.selected[0]["normalized_title"], source["raw_title"])
        self.assertEqual(result.selected[0]["normalized_content"], "")
        self.assertEqual(result.source_fallbacks, 1)

    def test_duplicate_summary_ids_fail_contract_without_publishing(self):
        result, _ = self.run_one(article(0), [summary(0), summary(0)])
        self.assertEqual(result.selected, [])
        self.assertIn(article(0)["original_url"], result.unevaluated_urls)


class KoreanSourceFallbackTests(unittest.TestCase):
    def test_preserves_complete_source_headline_without_quoting_body(self):
        source = article(0)
        source.update(raw_title="테슬라코리아 FSD 안전 검증 협조 계획", raw_description="테슬라코리아가 국내 FSD 안전성 검증에 협조하기로 했다.", raw_content="")
        generator = FakeGenerator([json.dumps([selection(0)]), "[]"])
        result = run_two_stage_pipeline([source], generator)
        self.assertEqual(len(result.selected), 1)
        self.assertEqual(result.selected[0]["normalized_title"], source["raw_title"])
        self.assertEqual(result.selected[0]["normalized_content"], "")
        self.assertEqual(result.source_fallbacks, 1)

    def test_does_not_publish_malformed_source_body(self):
        source = article(0)
        source.update(raw_title="가기업 공장 증설 확정", raw_description="가기업이 공장 증설을 확정했다.입니다.", raw_content="")
        result = run_two_stage_pipeline([source], FakeGenerator([json.dumps([selection(0)]), "[]"]))
        self.assertEqual(result.selected[0]["normalized_content"], "")
        self.assertEqual(result.source_fallbacks, 1)


class ReviewedEvidenceBoundaryTests(unittest.TestCase):
    def test_truncation_marker_is_not_substantive_source_evidence(self):
        source = article(0)
        source["raw_content"] = "The factory is expanding. [+12345 chars]"
        generated = {**summary(0), "source_excerpt": "[+12345 chars]"}
        result = run_two_stage_pipeline([source], FakeGenerator([json.dumps([selection(0)]), json.dumps([generated])]))
        self.assertEqual(result.selected, [])
        self.assertEqual(result.quality_reasons, {"invalid_source_excerpt": 1})

    def test_rss_short_text_sentinel_is_not_substantive_source_evidence(self):
        from news_pipeline import validate_legacy_headline
        result = validate_legacy_headline({
            "title": "테슬라 공장 증설", "content": "테슬라가 공장 증설을 발표했습니다.",
            "source_excerpt": "TEXT_TOO_SHORT", "importance_score": 8, "category": "corporate",
        }, {"id": 0, "title": "Tesla expands factory", "content_to_analyze": "TEXT_TOO_SHORT", "original_url": "https://example.com/a"})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
