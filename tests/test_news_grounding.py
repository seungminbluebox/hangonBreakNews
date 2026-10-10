"""Offline regression fixtures. These are SYNTHETIC, not captured provider rows."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import sys
import unittest

from news_selector import _decision_to_item, _is_valid_report_summary


def source(title, description):
    return {
        "provider_article_id": "grounded-1", "original_url": "https://example.com/grounded-1",
        "raw_title": title, "raw_description": description, "raw_content": description,
        "published_at": "2026-10-08T10:00:00+00:00", "source_name": "Synthetic News",
    }


def decision(article, title, content):
    return {
        "temp_id": 0, "source_ref": article["provider_article_id"],
        "source_title": article["raw_title"], "title": title, "content": content,
        "importance_score": 8, "category": "corporate", "news_type": "new_development",
        "selection_reason": "새로운 경제 관련 사실입니다.",
    }


class SentenceIntegrityTests(unittest.TestCase):
    def test_rejects_doubled_formal_endings_and_bare_ending_fragments(self):
        for text in ("회사가 공장을 증설했습니다.입니다.", "회사가 공장을 증설했습니다. 입니다.",
                     "회사가 공장을 증설했습니다입니다.", "회사가 공장을 증설했습니다.습니다."):
            with self.subTest(text=text):
                self.assertFalse(_is_valid_report_summary(text))

    def test_checks_every_sentence_without_requiring_whitespace(self):
        self.assertFalse(_is_valid_report_summary("공장 증설 발표.회사가 사업을 확대했습니다."))
        self.assertFalse(_is_valid_report_summary("공장을 증설했습니다.채용했습니다.생산했습니다."))
        self.assertTrue(_is_valid_report_summary("회사가 공장을 증설했습니다.채용도 확대했습니다."))
        self.assertTrue(_is_valid_report_summary("회사의 영업이익은 3.5% 늘었습니다."))
        self.assertTrue(_is_valid_report_summary('회사는 “공장 증설을 추진한다”고 밝혔습니다.'))


class SourceGroundingTests(unittest.TestCase):
    def test_corporate_story_preserves_source_headline_country(self):
        article = source("Zambia approves a copper mine expansion", "Zambia approved a copper mine expansion.")
        invalid = decision(article, "조바키아 구리 광산 증설 승인", "조바키아가 구리 광산 증설을 승인했습니다.")
        item, reason = _decision_to_item(invalid, [article], set())
        self.assertIsNone(item)
        self.assertEqual(reason, "missing_source_entity")
        valid = decision(article, "잠비아 구리 광산 증설 승인", "잠비아가 구리 광산 증설을 승인했습니다.")
        self.assertIsNotNone(_decision_to_item(valid, [article], set())[0])

    def test_country_guard_is_source_driven_not_a_bad_spelling_list(self):
        for name, wrong in (("Zambia", "잠바아"), ("Zimbabwe", "잠비아"), ("Slovakia", "슬로베니아")):
            article = source(f"{name} approves copper mine expansion", f"{name} approved copper mine expansion.")
            item, _ = _decision_to_item(decision(article, f"{wrong} 광산 증설 승인", f"{wrong}가 광산 증설을 승인했습니다."), [article], set())
            self.assertIsNone(item)

    def test_rejects_unsupported_investment_advice_claim_family(self):
        article = source("Tesla faces restrictions on automated driving", "A regulator limited Tesla's automated driving system.")
        invalid = decision(article, "테슬라 투자 권유 제한", "당국이 테슬라 투자 권유를 제한했습니다.")
        item, reason = _decision_to_item(invalid, [article], set())
        self.assertIsNone(item)
        self.assertEqual(reason, "unsupported_investment_recommendation")
        valid = decision(article, "테슬라 자율주행 제한", "당국이 테슬라의 자율주행 시스템 사용을 제한했습니다.")
        self.assertIsNotNone(_decision_to_item(valid, [article], set())[0])

    def test_preserves_genuine_source_backed_recommendation_reporting(self):
        article = source("Tesla stock recommendation restricted", "The regulator restricted investment recommendations about Tesla shares.")
        valid = decision(article, "테슬라 투자 권유 제한", "당국이 테슬라 주식에 대한 투자 권유를 제한했습니다.")
        self.assertIsNotNone(_decision_to_item(valid, [article], set())[0])


class LegacySourceChainTests(unittest.TestCase):
    def test_screening_retains_original_headline_as_source(self):
        tree = ast.parse(Path("breaking_tracker.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "filter_breaking_news")
        namespace = {
            "is_market_open": lambda: True, "json": json,
            "safe_generate_content": lambda prompt: SimpleNamespace(text=json.dumps([
                {"temp_id": 0, "title": "잘못 번역된 제목", "content": "잘못된 문장입니다."}
            ])),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "breaking_tracker.py", "exec"), namespace)
        result = namespace["filter_breaking_news"]([{"title": "Zambia approves copper mine expansion", "link": "https://example.com/a"}], [])
        self.assertEqual(result[0].get("source_title"), "Zambia approves copper mine expansion")


class LegacyValidationTests(unittest.TestCase):
    def analyze(self, content, source_title="Zambia approves copper mine expansion"):
        import news_pipeline
        tree = ast.parse(Path("breaking_tracker.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "perform_deep_analysis")
        raw_body = "Zambia approved copper mine expansion. The government confirmed the approval following a formal review of the mining project."
        newspaper = ModuleType("newspaper")
        class Article:
            def __init__(self, *args, **kwargs):
                self.text, self.top_image = raw_body, ""
            def download(self):
                pass
            def parse(self):
                pass
        newspaper.Article, newspaper.Config = Article, SimpleNamespace
        namespace = {
            "is_market_open": lambda: True, "json": json,
            "validate_legacy_headline": news_pipeline.validate_legacy_headline,
            "safe_generate_content": lambda prompt: SimpleNamespace(text=json.dumps([
                {"id": 0, "title": "잠비아 구리 광산 증설 승인", "content": content,
                 "importance_score": 8, "category": "corporate", "source_excerpt": raw_body}
            ])),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "breaking_tracker.py", "exec"), namespace)
        with patch.dict(sys.modules, {"newspaper": newspaper}), patch("time.sleep"):
            return namespace["perform_deep_analysis"]([
                {"title": "오류가 있는 AI 제목", "source_title": source_title, "original_url": "https://example.com/a"}
            ], [])

    def test_rss_body_summary_is_ignored_and_never_published(self):
        result = self.analyze("잠비아가 구리 광산 증설을 승인했습니다.입니다.")
        self.assertEqual(result[0]["content"], "")

    def test_valid_rss_summary_keeps_original_source_and_public_fields(self):
        result = self.analyze("잠비아가 구리 광산 증설을 승인했습니다.")
        self.assertEqual(result[0]["title"], "잠비아 구리 광산 증설 승인")
        self.assertEqual(result[0]["original_url"], "https://example.com/a")
        self.assertEqual(result[0]["content"], "")


class ObservedPublicOutputTests(unittest.TestCase):
    def test_observed_live_rows_are_rejected_without_typo_replacement(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/live_quality_20261008.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                if "source_excerpt" in case:
                    article = source(case["source_title"], case["source_excerpt"])
                    article["raw_content"] += " " + case.get("source_additional_excerpt", "")
                    item, reason = _decision_to_item(decision(article, case["observed_title"], case["observed_content"]), [article], set())
                    self.assertIsNone(item)
                    self.assertEqual(reason, case["expected_reason"])
                else:
                    self.assertFalse(_is_valid_report_summary(case["observed_content"]))


class ActorRoleGroundingTests(unittest.TestCase):
    """Synthetic source sentences isolate actor/role behavior from transport."""
    def test_cannot_turn_president_into_prime_minister_even_with_correct_country(self):
        article = source("Zambia announces solar target achievement", "President Hakainde Hichilema announced Zambia's solar target achievement.")
        bad = decision(article, "잠비아 태양광 목표 달성", "잠비아 총리가 태양광 목표 달성을 발표했습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "unsupported_official_role")
        good = decision(article, "잠비아 태양광 목표 달성", "잠비아 대통령이 태양광 목표 달성을 발표했습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])

    def test_does_not_assign_second_company_privacy_statement_to_first_company(self):
        article = source("테슬라코리아 검증 협조 및 BYD코리아 개인정보 보호 방침", "테슬라코리아는 안전성 검증에 협조하기로 했다. BYD코리아는 개인정보 보호를 위해 국내법을 준수하겠다고 밝혔다.")
        bad = decision(article, "테슬라코리아 안전 검증 협조", "테슬라코리아는 안전 검증에 협조하고 개인정보 보호를 위해 국내법을 준수하겠다고 밝혔습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "incorrect_source_claim_actor")
        good = decision(article, "테슬라코리아 안전 검증 협조", "테슬라코리아는 안전 검증에 협조한다고 밝혔습니다. BYD코리아는 개인정보 보호를 위해 국내법을 준수하겠다고 밝혔습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])

    def test_investment_disclaimer_is_not_support_for_a_recommendation_claim(self):
        article = source("Tesla cooperates with safety validation", "Tesla agreed to cooperate with safety validation. This article is not investment advice.")
        bad = decision(article, "테슬라 투자 권유 제한", "테슬라가 투자 권유를 제한했습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "unsupported_investment_recommendation")


class ReviewedRecommendationBoundaryTests(unittest.TestCase):
    def test_does_not_provide_disclaimer_does_not_support_investment_restrictions(self):
        article = source("Tesla faces product restrictions", "The regulator restricted automated driving. The publisher does not provide investment advice.")
        bad = decision(article, "테슬라 투자 권유 제한", "당국이 테슬라 투자 권유를 제한했습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "unsupported_investment_recommendation")

    def test_other_company_recommendation_does_not_support_tesla_restrictions(self):
        article = source("Tesla expands factory", "Tesla will expand a factory. Analysts recommend buying Samsung shares.")
        bad = decision(article, "테슬라 투자 권유 제한", "당국이 테슬라 투자 권유를 제한했습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "unsupported_investment_recommendation")

    def test_other_company_restriction_does_not_support_tesla_restrictions(self):
        article = source("Tesla expands factory", "Tesla will expand a factory. The regulator restricted investment recommendations about Samsung shares.")
        bad = decision(article, "테슬라 투자 권유 제한", "당국이 테슬라 투자 권유를 제한했습니다.")
        self.assertEqual(_decision_to_item(bad, [article], set())[1], "unsupported_investment_recommendation")


class ReviewedCountryBoundaryTests(unittest.TestCase):
    def test_indonesia_does_not_count_as_india(self):
        article = source("India approves copper mine expansion", "India approved copper mine expansion.")
        bad = decision(article, "인도네시아 광산 증설 승인", "인도네시아가 광산 증설을 승인했습니다.")
        self.assertIsNone(_decision_to_item(bad, [article], set())[0])

    def test_correct_headline_does_not_hide_different_known_country_in_body(self):
        article = source("Slovakia approves copper mine expansion", "Slovakia approved copper mine expansion.")
        bad = decision(article, "슬로바키아 광산 증설 승인", "슬로베니아가 광산 증설을 승인했습니다.")
        self.assertIsNone(_decision_to_item(bad, [article], set())[0])

    def test_preserves_legitimate_multicountry_source_and_korean_particles(self):
        article = source("India and Indonesia approve trade pact", "India and Indonesia approved a trade pact.")
        good = decision(article, "인도·인도네시아 무역 협정 승인", "인도와 인도네시아가 무역 협정을 승인했습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])


class SourceBackedCompoundAliasTests(unittest.TestCase):
    def test_preserves_bank_of_japan_compound(self):
        article = source("Bank of Japan raises interest rates", "The Bank of Japan raised interest rates.")
        good = decision(article, "일본은행 기준금리 인상", "일본은행이 기준금리를 인상했습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])

    def test_preserves_bank_of_korea_compound(self):
        article = source("South Korea central bank cuts interest rates", "The South Korea central bank cut interest rates.")
        good = decision(article, "한국은행 기준금리 인하", "한국은행이 기준금리를 인하했습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])

    def test_preserves_explicit_tesla_korea_compound(self):
        article = source("Tesla Korea agrees to safety checks", "Tesla Korea agreed to cooperate with safety checks.")
        good = decision(article, "테슬라코리아 안전성 검증 협조", "테슬라코리아가 안전성 검증에 협조하기로 했습니다.")
        self.assertIsNotNone(_decision_to_item(good, [article], set())[0])


if __name__ == "__main__":
    unittest.main()
