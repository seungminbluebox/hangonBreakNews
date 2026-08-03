from types import SimpleNamespace
import unittest

from news_selector import select_and_summarize


class FakeGenerator:
    def __init__(self, response_text):
        self.response_texts = iter(
            response_text if isinstance(response_text, list) else [response_text]
        )
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(text=next(self.response_texts))


def article(article_id, title, description, url):
    return {
        "provider": "gnews",
        "provider_article_id": article_id,
        "source_id": "example.com",
        "source_name": "Example News",
        "source_tier": "unrated",
        "source_url": "https://example.com",
        "source_country": "us",
        "original_url": url,
        "published_at": "2026-08-03T01:00:00+00:00",
        "updated_at": None,
        "fetched_at": "2026-08-03T02:00:00+00:00",
        "original_timezone": "UTC",
        "original_language": "en",
        "market_scope": "us",
        "raw_title": title,
        "raw_description": description,
        "raw_content": f"Article content for {article_id}",
        "normalized_title": None,
        "normalized_content": None,
        "image_url": None,
    }


class NewsSelectorTests(unittest.TestCase):
    def test_keeps_only_new_economic_development_and_preserves_source_facts(self):
        articles = [
            article(
                "earnings-1",
                "Chipmaker reports quarterly earnings",
                "Revenue rose 18% after the company released quarterly results.",
                "https://example.com/earnings",
            ),
            article(
                "comparison-1",
                "SpaceX versus the Magnificent Seven",
                "A comparison of valuations and past performance.",
                "https://example.com/comparison",
            ),
        ]
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "title": "반도체 기업 매출 18% 증가 📈",
                    "content": "분기 실적 발표에서 매출이 18% 증가했다.",
                    "importance_score": 7,
                    "category": "corporate",
                    "news_type": "new_development",
                    "selection_reason": "새 분기 실적과 수치를 발표함",
                    "original_url": "https://malicious.example/fake"
                },
                {
                    "temp_id": 1,
                    "title": "스페이스X 비교",
                    "content": "기업가치를 비교했다.",
                    "importance_score": 4,
                    "category": "corporate",
                    "news_type": "analysis",
                    "selection_reason": "기업 비교"
                }
            ]"""
        )

        selected = select_and_summarize(articles, generator)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["provider_article_id"], "earnings-1")
        self.assertEqual(selected[0]["raw_title"], "Chipmaker reports quarterly earnings")
        self.assertEqual(selected[0]["normalized_title"], "반도체 기업 매출 18% 증가 📈")
        self.assertEqual(
            selected[0]["normalized_content"],
            "분기 실적 발표에서 매출이 18% 증가했다.",
        )
        self.assertEqual(selected[0]["original_url"], "https://example.com/earnings")
        self.assertEqual(selected[0]["news_type"], "new_development")
        self.assertEqual(len(generator.prompts), 1)
        self.assertIn("Chipmaker reports quarterly earnings", generator.prompts[0])
        self.assertIn("A comparison of valuations and past performance.", generator.prompts[0])
        self.assertIn("110자 이내", generator.prompts[0])
        self.assertIn("원문에 없는 전망·인과관계", generator.prompts[0])

    def test_accepts_json_wrapped_in_a_markdown_code_fence(self):
        generator = FakeGenerator("```json\n[]\n```")

        selected = select_and_summarize(
            [
                article(
                    "article-1",
                    "A current business headline",
                    "A newly reported business development.",
                    "https://example.com/article-1",
                )
            ],
            generator,
        )

        self.assertEqual(selected, [])

    def test_repairs_invalid_json_once_without_using_it_as_news_facts(self):
        generator = FakeGenerator(
            [
                '[{"temp_id": 0, "title": "금리 동결", "content": 금리를 동결했다}]',
                """[
                    "invalid entry",
                    {"temp_id": 0, "news_type": "official_announcement"},
                    {
                        "temp_id": 0,
                        "title": "기준금리 동결 🏦",
                        "content": "중앙은행이 기준금리를 동결했습니다.",
                        "importance_score": 7,
                        "category": "indicator",
                        "news_type": "official_announcement",
                        "selection_reason": "새 금리 결정을 발표함"
                    }
                ]
                JSON 수정이 완료되었습니다.""",
            ]
        )

        selected = select_and_summarize(
            [
                article(
                    "rate-1",
                    "Central bank holds policy rate",
                    "The central bank announced its latest rate decision.",
                    "https://example.com/rate-1",
                )
            ],
            generator,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["normalized_title"], "기준금리 동결 🏦")
        self.assertEqual(len(generator.prompts), 2)
        self.assertIn("JSON 문법만 수정", generator.prompts[1])
        self.assertIn("원문 후보의 사실을 추가하거나 변경하지 마세요", generator.prompts[1])

    def test_splits_large_candidate_sets_into_ten_article_batches(self):
        articles = [
            article(
                f"article-{index}",
                f"Economic headline {index}",
                f"New economic development {index}.",
                f"https://example.com/article-{index}",
            )
            for index in range(11)
        ]
        generator = FakeGenerator(["[]", "[]"])

        selected = select_and_summarize(articles, generator)

        self.assertEqual(selected, [])
        self.assertEqual(len(generator.prompts), 2)
        self.assertIn('"temp_id": 0', generator.prompts[0])
        self.assertNotIn('"temp_id": 10', generator.prompts[0])
        self.assertIn('"temp_id": 10', generator.prompts[1])

    def test_excludes_obvious_analysis_titles_before_ai_without_losing_new_guidance(self):
        articles = [
            article(
                "analysis-1",
                "International Seaways stock looks fairly valued following its run",
                "A valuation discussion.",
                "https://example.com/analysis-1",
            ),
            article(
                "analysis-2",
                "What's going on with Xero's share price?",
                "A share-price explainer.",
                "https://example.com/analysis-2",
            ),
            article(
                "analysis-3",
                "Crypto market sees mixed moves this week",
                "A weekly market recap.",
                "https://example.com/analysis-3",
            ),
            article(
                "analysis-4",
                "바닥 다진 코스피 반등할까 [경제브리핑]",
                "시장 전망 기사.",
                "https://example.com/analysis-4",
            ),
            article(
                "analysis-5",
                "CEO comments on last month's AI model hack",
                "A new interview about an older incident.",
                "https://example.com/analysis-5",
            ),
            article(
                "guidance-1",
                "Why Axogen is up after raising 2026 revenue guidance",
                "The company raised its full-year revenue guidance.",
                "https://example.com/guidance-1",
            ),
        ]
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 5,
                    "title": "악소젠 매출 전망 상향 📈",
                    "content": "악소젠이 2026년 매출 가이던스를 상향했습니다.",
                    "importance_score": 7,
                    "category": "corporate",
                    "news_type": "official_announcement",
                    "selection_reason": "회사가 새 매출 전망을 발표함"
                }
            ]"""
        )

        selected = select_and_summarize(articles, generator)

        self.assertEqual([item["provider_article_id"] for item in selected], ["guidance-1"])
        self.assertEqual(len(generator.prompts), 1)
        self.assertNotIn("looks fairly valued", generator.prompts[0])
        self.assertNotIn("What's going on with", generator.prompts[0])
        self.assertNotIn("mixed moves", generator.prompts[0])
        self.assertNotIn("반등할까", generator.prompts[0])
        self.assertNotIn("last month's", generator.prompts[0])
        self.assertIn("raising 2026 revenue guidance", generator.prompts[0])

    def test_excludes_analysis_only_sources_before_ai(self):
        blocked = {
            **article(
                "analysis-source-1",
                "Company valuation after a recent share-price run",
                "An investment valuation article.",
                "https://simplywall.st/example",
            ),
            "source_id": "simplywall.st",
            "source_name": "simplywall.st",
        }
        trusted = article(
            "official-1",
            "Central bank announces a new rate decision",
            "The policy rate decision was released today.",
            "https://example.com/official-1",
        )
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 1,
                    "title": "중앙은행 금리 결정 발표",
                    "content": "중앙은행이 새로운 기준금리를 발표했습니다.",
                    "importance_score": 7,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "새 기준금리 결정을 발표함"
                }
            ]"""
        )

        selected = select_and_summarize([blocked, trusted], generator)

        self.assertEqual([item["provider_article_id"] for item in selected], ["official-1"])
        self.assertNotIn("simplywall.st", generator.prompts[0])

    def test_rejects_ai_decisions_below_existing_storage_threshold(self):
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "title": "주변 산업 소식",
                    "content": "중요도가 낮은 산업 소식입니다.",
                    "importance_score": 6,
                    "category": "corporate",
                    "news_type": "new_development",
                    "selection_reason": "작은 기업 변화가 발생함"
                }
            ]"""
        )

        selected = select_and_summarize(
            [
                article(
                    "minor-1",
                    "Minor company development",
                    "A small company announced a limited change.",
                    "https://example.com/minor-1",
                )
            ],
            generator,
        )

        self.assertEqual(selected, [])

    def test_keeps_one_more_complete_article_for_the_same_event(self):
        first = article(
            "duplicate-1",
            "Trump says US intervened in yen market",
            "Trump said the United States intervened.",
            "https://example.com/duplicate-1",
        )
        second = {
            **article(
                "duplicate-2",
                "Trump says US intervened in yen-dollar market due to Japan ties",
                "Trump said the United States intervened in the yen-dollar market because of its relationship with Japan.",
                "https://example.com/duplicate-2",
            ),
            "source_name": "Yonhap News",
        }
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "title": "트럼프, 엔화 약세 저지 위해 시장 개입",
                    "content": "트럼프 대통령은 일본과의 관계를 고려해 엔화 약세 저지를 위한 시장 개입을 했다고 밝혔습니다.",
                    "importance_score": 8,
                    "category": "market",
                    "news_type": "new_development",
                    "selection_reason": "미국의 엔화 시장 개입 사실을 발표함"
                },
                {
                    "temp_id": 1,
                    "title": "트럼프, 일본 관계 고려해 엔화 시장 개입",
                    "content": "트럼프 대통령은 일본과의 관계를 고려해 미국이 엔·달러 시장에 개입했다고 밝혔습니다.",
                    "importance_score": 8,
                    "category": "market",
                    "news_type": "new_development",
                    "selection_reason": "미국의 엔·달러 시장 개입 사실을 발표함"
                }
            ]"""
        )

        selected = select_and_summarize([first, second], generator)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["provider_article_id"], "duplicate-2")
        self.assertEqual(selected[0]["source_name"], "Yonhap News")


if __name__ == "__main__":
    unittest.main()
