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
                    "source_ref": "earnings-1",
                    "source_title": "Chipmaker reports quarterly earnings",
                    "title": "반도체 기업 매출 18% 증가 📈",
                    "content": "분기 실적 발표에서 매출이 18% 증가했습니다.",
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
            "분기 실적 발표에서 매출이 18% 증가했습니다.",
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
                        "source_ref": "rate-1",
                        "source_title": "Central bank holds policy rate",
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

    def test_restarts_selection_once_when_json_repair_also_fails(self):
        generator = FakeGenerator(
            [
                "not json",
                "still not json",
                """[
                    {
                        "temp_id": 0,
                        "source_ref": "rate-retry",
                        "source_title": "Central bank announces rate decision",
                        "title": "중앙은행 금리 결정 발표",
                        "content": "중앙은행이 새로운 금리 결정을 발표했습니다.",
                        "importance_score": 8,
                        "category": "indicator",
                        "news_type": "official_announcement",
                        "selection_reason": "새 금리 결정을 공식 발표함"
                    }
                ]""",
            ]
        )

        selected = select_and_summarize(
            [
                article(
                    "rate-retry",
                    "Central bank announces rate decision",
                    "The central bank published its new policy decision.",
                    "https://example.com/rate-retry",
                )
            ],
            generator,
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["rate-retry"],
        )
        self.assertEqual(len(generator.prompts), 3)
        self.assertIn("JSON 문법만 수정", generator.prompts[1])
        self.assertIn("Central bank announces rate decision", generator.prompts[2])

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
        self.assertIn('"source_ref": "article-0"', generator.prompts[0])
        self.assertNotIn('"source_ref": "article-10"', generator.prompts[0])
        self.assertIn('"source_ref": "article-10"', generator.prompts[1])

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
            article(
                "analysis-6",
                "What the market is saying about the U.S. yen intervention",
                "A collection of market commentary.",
                "https://example.com/analysis-6",
            ),
            article(
                "analysis-7",
                "TD Cowen says buy Circle. Morgan Stanley thinks it is time to sell",
                "Two analyst recommendations.",
                "https://example.com/analysis-7",
            ),
            article(
                "analysis-8",
                "Half of employees want promotions without management, study finds",
                "A workplace survey article.",
                "https://example.com/analysis-8",
            ),
            article(
                "analysis-9",
                "[외환-마감] 개입 경계감에 무거운 상단…1,420원대 유지",
                "외환시장 마감 시황 기사.",
                "https://example.com/analysis-9",
            ),
            article(
                "analysis-10",
                "SpaceX and AMD test Nasdaq 100 bullish turn",
                "A technical market outlook.",
                "https://example.com/analysis-10",
            ),
            article(
                "analysis-11",
                "Ford CEO predicts when Chinese EVs will enter the US market",
                "A long-term prediction.",
                "https://example.com/analysis-11",
            ),
            article(
                "analysis-12",
                "The effect of green carbon dots for photocatalytic reduction of CO2",
                "A scientific research paper.",
                "https://example.com/analysis-12",
            ),
            article(
                "analysis-13",
                "German businesses assess liability frameworks ahead of EU deadline",
                "A legal explainer before a future deadline.",
                "https://example.com/analysis-13",
            ),
            article(
                "analysis-14",
                "Deutsche says this subsector may offer some protection",
                "An analyst suggests stocks as downside protection.",
                "https://example.com/analysis-14",
            ),
            article(
                "analysis-15",
                "3 Investing Lessons From Implosion of a Hedge Fund",
                "An article extracting investing lessons from an older event.",
                "https://example.com/analysis-15",
            ),
        ]
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 5,
                    "source_ref": "guidance-1",
                    "source_title": "Why Axogen is up after raising 2026 revenue guidance",
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
        self.assertNotIn("What the market is saying", generator.prompts[0])
        self.assertNotIn("says buy Circle", generator.prompts[0])
        self.assertNotIn("study finds", generator.prompts[0])
        self.assertNotIn("외환-마감", generator.prompts[0])
        self.assertNotIn("bullish turn", generator.prompts[0])
        self.assertNotIn("predicts when", generator.prompts[0])
        self.assertNotIn("The effect of", generator.prompts[0])
        self.assertNotIn("ahead of EU deadline", generator.prompts[0])
        self.assertNotIn("may offer some protection", generator.prompts[0])
        self.assertNotIn("Investing Lessons", generator.prompts[0])
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
                    "source_ref": "official-1",
                    "source_title": "Central bank announces a new rate decision",
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

    def test_excludes_analysis_and_non_economic_feed_sources_before_ai(self):
        blocked_sources = []
        for index, source_id in enumerate(
            ("www.cmcmarkets.com", "www.nature.com", "www.cancernetwork.com")
        ):
            blocked_sources.append(
                {
                    **article(
                        f"blocked-{index}",
                        f"Blocked feed item {index}",
                        "Not a direct economic news event.",
                        f"https://{source_id}/blocked-{index}",
                    ),
                    "source_id": source_id,
                }
            )
        trusted = article(
            "trusted-news",
            "Central bank announces a new rate decision",
            "The policy rate decision was released today.",
            "https://example.com/trusted-news",
        )
        generator = FakeGenerator("[]")

        selected = select_and_summarize([*blocked_sources, trusted], generator)

        self.assertEqual(selected, [])
        for index in range(3):
            self.assertNotIn(f"blocked-{index}", generator.prompts[0])
        self.assertIn("trusted-news", generator.prompts[0])

    def test_excludes_minor_card_product_changes_but_keeps_card_market_news(self):
        minor_product_change = article(
            "citi-card-benefits",
            "Major Citi AAdvantage Executive Card Changes: Higher Fee, New Benefits",
            "The annual fee is increasing and product benefits are changing.",
            "https://example.com/citi-card-benefits",
        )
        market_news = article(
            "card-delinquency",
            "US credit card delinquency rate rises in latest quarter",
            "The latest industry delinquency data was released.",
            "https://example.com/card-delinquency",
        )
        generator = FakeGenerator("[]")

        selected = select_and_summarize(
            [minor_product_change, market_news],
            generator,
        )

        self.assertEqual(selected, [])
        self.assertNotIn("citi-card-benefits", generator.prompts[0])
        self.assertIn("card-delinquency", generator.prompts[0])

    def test_excludes_low_value_outlooks_research_and_consumer_fee_changes(self):
        low_value_articles = [
            article(
                "tanker-outlook",
                "2026 Tanker Market Outlook: Rates Near Historical Highs",
                "An outlook on whether historically high rates may continue.",
                "https://example.com/tanker-outlook",
            ),
            article(
                "milk-fuel-research",
                "Researchers turn milk waste into renewable fuel",
                "Researchers describe a new laboratory method.",
                "https://example.com/milk-fuel-research",
            ),
            article(
                "airline-membership-fee",
                "American Airlines raises Admirals Club annual membership fee",
                "The consumer lounge membership fee will increase.",
                "https://example.com/airline-membership-fee",
            ),
            article(
                "smartphone-trend",
                "Smartphone makers step up hardware innovation",
                "A broad review of foldable display competition.",
                "https://example.com/smartphone-trend",
            ),
        ]
        economic_release = article(
            "manufacturing-index",
            "US manufacturing index expands at fastest pace in four years",
            "The latest official manufacturing index was released.",
            "https://example.com/manufacturing-index",
        )
        generator = FakeGenerator("[]")

        selected = select_and_summarize(
            [*low_value_articles, economic_release],
            generator,
        )

        self.assertEqual(selected, [])
        for item in low_value_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        self.assertIn("manufacturing-index", generator.prompts[0])

    def test_excludes_promotional_milestones_rankings_and_routine_notices_before_ai(self):
        low_value_articles = [
            article(
                "product-milestone",
                "Bibigo salmon steak sales top 1.4 million since launch",
                "CJ CheilJedang highlighted cumulative sales of one consumer product.",
                "https://example.com/product-milestone",
            ),
            article(
                "forbes-ranking",
                "Malaysia companies named to Forbes Asia Best Under A Billion list",
                "Nineteen companies were included in the annual ranking.",
                "https://example.com/forbes-ranking",
            ),
            article(
                "tax-reminder",
                "Tax agency reminds 545,000 companies of interim corporate tax filing deadline",
                "The agency repeated the regular August filing and payment schedule.",
                "https://example.com/tax-reminder",
            ),
        ]
        policy_news = article(
            "isa-reform",
            "South Korea unveils tax reform creating productive finance ISA",
            "The government announced a new tax policy for domestic capital markets.",
            "https://example.com/isa-reform",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([*low_value_articles, policy_news], generator)

        for item in low_value_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        self.assertIn("isa-reform", generator.prompts[0])

    def test_excludes_local_services_routine_hires_explainers_and_earnings_previews(self):
        low_value_articles = [
            article(
                "airport-wheelchairs",
                "Changi Airport rolls out autonomous wheelchairs in terminals 2 and 3",
                "The airport introduced a passenger assistance service with SATS.",
                "https://example.com/airport-wheelchairs",
            ),
            article(
                "routine-md-hire",
                "Markel names Allianz portfolio chief as London specialty managing director",
                "The insurer filled a routine managing director role.",
                "https://example.com/routine-md-hire",
            ),
            article(
                "local-bottle-shop",
                "Auckland CBD bottle shop ordered to shut down",
                "A local liquor store received a closure order.",
                "https://example.com/local-bottle-shop",
            ),
            article(
                "consumer-study",
                "The sneaky economics of healthwashing",
                "A UC Davis study examined adulterated avocado oil products.",
                "https://example.com/consumer-study",
            ),
            article(
                "earnings-preview",
                "SpaceX set to report first quarterly earnings after market close",
                "The company will report results later today.",
                "https://example.com/earnings-preview",
            ),
        ]
        material_articles = [
            article(
                "energy-acquisition",
                "TotalEnergies buys Shell European renewable energy business",
                "The companies announced a completed acquisition of wind and solar assets.",
                "https://example.com/energy-acquisition",
            ),
            article(
                "reported-earnings",
                "Chipmaker reports quarterly earnings after market close",
                "Revenue and profit rose after the company released its results.",
                "https://example.com/reported-earnings",
            ),
            article(
                "ceo-succession",
                "Insurer names regional head as chief executive officer",
                "The board appointed its regional head as the company's new CEO.",
                "https://example.com/ceo-succession",
            ),
        ]
        generator = FakeGenerator("[]")

        select_and_summarize([*low_value_articles, *material_articles], generator)

        for item in low_value_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        for item in material_articles:
            self.assertIn(item["provider_article_id"], generator.prompts[0])

    def test_excludes_recently_saved_same_event_but_keeps_distinct_company_news(self):
        boeing = article(
            "boeing-certification",
            "FAA certifies Boeing MAX aircraft",
            "The FAA issued certification for Boeing's MAX aircraft.",
            "https://example.com/boeing-certification",
        )
        tesla = article(
            "tesla-launch",
            "Tesla launches Model Y L in the United States",
            "Tesla launched its Model Y L vehicle in the United States.",
            "https://example.com/tesla-launch",
        )
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "source_ref": "boeing-certification",
                    "source_title": "FAA certifies Boeing MAX aircraft",
                    "title": "FAA, 보잉 MAX 항공기 인증",
                    "content": "FAA가 보잉 MAX 항공기에 인증을 발급했습니다.",
                    "importance_score": 8,
                    "category": "corporate",
                    "news_type": "new_development",
                    "selection_reason": "FAA가 항공기 인증을 새로 발급했습니다."
                },
                {
                    "temp_id": 1,
                    "source_ref": "tesla-launch",
                    "source_title": "Tesla launches Model Y L in the United States",
                    "title": "테슬라, 미국서 모델 Y L 출시",
                    "content": "테슬라가 미국에서 모델 Y L 차량을 출시했습니다.",
                    "importance_score": 7,
                    "category": "corporate",
                    "news_type": "new_development",
                    "selection_reason": "테슬라가 미국에서 신차를 출시했습니다."
                }
            ]"""
        )
        recent_news = [
            {
                "title": "미 FAA, 보잉 MAX 인증 발급",
                "content": "미 연방항공청이 보잉 MAX 항공기에 인증을 발급했습니다.",
                "created_at": "2026-08-03T01:00:00+00:00",
            }
        ]

        selected = select_and_summarize(
            [boeing, tesla],
            generator,
            recent_news=recent_news,
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["tesla-launch"],
        )
        self.assertIn("미 FAA, 보잉 MAX 인증 발급", generator.prompts[0])

    def test_keeps_material_follow_up_to_a_recent_event(self):
        source = article(
            "boeing-complete",
            "Boeing MAX certification process completed",
            "The Boeing MAX certification process has been completed.",
            "https://example.com/boeing-complete",
        )
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "source_ref": "boeing-complete",
                    "source_title": "Boeing MAX certification process completed",
                    "title": "보잉 MAX 인증 절차 완료",
                    "content": "보잉 MAX 항공기의 인증 절차가 완료됐습니다.",
                    "importance_score": 8,
                    "category": "corporate",
                    "news_type": "follow_up",
                    "selection_reason": "기존 인증 절차가 완료됐습니다."
                }
            ]"""
        )

        selected = select_and_summarize(
            [source],
            generator,
            recent_news=[
                {
                    "title": "보잉 MAX 인증 절차 시작",
                    "content": "보잉 MAX 항공기의 인증 절차가 시작됐습니다.",
                    "created_at": "2026-08-03T01:00:00+00:00",
                }
            ],
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["boeing-complete"],
        )

    def test_recent_news_prompt_is_duplicate_context_not_a_summary_source(self):
        source = article(
            "new-policy",
            "Government announces a new policy",
            "The government announced a new economic policy.",
            "https://example.com/new-policy",
        )
        generator = FakeGenerator("[]")

        select_and_summarize(
            [source],
            generator,
            recent_news=[
                {
                    "title": "기존 금리 발표",
                    "content": "중앙은행이 기준금리를 발표했습니다.",
                }
            ],
        )

        self.assertIn("기존 금리 발표", generator.prompts[0])
        self.assertIn("중복 비교", generator.prompts[0])
        self.assertIn("사실 근거로 사용하지", generator.prompts[0])

    def test_deduplicates_against_recent_item_beyond_ai_prompt_context(self):
        source = article(
            "boeing-duplicate",
            "FAA certifies Boeing MAX aircraft",
            "The FAA issued certification for Boeing's MAX aircraft.",
            "https://example.com/boeing-duplicate",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "boeing-duplicate",
                "source_title": "FAA certifies Boeing MAX aircraft",
                "title": "FAA, 보잉 MAX 항공기 인증",
                "content": "FAA가 보잉 MAX 항공기에 인증을 발급했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "FAA가 항공기 인증을 새로 발급했습니다."
            }
        ]"""
        unrelated = [
            {
                "title": f"서로 다른 경제 소식 {index}",
                "content": f"서로 다른 경제 사건 {index}이 발표됐습니다.",
            }
            for index in range(100)
        ]
        duplicate = {
            "title": "미 FAA, 보잉 MAX 인증 발급",
            "content": "미 연방항공청이 보잉 MAX 항공기에 인증을 발급했습니다.",
        }

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=[*unrelated, duplicate],
        )

        self.assertEqual(selected, [])

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
                    "source_ref": "duplicate-1",
                    "source_title": "Trump says US intervened in yen market",
                    "title": "트럼프, 엔화 약세 저지 위해 시장 개입",
                    "content": "트럼프 대통령은 일본과의 관계를 고려해 엔화 약세 저지를 위한 시장 개입을 했다고 밝혔습니다.",
                    "importance_score": 8,
                    "category": "market",
                    "news_type": "new_development",
                    "selection_reason": "미국의 엔화 시장 개입 사실을 발표함"
                },
                {
                    "temp_id": 1,
                    "source_ref": "duplicate-2",
                    "source_title": "Trump says US intervened in yen-dollar market due to Japan ties",
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

    def test_deduplicates_different_headlines_from_the_same_statistical_release(self):
        short_report = article(
            "short-term-note-record",
            "Korea short-term note issuance rises 90.4% to a record",
            "First-half short-term note issuance rose 90.4%.",
            "https://example.com/short-term-note-record",
        )
        complete_report = article(
            "corporate-financing-report",
            "Korea stock and bond issuance falls while CP and short-term notes surge",
            "The same first-half FSS release said CP rose 19% and short-term notes rose 90.4%.",
            "https://example.com/corporate-financing-report",
        )
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "source_ref": "short-term-note-record",
                    "source_title": "Korea short-term note issuance rises 90.4% to a record",
                    "title": "상반기 단기사채·CP 발행액 역대 최대",
                    "content": "상반기 단기사채 발행액이 90.4% 증가했습니다.",
                    "importance_score": 7,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "상반기 기업 자금조달 통계가 발표됐습니다."
                },
                {
                    "temp_id": 1,
                    "source_ref": "corporate-financing-report",
                    "source_title": "Korea stock and bond issuance falls while CP and short-term notes surge",
                    "title": "상반기 주식·채권 감소, CP·단기사채 급증",
                    "content": "상반기 주식·회사채 발행은 줄고 CP·단기사채는 각각 19%, 90.4% 증가했습니다.",
                    "importance_score": 7,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "상반기 기업 자금조달 통계가 발표됐습니다."
                }
            ]"""
        )

        selected = select_and_summarize([short_report, complete_report], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["corporate-financing-report"],
        )

    def test_keeps_distinct_indicators_with_the_same_country_month_and_value(self):
        inflation = article(
            "us-inflation",
            "US consumer prices rise 3% in July",
            "US consumer prices rose 3% in July.",
            "https://example.com/us-inflation",
        )
        unemployment = article(
            "us-unemployment",
            "US unemployment rate reaches 3% in July",
            "The US unemployment rate was 3% in July.",
            "https://example.com/us-unemployment",
        )
        generator = FakeGenerator(
            """[
                {
                    "temp_id": 0,
                    "source_ref": "us-inflation",
                    "source_title": "US consumer prices rise 3% in July",
                    "title": "미국 7월 소비자물가 3% 상승",
                    "content": "미국 7월 소비자물가가 3% 상승했습니다.",
                    "importance_score": 8,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "미국 소비자물가가 새로 발표됐습니다."
                },
                {
                    "temp_id": 1,
                    "source_ref": "us-unemployment",
                    "source_title": "US unemployment rate reaches 3% in July",
                    "title": "미국 7월 실업률 3% 기록",
                    "content": "미국 7월 실업률이 3%를 기록했습니다.",
                    "importance_score": 8,
                    "category": "indicator",
                    "news_type": "official_announcement",
                    "selection_reason": "미국 실업률이 새로 발표됐습니다."
                }
            ]"""
        )

        selected = select_and_summarize([inflation, unemployment], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["us-inflation", "us-unemployment"],
        )

    def test_rejects_summary_bound_to_a_different_source_title(self):
        yen_article = article(
            "yen-intervention",
            "United States and Japan confirm yen intervention",
            "The dollar moved after the United States and Japan confirmed intervention.",
            "https://example.com/yen-intervention",
        )
        circle_article = article(
            "circle-rating",
            "Circle reports quarterly revenue growth",
            "Circle published its latest quarterly results.",
            "https://example.com/circle-rating",
        )
        mismatched_response = """[
                {
                    "temp_id": 0,
                    "source_ref": "yen-intervention",
                    "source_title": "Circle reports quarterly revenue growth",
                    "title": "서클 분기 매출 증가",
                    "content": "서클이 최신 분기 매출 증가를 발표했습니다.",
                    "importance_score": 8,
                    "category": "market",
                    "news_type": "new_development",
                    "selection_reason": "서클이 신규 분기 실적을 발표함"
                }
            ]"""
        generator = FakeGenerator(mismatched_response)

        selected = select_and_summarize([yen_article, circle_article], generator)

        self.assertEqual(selected, [])

    def test_rejects_summary_with_number_missing_from_source_article(self):
        source = article(
            "yen-policy",
            "United States and Japan confirm yen intervention",
            "Officials confirmed coordinated action in the foreign exchange market.",
            "https://example.com/yen-policy",
        )
        source["raw_content"] = "The source article continues... [31 chars]"
        unsupported_number_response = """[
                {
                    "temp_id": 0,
                    "source_ref": "yen-policy",
                    "source_title": "United States and Japan confirm yen intervention",
                    "title": "미·일 엔화 시장 개입 확인",
                    "content": "미·일 정부가 엔화 시장에 개입해 달러가 31% 하락했습니다.",
                    "importance_score": 9,
                    "category": "market",
                    "news_type": "official_announcement",
                    "selection_reason": "양국 정부가 외환시장 개입을 확인함"
                }
            ]"""
        generator = FakeGenerator(unsupported_number_response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_keeps_safe_decision_when_another_decision_fails_source_validation(self):
        unsafe = article(
            "unsafe-article",
            "United States and Japan confirm yen intervention",
            "Officials confirmed action in the foreign exchange market.",
            "https://example.com/unsafe-article",
        )
        safe = article(
            "safe-article",
            "Government announces a new tax policy",
            "The government published its latest tax policy.",
            "https://example.com/safe-article",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "unsafe-article",
                "source_title": "Government announces a new tax policy",
                "title": "잘못 연결된 요약",
                "content": "다른 기사의 내용이 연결됐습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "잘못 연결된 응답"
            },
            {
                "temp_id": 1,
                "source_ref": "safe-article",
                "source_title": "Government announces a new tax policy",
                "title": "정부, 새 세제 정책 발표",
                "content": "정부가 새로운 세제 정책을 발표했습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "정부가 새 세제 정책을 발표함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([unsafe, safe], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["safe-article"],
        )

    def test_rejects_non_korean_title_or_summary(self):
        source = article(
            "chinese-output",
            "Nvidia faces new competition",
            "The company faces new competition in its software business.",
            "https://example.com/chinese-output",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "chinese-output",
                "source_title": "Nvidia faces new competition",
                "title": "英伟达面临新的竞争",
                "content": "该公司的软件业务面临新的竞争。",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "새로운 경쟁 상황"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_rejects_macro_summary_that_omits_source_country(self):
        source = article(
            "korea-inflation",
            "South Korea consumer prices rise 2.8% in July",
            "South Korea reported that consumer prices rose 2.8% from a year earlier.",
            "https://example.com/korea-inflation",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "korea-inflation",
                "source_title": "South Korea consumer prices rise 2.8% in July",
                "title": "7월 소비자물가 2.8%로 둔화",
                "content": "7월 소비자물가가 전년 대비 2.8% 상승했습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "새 소비자물가 통계가 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_summary_with_untranslated_english_prose(self):
        source = article(
            "commodity-report",
            "Goldman says China's metals dominance increases commodity volatility",
            "The report said China's metals dominance can increase commodity volatility.",
            "https://example.com/commodity-report",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "commodity-report",
                "source_title": "Goldman says China's metals dominance increases commodity volatility",
                "title": "골드만삭스, 중국 상품시장 영향 분석",
                "content": "중국의 핵심 금속 공급망 dominance가 상품시장 변동성을 키운다고 밝혔습니다.",
                "importance_score": 7,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "새 상품시장 분석 보고서를 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_unexplained_specialist_acronym(self):
        source = article(
            "gpif-management",
            "Japan GPIF increases passive fund manager engagement to 62.6%",
            "Japan's public pension fund said manager engagement reached 62.6%.",
            "https://example.com/gpif-management",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "gpif-management",
                "source_title": "Japan GPIF increases passive fund manager engagement to 62.6%",
                "title": "GPIF, 패시브펀드 관리 강화",
                "content": "일본 GPIF가 패시브펀드 관리자 참여를 62.6%로 높였습니다.",
                "importance_score": 7,
                "category": "market",
                "news_type": "official_announcement",
                "selection_reason": "새 운용 현황을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_normalizes_known_company_transliteration(self):
        source = article(
            "nissan-results",
            "Nissan returns to quarterly profit and maintains outlook",
            "Nissan returned to profit in the quarter and maintained its annual outlook.",
            "https://example.com/nissan-results",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "nissan-results",
                "source_title": "Nissan returns to quarterly profit and maintains outlook",
                "title": "니산, 분기 흑자 전환 및 전망 유지",
                "content": "니산이 분기 흑자로 전환하고 연간 전망을 유지했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "새 분기 실적이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["normalized_title"], "닛산, 분기 흑자 전환 및 전망 유지")
        self.assertEqual(
            selected[0]["normalized_content"],
            "닛산이 분기 흑자로 전환하고 연간 전망을 유지했습니다.",
        )

    def test_normalizes_auckland_transliteration(self):
        source = article(
            "auckland-port-investment",
            "Auckland port approves major expansion investment",
            "The port approved a major expansion to increase cargo capacity.",
            "https://example.com/auckland-port-investment",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "auckland-port-investment",
                "source_title": "Auckland port approves major expansion investment",
                "title": "아크랜드 항만, 대규모 확장 투자 승인",
                "content": "아크랜드 항만이 물류 처리 능력을 높이기 위한 대규모 확장 투자를 승인했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "항만 운영사가 확장 투자를 승인했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["normalized_title"], "오클랜드 항만, 대규모 확장 투자 승인")
        self.assertEqual(
            selected[0]["normalized_content"],
            "오클랜드 항만이 물류 처리 능력을 높이기 위한 대규모 확장 투자를 승인했습니다.",
        )

    def test_rejects_summary_without_polite_report_ending(self):
        source = article(
            "market-rise",
            "Wall Street rises on hopes of Middle East deal",
            "The three major US indexes rose as hopes grew for easing Middle East tensions.",
            "https://example.com/market-rise",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "market-rise",
                "source_title": "Wall Street rises on hopes of Middle East deal",
                "title": "중동 갈등 완화 기대감으로 뉴욕증시 상승",
                "content": "미국-이란 갈등 완화 기대감에 뉴욕증시 3대 지수 상승",
                "importance_score": 7,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "중동 긴장 완화 기대감으로 주요 지수가 상승함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_rejects_korean_placeholder_summary(self):
        source = article(
            "short-source",
            "Company announces a business update",
            "The company published a new business update.",
            "https://example.com/short-source",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "short-source",
                "source_title": "Company announces a business update",
                "title": "기업, 사업 현황 발표",
                "content": "기사 내용이 부족합니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "기업이 사업 현황을 발표함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_rejects_machine_placeholder_with_korean_ending(self):
        source = article(
            "machine-placeholder",
            "Company publishes a new filing",
            "The company published a new filing today.",
            "https://example.com/machine-placeholder",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "machine-placeholder",
                "source_title": "Company publishes a new filing",
                "title": "기업, 신규 공시 발표",
                "content": "N/A입니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "기업이 신규 공시를 발표함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_rejects_broken_market_impact_template(self):
        source = article(
            "spain-growth",
            "Spanish economy beats growth expectations",
            "Spain reported economic growth above market expectations.",
            "https://example.com/spain-growth",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "spain-growth",
                "source_title": "Spanish economy beats growth expectations",
                "title": "스페인 경제 성장률 전망치 상회",
                "content": "스페인 경제 성장률이 전망치를 웃돌았습니다. 시장 영향: 유럽 경제 회복이 관전 포인트입니다~입니다.",
                "importance_score": 7,
                "category": "indicator",
                "news_type": "new_development",
                "selection_reason": "스페인이 전망치를 웃도는 성장률을 발표함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(selected, [])

    def test_prompt_defines_summary_quality_contract(self):
        source = article(
            "prompt-contract",
            "Central bank announces a policy decision",
            "The central bank published a new policy decision.",
            "https://example.com/prompt-contract",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([source], generator)

        prompt = generator.prompts[0]
        self.assertIn("정중한 보고체", prompt)
        self.assertIn("TEXT_TOO_SHORT", prompt)
        self.assertIn("시장 영향:", prompt)
        self.assertIn("7~8", prompt)
        self.assertIn("주요 경제 소식", prompt)
        self.assertIn("9~10", prompt)
        self.assertIn("긴급 속보", prompt)

    def test_allows_calendar_date_from_published_at(self):
        source = article(
            "dated-policy",
            "Government announces a new tax policy",
            "The government published its latest tax policy.",
            "https://example.com/dated-policy",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "dated-policy",
                "source_title": "Government announces a new tax policy",
                "title": "정부, 2026년 세제 정책 발표",
                "content": "정부가 8월 3일 새로운 세제 정책을 발표했습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "정부가 새 세제 정책을 발표함"
            }
        ]"""
        generator = FakeGenerator(response)

        selected = select_and_summarize([source], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["dated-policy"],
        )

if __name__ == "__main__":
    unittest.main()
