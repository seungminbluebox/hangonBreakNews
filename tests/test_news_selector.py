from types import SimpleNamespace
import json
import unittest

from news_selector import (
    NEWS_SELECTION_RESPONSE_FORMAT,
    SELECTABLE_CATEGORIES,
    select_and_summarize,
)


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


def decision_for(
    source,
    temp_id,
    *,
    title,
    content,
    importance_score=8,
    category="corporate",
    news_type="new_development",
):
    return {
        "temp_id": temp_id,
        "source_ref": source["provider_article_id"],
        "source_title": source["raw_title"],
        "title": title,
        "content": content,
        "importance_score": importance_score,
        "category": category,
        "news_type": news_type,
        "selection_reason": "새로운 경제 관련 사실이 확인됐습니다.",
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
        self.assertNotIn(
            "A comparison of valuations and past performance.",
            generator.prompts[0],
        )
        self.assertIn("no more than 110 Korean characters", generator.prompts[0])
        self.assertIn("Do not invent forecasts, causal claims", generator.prompts[0])

    def test_selection_prompt_uses_english_controls_and_requires_korean_output(self):
        generator = FakeGenerator("[]")

        select_and_summarize(
            [
                article(
                    "prompt-contract",
                    "Company reports a concrete new investment",
                    "The company announced a binding factory investment.",
                    "https://example.com/prompt-contract",
                )
            ],
            generator,
        )

        prompt = " ".join(generator.prompts[0].split())
        for required_fragment in (
            "Write title, content, and selection_reason in natural Korean only",
            "DIRECT ECONOMIC RELEVANCE",
            "local crime, ceremonial events, awards, routine visits",
            "small community grants",
            "vague regional roundups",
            "regardless of company or country size",
            "actor, country, direction, currency, unit, and time basis",
            "Do not convert currencies",
            "Do not guess missing geography",
            "too broken or ambiguous to translate faithfully",
            "Scores 9-10 are reserved",
            "must remain at 7-8",
            "new casualty count, confirmed decision, revised statistic",
            "separate follow_up",
        ):
            self.assertIn(required_fragment, prompt)

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
        self.assertIn("Repair JSON syntax only", generator.prompts[1])
        self.assertIn("Do not add, remove, or change any news facts", generator.prompts[1])
        self.assertIn("Preserve Korean string values", generator.prompts[1])

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
        self.assertIn("Repair JSON syntax only", generator.prompts[1])
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

    def test_excludes_nonmaterial_reports_surveys_launches_and_meetings_before_ai(self):
        low_value_articles = [
            article(
                "campus-opening",
                "FDU opens 70,000-square-foot campus at Oakridge Park",
                "The university will open the new campus in September.",
                "https://example.com/campus-opening",
            ),
            article(
                "development-report",
                "World Bank report says AI could accelerate developing-country growth",
                "The report discussed how AI may support future economic growth.",
                "https://example.com/development-report",
            ),
            article(
                "scam-accounts",
                "OpenAI blocks Cambodian accounts used for investment scams",
                "The company disabled accounts after identifying abusive activity.",
                "https://example.com/scam-accounts",
            ),
            article(
                "product-platform",
                "Electrovaya launches high-power energy storage platform",
                "The new product is designed for data centers.",
                "https://example.com/product-platform",
            ),
            article(
                "shopping-survey",
                "Survey finds two-thirds of consumers plan to use AI for gift shopping",
                "The consumer survey covered product comparison and recommendations.",
                "https://example.com/shopping-survey",
            ),
            article(
                "biofuel-forecast",
                "Global biofuel demand forecast to grow 30% in 2026",
                "Demand is expected to grow because of energy security concerns.",
                "https://example.com/biofuel-forecast",
            ),
            article(
                "youtube-warning",
                "Indonesia warns YouTube over vaping promotion involving children",
                "The ministry requested an answer within three days but announced no sanction.",
                "https://example.com/youtube-warning",
            ),
            article(
                "discussion-only-meeting",
                "White House holds meeting with major AI companies",
                "Officials discussed regulation and competition but announced no decision.",
                "https://example.com/discussion-only-meeting",
            ),
            article(
                "company-spin",
                "Ford calls 10.2% sales decline a good month",
                "The company described the decline as consistent with its strategy.",
                "https://example.com/company-spin",
            ),
        ]
        material_articles = [
            article(
                "medical-device-rule",
                "Canada finalizes new medical device licensing requirements",
                "The binding regulatory changes take effect in December.",
                "https://example.com/medical-device-rule",
            ),
            article(
                "earnings-guidance",
                "Novo Nordisk reports second-quarter sales and raises annual guidance",
                "The company released results and raised its full-year outlook.",
                "https://example.com/earnings-guidance",
            ),
        ]
        generator = FakeGenerator("[]")

        select_and_summarize([*low_value_articles, *material_articles], generator)

        for item in low_value_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        for item in material_articles:
            self.assertIn(item["provider_article_id"], generator.prompts[0])

    def test_excludes_minor_payment_rollouts_market_moves_and_future_plans_before_ai(self):
        low_value_articles = [
            article(
                "retailer-crypto-payment",
                "Dubai Duty Free introduces Crypto.com Pay",
                "Eligible shoppers can use cryptocurrency at airport stores and online.",
                "https://example.com/retailer-crypto-payment",
            ),
            article(
                "routine-index-rise",
                "Pakistan stock index jumps 2,581 points",
                "The KSE-100 rose 1.46% on improved risk appetite and refinancing hopes.",
                "https://example.com/routine-index-rise",
            ),
            article(
                "recycling-executive-pay",
                "Irish recycling body raises executive and board pay 16%",
                "Re-Turn increased compensation for its chief executive and board members.",
                "https://example.com/recycling-executive-pay",
            ),
            article(
                "scheduled-starship-test",
                "SpaceX plans Starship tower-catch test later this month",
                "The company will attempt the test during a future flight.",
                "https://example.com/scheduled-starship-test",
            ),
            article(
                "organoid-guideline-goal",
                "Korea aims for OECD adoption of organoid toxicity test by 2028",
                "The method has not yet been adopted or approved.",
                "https://example.com/organoid-guideline-goal",
            ),
        ]
        material_articles = [
            article(
                "official-unemployment",
                "New Zealand unemployment reaches 11-year high",
                "The official unemployment rate rose to 5.6%.",
                "https://example.com/official-unemployment",
            ),
            article(
                "government-intervention",
                "Government intervenes in foreign exchange market",
                "Officials confirmed direct market intervention.",
                "https://example.com/government-intervention",
            ),
        ]
        generator = FakeGenerator("[]")

        select_and_summarize([*low_value_articles, *material_articles], generator)

        for item in low_value_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        for item in material_articles:
            self.assertIn(item["provider_article_id"], generator.prompts[0])

    def test_excludes_consumer_product_launches_without_commercial_results_before_ai(self):
        product_articles = [
            article(
                "limited-whisky",
                "Aston Martin and Glenfiddich launch limited 16-year whisky",
                "The brands unveiled a limited-edition bottle.",
                "https://example.com/limited-whisky",
            ),
            article(
                "beer-bottles",
                "Goose Island launches six new bottles",
                "The brewer unveiled six bourbon-barrel product variants.",
                "https://example.com/beer-bottles",
            ),
            article(
                "future-car",
                "Mercedes-AMG unveils 2027 GT 53 four-door model",
                "The future model has 536 horsepower and a 500-mile range.",
                "https://example.com/future-car",
            ),
            article(
                "ford-name-price",
                "Ford unveils Fathom electric pickup name and $28,350 price",
                "Ford announced the model name and starting price but no sales or production result.",
                "https://example.com/ford-name-price",
            ),
        ]
        factory_halt = article(
            "honda-halt",
            "Honda halts three plants after earthquake damages supplier",
            "Honda stopped production at three factories after supply disruption.",
            "https://example.com/honda-halt",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([*product_articles, factory_halt], generator)

        for item in product_articles:
            self.assertNotIn(item["provider_article_id"], generator.prompts[0])
        self.assertIn("honda-halt", generator.prompts[0])

    def test_keeps_announced_company_pricing_changes_before_ai(self):
        baggage_fee = article(
            "future-baggage-fee",
            "Jetstar to charge baggage fee from February 2027",
            "The airline will charge passengers up to $37 for baggage next year.",
            "https://example.com/future-baggage-fee",
        )
        airline_results = article(
            "airline-results",
            "Jetstar reports quarterly profit increase",
            "The airline reported new quarterly revenue and profit.",
            "https://example.com/airline-results",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([baggage_fee, airline_results], generator)

        self.assertIn("future-baggage-fee", generator.prompts[0])
        self.assertIn("airline-results", generator.prompts[0])

    def test_excludes_routine_currency_stability_without_a_new_action_before_ai(self):
        stable_rand = article(
            "rand-steady",
            "South African rand holds steady on Iran talks hopes",
            "The rand remained stable as investors watched diplomatic talks.",
            "https://example.com/rand-steady",
        )
        intervention = article(
            "rand-intervention",
            "South African central bank intervenes in currency market",
            "The central bank announced direct market intervention today.",
            "https://example.com/rand-intervention",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([stable_rand, intervention], generator)

        self.assertNotIn("rand-steady", generator.prompts[0])
        self.assertIn("rand-intervention", generator.prompts[0])

    def test_keeps_material_currency_instability_before_ai(self):
        unstable_currency = article(
            "currency-unstable",
            "Currency becomes unstable during a banking panic",
            "The currency became unstable as deposit withdrawals accelerated.",
            "https://example.com/currency-unstable",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([unstable_currency], generator)

        self.assertIn("currency-unstable", generator.prompts[0])

    def test_excludes_vague_expert_risk_warning_without_new_evidence_before_ai(self):
        warning = article(
            "ai-risk-warning",
            "Hinton warns AI agents could cause more cyberattacks",
            "In an interview, Hinton said future AI misuse may increase risk.",
            "https://example.com/ai-risk-warning",
        )
        test_result = article(
            "ai-test-result",
            "UK institute publishes new AI agent security test results",
            "The official laboratory reported newly observed test behavior.",
            "https://example.com/ai-test-result",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([warning, test_result], generator)

        self.assertNotIn("ai-risk-warning", generator.prompts[0])
        self.assertIn("ai-test-result", generator.prompts[0])

    def test_keeps_concrete_company_penalties_before_ai(self):
        scooter_fine = article(
            "rome-scooter-fine",
            "Rome fines Lime Bird and Dott over e-scooter services",
            "The city imposed a EUR 2.675 million fine on three scooter operators.",
            "https://example.com/rome-scooter-fine",
        )
        national_fine = article(
            "national-antitrust-fine",
            "National regulator fines major banks for price fixing",
            "The regulator imposed a binding nationwide antitrust penalty.",
            "https://example.com/national-antitrust-fine",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([scooter_fine, national_fine], generator)

        self.assertIn("rome-scooter-fine", generator.prompts[0])
        self.assertIn("national-antitrust-fine", generator.prompts[0])

    def test_excludes_impersonation_scam_advisory_without_enforcement_before_ai(self):
        scam_advisory = article(
            "mica-scam-warning",
            "EU regulator warns of MiCA impersonation scams",
            "Fraudsters impersonated regulators, but no enforcement action or material loss was reported.",
            "https://example.com/mica-scam-warning",
        )
        enforcement = article(
            "mica-enforcement",
            "EU regulator closes unregistered crypto firms under MiCA",
            "The regulator ordered the firms to close and froze assets today.",
            "https://example.com/mica-enforcement",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([scam_advisory, enforcement], generator)

        self.assertNotIn("mica-scam-warning", generator.prompts[0])
        self.assertIn("mica-enforcement", generator.prompts[0])

    def test_excludes_internally_conflicting_revenue_growth_series_before_ai(self):
        conflicting = article(
            "ddn-conflicting-growth",
            "DDN expects 2026 revenue of $1 billion on surging AI demand",
            "Revenue rose from $400 million in 2024 to $5 billion in 2025 and is expected to reach $1 billion in 2026.",
            "https://example.com/ddn-conflicting-growth",
        )
        coherent = article(
            "ddn-coherent-growth",
            "Storage company expects 2026 revenue of $1 billion",
            "Revenue rose from $400 million in 2024 to $500 million in 2025 and is expected to reach $1 billion in 2026.",
            "https://example.com/ddn-coherent-growth",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([conflicting, coherent], generator)

        self.assertNotIn("ddn-conflicting-growth", generator.prompts[0])
        self.assertIn("ddn-coherent-growth", generator.prompts[0])

    def test_excludes_repackaged_old_event_but_keeps_new_followup_and_future_effective_date(self):
        stale_event = article(
            "stale-difc-rule",
            "DIFC corporate regulation reform expands global access",
            "On July 24, 2026, DIFC amended its corporate regulations.",
            "https://example.com/stale-difc-rule",
        )
        stale_event["published_at"] = "2026-08-03T01:00:00+00:00"
        new_followup = article(
            "new-difc-enforcement",
            "DIFC begins enforcement of corporate regulation reform today",
            "Enforcement started today for rules adopted on July 24, 2026.",
            "https://example.com/new-difc-enforcement",
        )
        new_followup["published_at"] = "2026-08-03T01:00:00+00:00"
        future_effective_date = article(
            "canada-device-rule",
            "Canada finalizes medical device licensing reform",
            "The final rule takes effect on December 14, 2026.",
            "https://example.com/canada-device-rule",
        )
        future_effective_date["published_at"] = "2026-08-03T01:00:00+00:00"
        quarter_end_date = article(
            "quarter-end-date",
            "Company reports second-quarter earnings",
            "2026년 6월 30일 기준 분기 매출과 순이익을 발표했습니다.",
            "https://example.com/quarter-end-date",
        )
        quarter_end_date["published_at"] = "2026-08-03T01:00:00+00:00"
        generator = FakeGenerator("[]")

        select_and_summarize(
            [stale_event, new_followup, future_effective_date, quarter_end_date],
            generator,
        )

        self.assertNotIn("stale-difc-rule", generator.prompts[0])
        self.assertIn("new-difc-enforcement", generator.prompts[0])
        self.assertIn("canada-device-rule", generator.prompts[0])
        self.assertIn("quarter-end-date", generator.prompts[0])

    def test_excludes_investor_letter_commentary_before_ai(self):
        investor_letter = article(
            "ice-investor-letter",
            "Emerald Wealth Partners discusses ICE in second-quarter investor letter",
            "The fund reported a 0.8% net return and described ICE as resilient.",
            "https://example.com/ice-investor-letter",
        )
        earnings = article(
            "ice-earnings",
            "ICE reports second-quarter earnings",
            "ICE reported quarterly revenue and net income.",
            "https://example.com/ice-earnings",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([investor_letter, earnings], generator)

        self.assertNotIn("ice-investor-letter", generator.prompts[0])
        self.assertIn("ice-earnings", generator.prompts[0])

    def test_excludes_tentative_corporate_plans_without_a_decision_before_ai(self):
        cfe_plan = article(
            "cfe-vehicle-plan",
            "Mexico's CFE considers gas-generation-backed investment vehicle",
            "The state utility said it could launch the vehicle around 2027.",
            "https://example.com/cfe-vehicle-plan",
        )
        disney_plan = article(
            "disney-streaming-plan",
            "Disney considers free streaming service",
            "The company is exploring an ad-supported service but has made no decision.",
            "https://example.com/disney-streaming-plan",
        )
        approved_sale = article(
            "approved-sale",
            "Regulator approves sale of national power assets",
            "The regulator approved the binding asset sale today.",
            "https://example.com/approved-sale",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([cfe_plan, disney_plan, approved_sale], generator)

        self.assertNotIn("cfe-vehicle-plan", generator.prompts[0])
        self.assertNotIn("disney-streaming-plan", generator.prompts[0])
        self.assertIn("approved-sale", generator.prompts[0])

    def test_excludes_nonbinding_guidelines_and_minor_codeshare_expansion_before_ai(self):
        trade_group_guidelines = article(
            "nai-guidelines",
            "NAI releases advertising AI guidelines",
            "The trade association issued voluntary guidance urging industry compliance.",
            "https://example.com/nai-guidelines",
        )
        codeshare = article(
            "airline-codeshare",
            "Emirates and South African Airways expand codeshare to nine routes",
            "The airlines added nine routes to their existing codeshare partnership.",
            "https://example.com/airline-codeshare",
        )
        regulator_rule = article(
            "regulator-rule",
            "Regulator adopts binding rules for AI advertising",
            "The regulator adopted enforceable disclosure requirements today.",
            "https://example.com/regulator-rule",
        )
        generator = FakeGenerator("[]")

        select_and_summarize(
            [trade_group_guidelines, codeshare, regulator_rule],
            generator,
        )

        self.assertNotIn("nai-guidelines", generator.prompts[0])
        self.assertNotIn("airline-codeshare", generator.prompts[0])
        self.assertIn("regulator-rule", generator.prompts[0])

    def test_excludes_court_skepticism_without_a_ruling_before_ai(self):
        court_comment = article(
            "court-doubts-plan",
            "Court questions TG Jones restructuring plan",
            "The judge expressed serious doubts but issued no ruling or order.",
            "https://example.com/court-doubts-plan",
        )
        court_order = article(
            "court-protection-order",
            "Court grants Goodfood creditor protection order",
            "The court granted a creditor protection order for debt restructuring.",
            "https://example.com/court-protection-order",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([court_comment, court_order], generator)

        self.assertNotIn("court-doubts-plan", generator.prompts[0])
        self.assertIn("court-protection-order", generator.prompts[0])

    def test_excludes_retrospective_stock_performance_without_a_new_catalyst_before_ai(self):
        retrospective = article(
            "goldman-stock-history",
            "Goldman Sachs stock has surged 106% since 2025",
            "The article reviews the stock's past performance without a new company event.",
            "https://example.com/goldman-stock-history",
        )
        market_event = article(
            "goldman-earnings",
            "Goldman Sachs reports second-quarter earnings",
            "The bank reported new quarterly revenue and net income figures.",
            "https://example.com/goldman-earnings",
        )
        generator = FakeGenerator("[]")

        select_and_summarize([retrospective, market_event], generator)

        self.assertNotIn("goldman-stock-history", generator.prompts[0])
        self.assertIn("goldman-earnings", generator.prompts[0])

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

    def test_rejects_same_intervention_story_when_only_the_market_quote_changed(self):
        source = article(
            "yen-stable-followup",
            "Yen holds steady after historic joint intervention",
            "The yen held near 157.72 per dollar after Japan and the US bought yen.",
            "https://example.com/yen-stable-followup",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "yen-stable-followup",
                "source_title": "Yen holds steady after historic joint intervention",
                "title": "일본 엔화, 공동 개입 후 안정세 유지",
                "content": "미국과 일본의 공동 매수 개입 이후 엔화가 달러당 157.72엔 선에서 안정세를 유지했습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "follow_up",
                "selection_reason": "공동 개입 이후 엔화 시세가 안정세를 유지했습니다."
            }
        ]"""
        recent_news = [
            {
                "title": "일본 엔화, 미·일 공동 개입 후 변동성 완화되며 안정세",
                "content": "미국과 일본의 공동 매수 개입 이후 엔화가 달러당 157.55엔 선에서 안정세를 보였습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

    def test_deduplicates_same_ai_safety_test_with_different_headline_wording(self):
        source = article(
            "aisi-test-followup",
            "UK security institute finds flaws in OpenAI and Anthropic agents",
            "AISI tests found that OpenAI and Anthropic agents performed unauthorized actions.",
            "https://example.com/aisi-test-followup",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "aisi-test-followup",
                "source_title": "UK security institute finds flaws in OpenAI and Anthropic agents",
                "title": "영국 보안연구소, OpenAI·안스로픽 AI 보안 결함 발견",
                "content": "영국 인공지능 보안연구소의 시험에서 오픈AI와 안스로픽 에이전트의 승인되지 않은 동작이 발견됐습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "영국 연구소가 AI 에이전트 시험 결과를 공개했습니다."
            }
        ]"""
        recent_news = [
            {
                "title": "영국 AI 보안 테스트 중 모델 오작동 발생",
                "content": "영국 인공지능 보안연구소의 시험에서 오픈AI와 안스로픽 모델의 문제 행동이 관찰됐습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

    def test_deduplicates_same_korean_tech_selloff_across_market_angles(self):
        market_articles = [
            article(
                "kospi-selloff",
                "Wall Street tech selloff sends KOSPI down 4.6%",
                "The KOSPI fell 4.6% to 6,296.38 as US technology shares declined.",
                "https://example.com/kospi-selloff",
            ),
            article(
                "asia-tech-selloff",
                "SK Hynix slides 9.71% as Asian tech shares follow Wall Street lower",
                "Samsung Electronics fell 6.13% as the US technology selloff spread to Korea.",
                "https://example.com/asia-tech-selloff",
            ),
            article(
                "ai-stocks-selloff",
                "AI stocks reverse as KOSPI falls 4%",
                "Korean AI-related shares declined during the same technology selloff.",
                "https://example.com/ai-stocks-selloff",
            ),
        ]
        response = """[
            {
                "temp_id": 0,
                "source_ref": "kospi-selloff",
                "source_title": "Wall Street tech selloff sends KOSPI down 4.6%",
                "title": "월스트리트 기술주 하락으로 코스피 4.6% 급락",
                "content": "미국 기술주 매도세가 확산되며 코스피가 4.6% 하락한 6,296.38을 기록했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "breaking",
                "selection_reason": "같은 날 미국 기술주 하락이 한국 증시로 확산됐습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "asia-tech-selloff",
                "source_title": "SK Hynix slides 9.71% as Asian tech shares follow Wall Street lower",
                "title": "SK하이닉스 9.71% 급락 등 아시아 기술주 하락",
                "content": "미국 기술주 하락 여파로 SK하이닉스가 9.71%, 삼성전자가 6.13% 하락했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "breaking",
                "selection_reason": "같은 기술주 매도세가 아시아 시장에 확산됐습니다."
            },
            {
                "temp_id": 2,
                "source_ref": "ai-stocks-selloff",
                "source_title": "AI stocks reverse as KOSPI falls 4%",
                "title": "AI 관련주, 코스피 4% 하락과 함께 약세",
                "content": "같은 기술주 매도세로 한국 코스피와 AI 관련주가 4% 안팎 하락했습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "같은 날 한국 기술주가 동반 하락했습니다."
            }
        ]"""

        selected = select_and_summarize(market_articles, FakeGenerator(response))

        self.assertEqual(len(selected), 1)

    def test_deduplicates_same_company_quarterly_results_and_dividend_angle(self):
        source = article(
            "dbs-dividend-angle",
            "DBS raises dividend to 81 cents as second-quarter profit rises 9%",
            "DBS reported second-quarter net profit and declared an 81-cent dividend.",
            "https://example.com/dbs-dividend-angle",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "dbs-dividend-angle",
                "source_title": "DBS raises dividend to 81 cents as second-quarter profit rises 9%",
                "title": "DBS, 2분기 이익 증가에 81센트 배당",
                "content": "DBS가 2분기 순이익이 9% 증가하며 81센트 배당을 선언했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "follow_up",
                "selection_reason": "DBS의 같은 2분기 실적과 배당이 발표됐습니다."
            }
        ]"""
        recent_news = [
            {
                "title": "DBS 2분기 순이익 9% 증가, 30억8천만 싱가포르달러 기록",
                "content": "DBS가 2분기 순이익 30억8천만 싱가포르달러를 기록해 전년보다 9% 증가했습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

    def test_deduplicates_same_government_industrial_ai_policy(self):
        source = article(
            "industry-ai-policy",
            "Korea announces AI manufacturing innovation for major industries",
            "The government plan covers steel, shipbuilding, autos, physical AI and humanoids.",
            "https://example.com/industry-ai-policy",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "industry-ai-policy",
                "source_title": "Korea announces AI manufacturing innovation for major industries",
                "title": "정부, 주력산업 AI 제조 혁신·피지컬 AI 도입 발표",
                "content": "한국 정부가 철강·조선·자동차 등 주력산업에 AI 공정과 피지컬 AI를 도입한다고 발표했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "같은 산업 AI 전환 계획의 세부 내용입니다."
            }
        ]"""
        recent_news = [
            {
                "title": "정부, 주력 산업 AI 전환 및 휴머노이드 개발 추진",
                "content": "한국 정부가 철강·조선·자동차 등 10대 주력산업에 AI를 접목하고 특화 휴머노이드를 개발한다고 발표했습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

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

        self.assertIn("RECENT 24-HOUR NEWS - DUPLICATE COMPARISON ONLY", generator.prompts[0])
        self.assertIn("Use recent news only for duplicate comparison", generator.prompts[0])
        self.assertIn("never as a factual source", generator.prompts[0])

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

    def test_deduplicates_spaced_and_compact_titles_for_the_same_market_record(self):
        source = article(
            "us-record-high-followup",
            "US stocks hit record high as AI earnings rise and oil falls",
            "The S&P 500 rose 1.8% to an all-time high.",
            "https://example.com/us-record-high-followup",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "us-record-high-followup",
                "source_title": "US stocks hit record high as AI earnings rise and oil falls",
                "title": "미국 주식시장, AI 이익·유가 하락으로 사상 최고치",
                "content": "S&P 500이 1.8% 상승하며 사상 최고치를 기록했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "미국 증시가 사상 최고치를 기록했습니다."
            }
        ]"""
        recent_news = [
            {
                "title": "미국주식시장 역사적 고점",
                "content": "S&P 500이 1.8% 상승하며 역사적 최고치를 기록했습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

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

    def test_deduplicates_labor_release_across_employment_unemployment_and_jobs_angles(self):
        sources = [
            article(
                "employment-angle",
                "US month 7 employment report puts unemployment at 4.2%",
                "The official month 7 employment report put unemployment at 4.2%.",
                "https://example.com/employment-angle",
            ),
            article(
                "unemployment-angle",
                "US month 7 unemployment rate is 4.2%",
                "The official month 7 unemployment rate was 4.2%.",
                "https://example.com/unemployment-angle",
            ),
            article(
                "jobs-angle-complete",
                "US month 7 jobs report includes a 4.2% jobless rate",
                "The Labor Department month 7 jobs report included a 4.2% jobless rate and broader labor details.",
                "https://example.com/jobs-angle-complete",
            ),
        ]
        sources[2]["raw_content"] = (
            "The complete Labor Department release included the month 7 jobs "
            "figures, the 4.2% jobless rate, and supporting labor-market details."
        )
        decisions = [
            {
                "temp_id": 0,
                "source_ref": "employment-angle",
                "source_title": sources[0]["raw_title"],
                "title": "미국 7월 고용지표 발표",
                "content": "미국 7월 고용지표에서 실업률이 4.2%로 발표됐습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "미국의 월간 고용지표가 발표됐습니다.",
            },
            {
                "temp_id": 1,
                "source_ref": "unemployment-angle",
                "source_title": sources[1]["raw_title"],
                "title": "미국 7월 실업률 4.2%",
                "content": "미국 7월 실업률이 4.2%로 발표됐습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "미국의 월간 실업률이 발표됐습니다.",
            },
            {
                "temp_id": 2,
                "source_ref": "jobs-angle-complete",
                "source_title": sources[2]["raw_title"],
                "title": "미국 7월 일자리 보고서 발표",
                "content": "미 노동부가 7월 일자리 보고서에서 구직자 비율을 4.2%로 발표했습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "미국의 월간 일자리 보고서가 발표됐습니다.",
            },
        ]

        selected = select_and_summarize(
            sources,
            FakeGenerator(json.dumps(decisions, ensure_ascii=False)),
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["jobs-angle-complete"],
        )

    def test_keeps_labor_stories_when_country_month_or_key_number_differs(self):
        cases = (
            (
                ("미국", "US", 7, "4.2"),
                ("캐나다", "Canada", 7, "4.2"),
            ),
            (
                ("미국", "US", 7, "4.2"),
                ("미국", "US", 8, "4.2"),
            ),
            (
                ("미국", "US", 7, "4.2"),
                ("미국", "US", 7, "4.3"),
            ),
        )

        for case_index, pair in enumerate(cases):
            with self.subTest(case_index=case_index):
                sources = []
                decisions = []
                for index, (country_ko, country_en, month, value) in enumerate(pair):
                    article_id = f"labor-{case_index}-{index}"
                    raw_title = (
                        f"{country_en} month {month} unemployment rate is {value}%"
                    )
                    sources.append(
                        article(
                            article_id,
                            raw_title,
                            f"The official month {month} unemployment rate was {value}%.",
                            f"https://example.com/{article_id}",
                        )
                    )
                    decisions.append(
                        {
                            "temp_id": index,
                            "source_ref": article_id,
                            "source_title": raw_title,
                            "title": f"{country_ko} {month}월 실업률 {value}%",
                            "content": (
                                f"{country_ko} {month}월 실업률이 {value}%로 발표됐습니다."
                            ),
                            "importance_score": 8,
                            "category": "indicator",
                            "news_type": "official_announcement",
                            "selection_reason": f"{country_ko}의 월간 실업률이 발표됐습니다.",
                        }
                    )

                selected = select_and_summarize(
                    sources,
                    FakeGenerator(json.dumps(decisions, ensure_ascii=False)),
                )

                self.assertEqual(len(selected), 2)

    def test_keeps_revised_official_labor_figure_as_material_follow_up(self):
        source = article(
            "revised-labor-rate",
            "US month 7 unemployment rate revised to 4.1%",
            "The official month 7 unemployment rate was revised to 4.1%.",
            "https://example.com/revised-labor-rate",
        )
        decision = {
            "temp_id": 0,
            "source_ref": "revised-labor-rate",
            "source_title": source["raw_title"],
            "title": "미국 7월 실업률 4.1%로 수정",
            "content": "미국 7월 실업률이 기존 4.2%에서 4.1%로 수정됐습니다.",
            "importance_score": 8,
            "category": "indicator",
            "news_type": "follow_up",
            "selection_reason": "미국의 공식 실업률 수치가 수정됐습니다.",
        }
        source["raw_description"] += " The previous estimate was 4.2%."

        selected = select_and_summarize(
            [source],
            FakeGenerator(json.dumps([decision], ensure_ascii=False)),
            recent_news=[
                {
                    "title": "미국 7월 실업률 4.2%",
                    "content": "미국 7월 실업률이 4.2%로 발표됐습니다.",
                }
            ],
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["revised-labor-rate"],
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

    def test_caps_routine_record_and_earnings_but_keeps_market_intervention_breaking(self):
        market_record = article(
            "market-record",
            "S&P 500 rises 1.8% to a record high",
            "The index reached an all-time high after a broad rally.",
            "https://example.com/market-record",
        )
        routine_earnings = article(
            "routine-earnings",
            "SpaceX reports second-quarter revenue above forecasts",
            "The company reported quarterly revenue of KRW 11 trillion.",
            "https://example.com/routine-earnings",
        )
        intervention = article(
            "joint-intervention",
            "United States and Japan conduct joint yen intervention",
            "The two governments intervened directly in the foreign exchange market.",
            "https://example.com/joint-intervention",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "market-record",
                "source_title": "S&P 500 rises 1.8% to a record high",
                "title": "S&P 500, 1.8% 상승해 사상 최고치",
                "content": "S&P 500이 1.8% 상승하며 사상 최고치를 기록했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "미국 대표 지수가 사상 최고치를 기록했습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "routine-earnings",
                "source_title": "SpaceX reports second-quarter revenue above forecasts",
                "title": "스페이스X, 2분기 매출 11조 원 기록",
                "content": "스페이스X가 2분기 매출 11조 원을 기록했다고 발표했습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "분기 실적이 시장 전망을 웃돌았습니다."
            },
            {
                "temp_id": 2,
                "source_ref": "joint-intervention",
                "source_title": "United States and Japan conduct joint yen intervention",
                "title": "미·일 정부, 엔화 방어 공동 시장 개입",
                "content": "미국과 일본 정부가 엔화 방어를 위해 외환시장에 공동 개입했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "breaking",
                "selection_reason": "주요국 정부가 외환시장에 직접 개입했습니다."
            }
        ]"""

        selected = select_and_summarize(
            [market_record, routine_earnings, intervention],
            FakeGenerator(response),
        )

        self.assertEqual(
            {
                item["provider_article_id"]: item["importance_score"]
                for item in selected
            },
            {
                "market-record": 8,
                "routine-earnings": 8,
                "joint-intervention": 9,
            },
        )

    def test_caps_broad_trend_without_action_but_keeps_emergency_governance_event(self):
        trend = article(
            "china-ai-constraint",
            "China AI development constrained by data shortage",
            "New industry data shows a broad shortage is constraining AI development in China.",
            "https://example.com/china-ai-constraint",
        )
        emergency_action = article(
            "central-bank-governor-removed",
            "Government removes central bank governor after emergency meeting",
            "The government removed the central bank governor with immediate effect after an emergency meeting.",
            "https://example.com/central-bank-governor-removed",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "china-ai-constraint",
                "source_title": "China AI development constrained by data shortage",
                "title": "중국 AI 개발, 데이터 부족으로 제약",
                "content": "중국 AI 산업의 개발이 광범위한 데이터 부족으로 제약받고 있습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "중국 AI 산업의 데이터 부족 현황이 새로 보도됐습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "central-bank-governor-removed",
                "source_title": "Government removes central bank governor after emergency meeting",
                "title": "정부, 중앙은행 총재 즉시 해임",
                "content": "정부가 긴급회의 후 중앙은행 총재를 즉시 해임했습니다.",
                "importance_score": 9,
                "category": "policy",
                "news_type": "breaking",
                "selection_reason": "통화정책 거버넌스에 즉각적인 변화가 발생했습니다."
            }
        ]"""

        selected = select_and_summarize(
            [trend, emergency_action],
            FakeGenerator(response),
        )

        self.assertEqual(
            {
                item["provider_article_id"]: item["importance_score"]
                for item in selected
            },
            {
                "china-ai-constraint": 8,
                "central-bank-governor-removed": 9,
            },
        )

    def test_caps_korean_routine_market_record_and_earnings(self):
        market_record = article(
            "korean-market-record",
            "코스피 2.1% 상승해 사상 최고치",
            "코스피 지수가 2.1% 상승하며 사상 최고치를 기록했습니다.",
            "https://example.com/korean-market-record",
        )
        routine_earnings = article(
            "korean-routine-earnings",
            "삼성전자 2분기 매출 80조 원 기록",
            "삼성전자가 2분기 매출 80조 원을 발표했습니다.",
            "https://example.com/korean-routine-earnings",
        )
        market_record["raw_content"] = "코스피가 장중 사상 최고치를 경신했습니다."
        routine_earnings["raw_content"] = "삼성전자가 정기 분기 실적을 발표했습니다."
        response = """[
            {
                "temp_id": 0,
                "source_ref": "korean-market-record",
                "source_title": "코스피 2.1% 상승해 사상 최고치",
                "title": "코스피, 2.1% 상승해 사상 최고치",
                "content": "코스피가 2.1% 상승하며 사상 최고치를 기록했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "국내 대표 지수가 사상 최고치를 기록했습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "korean-routine-earnings",
                "source_title": "삼성전자 2분기 매출 80조 원 기록",
                "title": "삼성전자, 2분기 매출 80조 원 기록",
                "content": "삼성전자가 2분기 매출 80조 원을 기록했습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "삼성전자가 분기 실적을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize(
            [market_record, routine_earnings],
            FakeGenerator(response),
        )

        self.assertEqual(
            [item["importance_score"] for item in selected],
            [8, 8],
        )

    def test_caps_nonfinal_regulatory_review_at_eight(self):
        source = article(
            "sec-listing-delay",
            "SEC delays decision on Nasdaq $5 million listing rule",
            "The SEC postponed approval while it continues reviewing the proposal.",
            "https://example.com/sec-listing-delay",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "sec-listing-delay",
                "source_title": "SEC delays decision on Nasdaq $5 million listing rule",
                "title": "SEC, 나스닥 5백만달러 상장 규칙 승인 보류",
                "content": "미국 증권거래위원회가 최소 시장가치 5백만달러 상장 규칙의 승인을 보류했습니다.",
                "importance_score": 9,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "규제기관이 규칙 승인을 연기했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_caps_ordinary_corporate_acquisition_at_eight(self):
        source = article(
            "ice-marketaxess-deal",
            "ICE agrees to acquire MarketAxess for $5.7 billion",
            "The exchange operator signed an agreement to buy the bond platform.",
            "https://example.com/ice-marketaxess-deal",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "ice-marketaxess-deal",
                "source_title": "ICE agrees to acquire MarketAxess for $5.7 billion",
                "title": "ICE, 마켓액세스 57억달러에 인수",
                "content": "ICE가 전자 채권 거래 플랫폼 마켓액세스를 57억달러에 인수하기로 합의했습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "대형 기업 인수 계약이 체결됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_caps_ordinary_software_acquisition_at_eight(self):
        source = article(
            "software-acquisition",
            "Oracle agrees to acquire a software company",
            "The companies signed an ordinary acquisition agreement.",
            "https://example.com/software-acquisition",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "software-acquisition",
                "source_title": "Oracle agrees to acquire a software company",
                "title": "오라클, 소프트웨어 기업 인수 합의",
                "content": "오라클이 소프트웨어 기업을 인수하기로 합의했습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "일반적인 기업 인수 계약이 체결됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_caps_takeover_deal_at_eight(self):
        source = article(
            "apollo-easyjet-takeover",
            "Apollo reaches $7.7 billion deal to buy easyJet",
            "Apollo agreed a takeover of the airline.",
            "https://example.com/apollo-easyjet-takeover",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "apollo-easyjet-takeover",
                "source_title": "Apollo reaches $7.7 billion deal to buy easyJet",
                "title": "아폴로, 이지젯 77억 달러에 인수",
                "content": "아폴로 글로벌이 영국 저비용 항공사 이지젯을 77억 달러에 인수하기로 합의했습니다.",
                "importance_score": 9,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "항공사 인수 합의가 체결됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_rejects_percentage_growth_without_a_named_metric(self):
        source = article(
            "ambiguous-growth",
            "Arista Networks launches AI platform as quarterly revenue grows 40%",
            "The company said quarterly revenue increased 40%.",
            "https://example.com/ambiguous-growth",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "ambiguous-growth",
                "source_title": "Arista Networks launches AI platform as quarterly revenue grows 40%",
                "title": "아리스타 네트워크, AI 플랫폼 출시로 실적 호조",
                "content": "아리스타 네트워크가 실적에서 40% 성장률을 기록했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "회사가 신규 플랫폼과 분기 실적을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_fund_return_attributed_to_a_portfolio_company(self):
        source = article(
            "fund-return-attribution",
            "Norway wealth fund returns 0.8% in second quarter and discusses ICE holding",
            "The sovereign wealth fund reported a 0.8% second-quarter net return; ICE was one holding discussed.",
            "https://example.com/fund-return-attribution",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "fund-return-attribution",
                "source_title": "Norway wealth fund returns 0.8% in second quarter and discusses ICE holding",
                "title": "ICE, 시장 변동성에도 견고한 구조",
                "content": "ICE가 2분기에 0.8% 순수익을 기록했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "분기 투자 성과가 공개됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_keeps_fund_return_when_the_source_actor_is_preserved(self):
        source = article(
            "fund-return-correct",
            "Norway wealth fund returns 0.8% in the second quarter",
            "The sovereign wealth fund reported a 0.8% net return for the quarter.",
            "https://example.com/fund-return-correct",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "fund-return-correct",
                "source_title": "Norway wealth fund returns 0.8% in the second quarter",
                "title": "노르웨이 국부펀드, 2분기 0.8% 수익",
                "content": "노르웨이 국부펀드가 2분기에 0.8% 순수익률을 기록했습니다.",
                "importance_score": 7,
                "category": "market",
                "news_type": "official_announcement",
                "selection_reason": "국부펀드가 새 분기 수익률을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["fund-return-correct"],
        )

    def test_normalizes_jobless_claim_count_as_level_not_increase_amount(self):
        source = article(
            "us-jobless-claims",
            "US weekly jobless claims rise to 199,000",
            "Initial applications increased to 199,000 from 195,000 last week.",
            "https://example.com/us-jobless-claims",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "us-jobless-claims",
                "source_title": "US weekly jobless claims rise to 199,000",
                "title": "미국 실업급여 신청 199,000명 증가",
                "content": "미국 주간 실업급여 신청이 199,000명으로 증가했습니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "미국의 새 주간 실업급여 신청 건수가 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "미국 실업급여 신청 199,000건으로 증가",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "미국 주간 실업급여 신청이 199,000건으로 증가했습니다.",
        )

    def test_normalizes_jobless_claim_count_below_threshold_as_cases(self):
        source = article(
            "jobless-below-200k",
            "US jobless claims stay below 200,000 for third week",
            "Initial jobless claims remained below 20만 (200,000) for a third week.",
            "https://example.com/jobless-below-200k",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "jobless-below-200k",
                "source_title": "US jobless claims stay below 200,000 for third week",
                "title": "미국 신규 실업수당 청구 3주 연속 20만명 하회",
                "content": "미국 신규 실업수당 청구 건수가 3주 연속 20만명을 밑돌았습니다.",
                "importance_score": 7,
                "category": "indicator",
                "news_type": "new_development",
                "selection_reason": "미국의 새 주간 고용 지표가 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "미국 신규 실업수당 청구 3주 연속 20만건 하회",
        )
        self.assertIn("20만건", selected[0]["normalized_content"])

    def test_rejects_incomplete_generated_title(self):
        source = article(
            "rwe-incomplete-title",
            "RWE agrees $1.22 billion deal to cancel US offshore wind leases",
            "RWE will cancel three leases and redirect the funds to gas projects.",
            "https://example.com/rwe-incomplete-title",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "rwe-incomplete-title",
                "source_title": "RWE agrees $1.22 billion deal to cancel US offshore wind leases",
                "title": "RWE, 미국 해상풍력 임대 취소하고 12억2천만 달러 규모 가",
                "content": "독일 에너지 기업 RWE가 미국 해상풍력 임대 3건을 취소하는 12억2천만 달러 규모 계약을 체결했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "에너지 자산 운용 계획이 변경됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_structurally_truncated_titles_without_blocking_valid_numbers(self):
        cases = (
            (
                "ai-adoption-truncated",
                "Company signs agreement to introduce AI",
                "The company signed a binding agreement to introduce AI.",
                "기업, AI 도입을위",
                "기업이 AI 도입을 위한 구속력 있는 계약을 체결했습니다.",
            ),
            (
                "prince-group-truncated",
                "US names Prince Group associates in enforcement action",
                "The US government named associates tied to Prince Group.",
                "미국, 프린스 그룹 관련자 제",
                "미국 정부가 프린스 그룹 관련자들을 제재했습니다.",
            ),
            (
                "tax-burden-truncated",
                "Tax reform reduces household burden by 1 trillion won",
                "The reform reduces the household tax burden by 1 trillion won.",
                "세제개편으로 가계 세부담 1",
                "세제개편으로 가계 세부담이 1조원 감소합니다.",
            ),
            (
                "apartment-price-truncated",
                "Apartment price growth slows to 0.09%",
                "Apartment price growth slowed to 0.09% this week.",
                "아파트값 상승폭 0.09%로",
                "아파트값 상승폭이 0.09%로 둔화했습니다.",
            ),
        )

        for article_id, raw_title, description, title, content in cases:
            with self.subTest(article_id=article_id):
                source = article(
                    article_id,
                    raw_title,
                    description,
                    f"https://example.com/{article_id}",
                )
                response = json.dumps(
                    [
                        {
                            "temp_id": 0,
                            "source_ref": article_id,
                            "source_title": raw_title,
                            "title": title,
                            "content": content,
                            "importance_score": 7,
                            "category": "corporate",
                            "news_type": "official_announcement",
                            "selection_reason": "새로운 경제 사실이 발표됐습니다.",
                        }
                    ],
                    ensure_ascii=False,
                )

                selected = select_and_summarize([source], FakeGenerator(response))

                self.assertEqual(selected, [])

    def test_keeps_complete_titles_that_end_with_meaningful_numbers(self):
        sources = [
            article(
                "sp500-record",
                "S&P 500 reaches a new record",
                "The S&P 500 reached a new record.",
                "https://example.com/sp500-record",
            ),
            article(
                "seven-models",
                "Automaker launches 7 new models",
                "The company launched 7 new vehicle models.",
                "https://example.com/seven-models",
            ),
        ]
        response = """[
            {
                "temp_id": 0,
                "source_ref": "sp500-record",
                "source_title": "S&P 500 reaches a new record",
                "title": "S&P 500 사상 최고",
                "content": "S&P 500 지수가 사상 최고치를 기록했습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "미국 대표 지수가 사상 최고치를 기록했습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "seven-models",
                "source_title": "Automaker launches 7 new models",
                "title": "자동차업체, 신차 7종 출시",
                "content": "자동차업체가 새로운 차량 7종을 출시했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "자동차업체가 신차 7종을 출시했습니다."
            }
        ]"""

        selected = select_and_summarize(sources, FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["sp500-record", "seven-models"],
        )

    def test_rejects_opposite_title_and_summary_direction(self):
        source = article(
            "opposite-market-direction",
            "US stocks rise after employment report",
            "The major US indexes rose after the report.",
            "https://example.com/opposite-market-direction",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "opposite-market-direction",
                "source_title": "US stocks rise after employment report",
                "title": "미국 증시, 고용지표 발표 후 하락",
                "content": "미국 주요 지수가 고용지표 발표 후 상승했습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "고용지표 발표 후 미국 증시가 움직였습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_keeps_mixed_metric_summary_when_title_direction_is_supported(self):
        source = article(
            "mixed-earnings-direction",
            "Company revenue rises while profit falls",
            "Quarterly revenue increased, but net profit decreased.",
            "https://example.com/mixed-earnings-direction",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "mixed-earnings-direction",
                "source_title": "Company revenue rises while profit falls",
                "title": "기업 매출 증가, 순이익은 감소",
                "content": "기업의 분기 매출은 증가했지만 순이익은 감소했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "기업이 상반된 방향의 분기 실적을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["mixed-earnings-direction"],
        )

    def test_normalizes_source_supported_company_and_energy_terms(self):
        sources = [
            article(
                "chevron-dividend",
                "Chevron expands its dividend",
                "Chevron announced a larger shareholder dividend.",
                "https://example.com/chevron-dividend",
            ),
            article(
                "india-lpg",
                "US expands liquefied petroleum gas LPG shipments to India",
                "US LPG shipments to India expanded.",
                "https://example.com/india-lpg",
            ),
            article(
                "hormuz-shipping",
                "Strait of Hormuz disruption delays oil cargoes",
                "The disruption delayed oil cargoes through the Strait of Hormuz.",
                "https://example.com/hormuz-shipping",
            ),
        ]
        response = """[
            {
                "temp_id": 0,
                "source_ref": "chevron-dividend",
                "source_title": "Chevron expands its dividend",
                "title": "체비론, 배당 확대",
                "content": "체비론이 주주 배당을 확대한다고 발표했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "기업이 배당 확대를 발표했습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "india-lpg",
                "source_title": "US expands liquefied petroleum gas LPG shipments to India",
                "title": "미국, 인도 액화석유가 공급 확대",
                "content": "미국이 인도에 대한 액화석유가(LPG) 공급을 확대했습니다.",
                "importance_score": 7,
                "category": "market",
                "news_type": "new_development",
                "selection_reason": "미국의 인도 에너지 공급이 확대됐습니다."
            },
            {
                "temp_id": 2,
                "source_ref": "hormuz-shipping",
                "source_title": "Strait of Hormuz disruption delays oil cargoes",
                "title": "만유원지 차질로 원유 운송 지연",
                "content": "만유원지 운송 차질로 원유 화물 운송이 지연됐습니다.",
                "importance_score": 8,
                "category": "geopolitics",
                "news_type": "new_development",
                "selection_reason": "주요 원유 운송 경로에 차질이 발생했습니다."
            }
        ]"""

        selected = select_and_summarize(sources, FakeGenerator(response))

        self.assertEqual(selected[0]["normalized_title"], "셰브론, 배당 확대")
        self.assertIn("액화석유가스(LPG)", selected[1]["normalized_content"])
        self.assertEqual(
            selected[2]["normalized_title"],
            "호르무즈 해협 차질로 원유 운송 지연",
        )

    def test_rejects_repeated_korean_large_number_unit(self):
        source = article(
            "uob-profit",
            "UOB reports quarter 2 profit components of 1, 4 and 8 million Singapore dollars",
            "The bank disclosed quarter 2 profit components of 1, 4 and 8 million Singapore dollars.",
            "https://example.com/uob-profit",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "uob-profit",
                "source_title": "UOB reports quarter 2 profit components of 1, 4 and 8 million Singapore dollars",
                "title": "UOB, 2분기 순이익 증가",
                "content": "UOB의 2분기 순이익이 1억4천만8백만 싱가포르달러로 증가했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "은행이 분기 순이익 증가를 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_marks_unrepaired_selected_quality_failure_retryable(self):
        source = article(
            "retry-title",
            "Company signs agreement to introduce AI",
            "The company signed a binding agreement to introduce AI.",
            "https://example.com/retry-title",
        )
        broken_response = """[{
            "temp_id": 0,
            "source_ref": "retry-title",
            "source_title": "Company signs agreement to introduce AI",
            "title": "기업, AI 도입을위",
            "content": "기업이 AI 도입을 위한 구속력 있는 계약을 체결했습니다.",
            "importance_score": 7,
            "category": "corporate",
            "news_type": "official_announcement",
            "selection_reason": "기업이 AI 도입 계약을 체결했습니다."
        }]"""
        generator = FakeGenerator([broken_response, broken_response])

        result = select_and_summarize([source], generator)

        self.assertEqual(result, [])
        self.assertEqual(
            result.retryable_urls,
            frozenset({source["original_url"]}),
        )
        self.assertEqual(len(generator.prompts), 2)
        repair_prompt = " ".join(generator.prompts[1].split())
        self.assertIn("Correct only the title and summary quality errors", repair_prompt)
        for required_fragment in (
            "Do not reconsider whether the article should be selected",
            "Write title, content, and selection_reason in natural Korean only",
            "Do not convert currencies",
            "million, billion, trillion, M, B, or T",
            "subsidiary, special-purpose vehicle (SPV), or consortium",
            "controlled security test",
            "actor, country, direction, currency, unit, metric, and time basis",
            "If a faithful repair is impossible, omit the item",
        ):
            self.assertIn(required_fragment, repair_prompt)

    def test_selects_article_when_focused_quality_repair_succeeds(self):
        source = article(
            "repaired-title",
            "Company signs agreement to introduce AI",
            "The company signed a binding agreement to introduce AI.",
            "https://example.com/repaired-title",
        )
        broken_response = """[{
            "temp_id": 0,
            "source_ref": "repaired-title",
            "source_title": "Company signs agreement to introduce AI",
            "title": "기업, AI 도입을위",
            "content": "기업이 AI 도입을 위한 구속력 있는 계약을 체결했습니다.",
            "importance_score": 7,
            "category": "corporate",
            "news_type": "official_announcement",
            "selection_reason": "기업이 AI 도입 계약을 체결했습니다."
        }]"""
        repaired_response = """[{
            "temp_id": 0,
            "source_ref": "repaired-title",
            "source_title": "Company signs agreement to introduce AI",
            "title": "기업, AI 도입 계약 체결",
            "content": "기업이 AI 도입을 위한 구속력 있는 계약을 체결했습니다.",
            "importance_score": 7,
            "category": "corporate",
            "news_type": "official_announcement",
            "selection_reason": "기업이 AI 도입 계약을 체결했습니다."
        }]"""
        generator = FakeGenerator([broken_response, repaired_response])

        result = select_and_summarize([source], generator)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["normalized_title"], "기업, AI 도입 계약 체결")
        self.assertEqual(result.retryable_urls, frozenset())
        self.assertEqual(len(generator.prompts), 2)

    def test_rejects_title_number_missing_from_summary_body(self):
        source = article(
            "doordash-number-drift",
            "DoorDash reports second-quarter revenue of $4.45 billion",
            "Revenue rose while gross order value reached $33.1 billion.",
            "https://example.com/doordash-number-drift",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "doordash-number-drift",
                "source_title": "DoorDash reports second-quarter revenue of $4.45 billion",
                "title": "도어대시, 2분기 매출 44.5억 달러 달성",
                "content": "도어대시의 2분기 총주문액이 331억 달러를 기록했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "도어대시가 새 분기 실적을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_normalizes_million_currency_abbreviation_for_korean_readers(self):
        source = article(
            "national-euro-fine",
            "National regulator imposes EUR 2.675M antitrust fine",
            "The regulator imposed a 2.675 million euro binding penalty.",
            "https://example.com/national-euro-fine",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "national-euro-fine",
                "source_title": "National regulator imposes EUR 2.675M antitrust fine",
                "title": "규제기관, 2.675M 유로 과징금 부과",
                "content": "규제기관이 가격 담합에 2.675M 유로의 과징금을 부과했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "구속력 있는 과징금이 새로 부과됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "규제기관, 267만5천 유로 과징금 부과",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "규제기관이 가격 담합에 267만5천 유로의 과징금을 부과했습니다.",
        )

    def test_normalizes_singapore_dollar_amount_and_cents(self):
        source = article(
            "dbs-results-currency",
            "DBS second-quarter profit rises 9% to S$3.08 billion",
            "DBS declared a dividend of 81 Singapore cents per share.",
            "https://example.com/dbs-results-currency",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "dbs-results-currency",
                "source_title": "DBS second-quarter profit rises 9% to S$3.08 billion",
                "title": "DBS 2분기 순이익 9% 증가 S$30.8억 기록",
                "content": "DBS가 2분기 순이익이 9% 증가한 S$30.8억 달러를 기록하고 81센트 배당을 선언했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "DBS의 새 분기 실적과 배당이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "DBS 2분기 순이익 9% 증가 30억8천만 싱가포르달러 기록",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "DBS가 2분기 순이익이 9% 증가한 30억8천만 싱가포르달러를 기록하고 81싱가포르센트 배당을 선언했습니다.",
        )

    def test_normalizes_south_african_rand_name(self):
        source = article(
            "rand-intervention-terms",
            "South African rand rises after central bank intervention",
            "The rand strengthened after the South African Reserve Bank intervened.",
            "https://example.com/rand-intervention-terms",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "rand-intervention-terms",
                "source_title": "South African rand rises after central bank intervention",
                "title": "남아공Rand, 중앙은행 개입 후 상승",
                "content": "남아공Rand은 중앙은행의 외환시장 개입 이후 상승했습니다.",
                "importance_score": 8,
                "category": "market",
                "news_type": "official_announcement",
                "selection_reason": "중앙은행의 새 시장 개입이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "남아프리카공화국 랜드화, 중앙은행 개입 후 상승",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "남아프리카공화국 랜드화는 중앙은행의 외환시장 개입 이후 상승했습니다.",
        )

    def test_rejects_transaction_summary_that_replaces_primary_company_with_history(self):
        source = article(
            "de-beers-wrong-actor",
            "Anglo American plans sale of its 85% De Beers stake",
            "Anglo American is seeking a buyer; the Oppenheimer family was a former owner.",
            "https://example.com/de-beers-wrong-actor",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "de-beers-wrong-actor",
                "source_title": "Anglo American plans sale of its 85% De Beers stake",
                "title": "앵글로 아메리칸, 디비어스 지분 매각 추진",
                "content": "오펜하이머가 디비어스 지분을 매각하고 앙골라 아메리칸이 85%를 인수할 계획입니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "디비어스 지분 매각 계획이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_normalizes_anglo_american_name_when_actor_is_correct(self):
        source = article(
            "de-beers-correct-actor",
            "Anglo American plans sale of its 85% De Beers stake",
            "Anglo American announced plans to sell its controlling stake.",
            "https://example.com/de-beers-correct-actor",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "de-beers-correct-actor",
                "source_title": "Anglo American plans sale of its 85% De Beers stake",
                "title": "앙골라 아메리칸, 디비어스 지분 매각 추진",
                "content": "앙골라 아메리칸이 디비어스 지분 85% 매각을 추진한다고 발표했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "지배주주가 지분 매각 계획을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "앵글로 아메리칸, 디비어스 지분 매각 추진",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "앵글로 아메리칸이 디비어스 지분 85% 매각을 추진한다고 발표했습니다.",
        )

    def test_normalizes_goldman_sachs_name(self):
        source = article(
            "goldman-results",
            "Goldman Sachs reports second-quarter earnings",
            "Goldman Sachs reported new quarterly revenue and net income.",
            "https://example.com/goldman-results",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "goldman-results",
                "source_title": "Goldman Sachs reports second-quarter earnings",
                "title": "골드만 사스, 2분기 실적 발표",
                "content": "골드만 사스가 2분기 매출과 순이익을 발표했습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "새 분기 실적이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["normalized_title"], "골드만삭스, 2분기 실적 발표")
        self.assertEqual(
            selected[0]["normalized_content"],
            "골드만삭스가 2분기 매출과 순이익을 발표했습니다.",
        )

    def test_normalizes_network_advertising_initiative_name(self):
        source = article(
            "nai-agreement",
            "Network Advertising Initiative signs binding data agreement",
            "The NAI signed a binding data agreement with a federal regulator.",
            "https://example.com/nai-agreement",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "nai-agreement",
                "source_title": "Network Advertising Initiative signs binding data agreement",
                "title": "네트워크 광고 주도권, 데이터 계약 체결",
                "content": "네트워크 광고 주도권이 연방 규제기관과 구속력 있는 데이터 계약을 체결했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "구속력 있는 신규 계약이 체결됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "네트워크 광고 이니셔티브(NAI), 데이터 계약 체결",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "네트워크 광고 이니셔티브(NAI)가 연방 규제기관과 구속력 있는 데이터 계약을 체결했습니다.",
        )

    def test_converts_decimal_billion_dollars_to_korean_eok_correctly(self):
        source = article(
            "manulife-results",
            "Manulife reports second-quarter earnings",
            "Core EPS rose 16% and net income increased by $0.3 billion.",
            "https://example.com/manulife-results",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "manulife-results",
                "source_title": "Manulife reports second-quarter earnings",
                "title": "맨라이프, 2026년 2분기 실적 발표",
                "content": "맨라이프의 2026년 2분기 핵심 주당순이익이 16% 증가하고 순이익이 0.3억 달러 늘어났습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "회사가 새 분기 실적을 발표했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_content"],
            "맨라이프의 2026년 2분기 핵심 주당순이익이 16% 증가하고 순이익이 3억 달러 늘어났습니다.",
        )

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

    def test_normalizes_boe_per_day_for_beginner_readers(self):
        source = article(
            "oil-production",
            "Diamondback second-quarter production exceeds 1 million boe/d",
            "The producer raised full-year production guidance.",
            "https://example.com/oil-production",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "oil-production",
                "source_title": "Diamondback second-quarter production exceeds 1 million boe/d",
                "title": "다이아몬드백, 생산량 1백만 boe/d 돌파",
                "content": "다이아몬드백의 2분기 생산량이 1백만 boe/d를 넘었으며 연간 가이던스를 높였습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "생산량과 연간 가이던스가 새로 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "다이아몬드백, 생산량 1백만 석유환산배럴/일 돌파",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "다이아몬드백의 2분기 생산량이 1백만 석유환산배럴/일을 넘었으며 연간 가이던스를 높였습니다.",
        )

    def test_normalizes_maybank_name_and_insurance_distribution_phrase(self):
        source = article(
            "maybank-etiqa",
            "Maybank acquires full ownership of Etiqa insurance business",
            "The deal strengthens bank-led insurance distribution in Malaysia and Singapore.",
            "https://example.com/maybank-etiqa",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "maybank-etiqa",
                "source_title": "Maybank acquires full ownership of Etiqa insurance business",
                "title": "마이칸은행, 에티카 완전 인수",
                "content": "마이칸은행은 에티카를 인수해 말레이시아·싱가포르에서 은행 주도의 배분을 강화했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "은행이 보험 사업의 완전한 소유권을 확보했습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["normalized_title"], "메이뱅크, 에티카 완전 인수")
        self.assertEqual(
            selected[0]["normalized_content"],
            "메이뱅크는 에티카를 인수해 말레이시아·싱가포르에서 은행 채널을 통한 보험 판매를 강화했습니다.",
        )

    def test_normalizes_smrt_profit_name_and_singapore_dollar(self):
        source = article(
            "smrt-profit",
            "SMRT Trains profit doubles to S$12.8 million",
            "After-tax profit doubled while revenue increased 5.6%.",
            "https://example.com/smrt-profit",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "smrt-profit",
                "source_title": "SMRT Trains profit doubles to S$12.8 million",
                "title": "SMRT 기계수익 12.8백만 달러로 두 배",
                "content": "SMRT의 세후 이익이 12.8백만 달러로 두 배 증가하고 매출은 5.6% 늘었습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "철도 운영사의 실적이 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "SMRT 트레인스 순이익 12.8백만 싱가포르달러로 두 배",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "SMRT 트레인스의 세후 이익이 12.8백만 싱가포르달러로 두 배 증가하고 매출은 5.6% 늘었습니다.",
        )

    def test_expands_isr_for_beginner_readers(self):
        source = article(
            "military-isr",
            "Chinese military researchers use US AI models for drones and ISR",
            "Researchers applied the models to drones, cyber warfare, and ISR systems.",
            "https://example.com/military-isr",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "military-isr",
                "source_title": "Chinese military researchers use US AI models for drones and ISR",
                "title": "중국군, 드론·ISR용 AI 개발",
                "content": "중국 군 연구진이 드론과 ISR 시스템 개발에 미국 AI 모델을 활용했습니다.",
                "importance_score": 8,
                "category": "geopolitics",
                "news_type": "new_development",
                "selection_reason": "중국 군 연구진의 미국 AI 모델 활용이 확인됐습니다."
            }
        ]"""

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertIn("정보·감시·정찰(ISR)", selected[0]["normalized_title"])
        self.assertIn("정보·감시·정찰(ISR)", selected[0]["normalized_content"])

    def test_softens_test_environment_wording_and_removes_editorial_indicator_judgment(self):
        safety_test = article(
            "ai-safety-test",
            "UK AI Security Institute observes harmful model actions during tests",
            "Models inserted malicious code and sent spam in controlled evaluations.",
            "https://example.com/ai-safety-test",
        )
        unemployment = article(
            "nz-unemployment",
            "New Zealand unemployment reaches 5.6%, highest in 11 years",
            "The official quarterly unemployment rate increased to 5.6%.",
            "https://example.com/nz-unemployment",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "ai-safety-test",
                "source_title": "UK AI Security Institute observes harmful model actions during tests",
                "title": "영국 AI 보안 테스트 중 모델 오작동",
                "content": "영국 AI 보안 테스트 중 모델이 통제 범위를 벗어난 행동을 보였습니다.",
                "importance_score": 8,
                "category": "corporate",
                "news_type": "new_development",
                "selection_reason": "통제된 평가에서 문제 행동이 관찰됐습니다."
            },
            {
                "temp_id": 1,
                "source_ref": "nz-unemployment",
                "source_title": "New Zealand unemployment reaches 5.6%, highest in 11 years",
                "title": "뉴질랜드 실업률 11년 만에 최고",
                "content": "뉴질랜드 실업률이 11년 만에 최고인 5.6%로 상승했습니다. 이는 노동시장 약화를 보여주는 중요한 지표입니다.",
                "importance_score": 8,
                "category": "indicator",
                "news_type": "official_announcement",
                "selection_reason": "공식 실업률이 새로 발표됐습니다."
            }
        ]"""

        selected = select_and_summarize(
            [safety_test, unemployment],
            FakeGenerator(response),
        )

        self.assertEqual(
            selected[0]["normalized_content"],
            "영국 AI 보안 테스트 중 모델이 시험 환경에서 문제 행동을 보였습니다.",
        )
        self.assertEqual(
            selected[1]["normalized_content"],
            "뉴질랜드 실업률이 11년 만에 최고인 5.6%로 상승했습니다.",
        )

    def test_prompt_contract_keeps_broad_coverage_and_requires_precise_attribution(self):
        source = article(
            "regional-supply-contract",
            "Regional manufacturer signs binding supply agreement",
            "The company signed a new binding contract with a named customer.",
            "https://example.com/regional-supply-contract",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "regional-supply-contract",
                "source_title": "Regional manufacturer signs binding supply agreement",
                "title": "지역 제조사, 신규 공급계약 체결",
                "content": "지역 제조사가 고객사와 구속력 있는 신규 공급계약을 체결했습니다.",
                "importance_score": 7,
                "category": "corporate",
                "news_type": "official_announcement",
                "selection_reason": "기업의 새 공급계약이 체결됐습니다."
            }
        ]"""

        class ContractAwareGenerator:
            def __init__(self):
                self.prompts = []

            def __call__(self, prompt):
                self.prompts.append(prompt)
                normalized_prompt = " ".join(prompt.split())
                required_fragments = (
                    'regardless of company or country size',
                    'Preserve the source-backed actor',
                    'exploration results into confirmed reserves',
                    'controlled security test or authorized evaluation',
                    'affected rule',
                    'Keep every important title number in the content',
                    'ideally <=35 characters and never >55',
                )
                text = response if all(
                    fragment in normalized_prompt for fragment in required_fragments
                ) else "[]"
                return SimpleNamespace(text=text)

        selected = select_and_summarize([source], ContractAwareGenerator())

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["regional-supply-contract"],
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
        self.assertIn("polite news-reporting ending", prompt)
        self.assertIn("TEXT_TOO_SHORT", prompt)
        self.assertIn('"market impact"', prompt)
        self.assertIn("7: meaningful new economic information", prompt)
        self.assertIn("8: a major company result", prompt)
        self.assertIn("Scores 9-10 are reserved", prompt)
        self.assertIn("confirmed, time-sensitive events", prompt)

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

    def test_upgrades_confirmed_joint_fx_intervention_to_urgent(self):
        source = article(
            "confirmed-joint-intervention",
            "US and Japan conduct joint yen-buying intervention",
            "Officials confirmed direct foreign-exchange intervention.",
            "https://example.com/confirmed-joint-intervention",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="미·일, 엔화 방어 위해 공동 시장 개입",
                    content="미국과 일본이 엔화 방어를 위해 외환시장에 공동 개입했습니다.",
                    importance_score=8,
                    category="market",
                    news_type="breaking",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 9)

    def test_does_not_upgrade_speculative_joint_intervention_report(self):
        source = article(
            "possible-joint-intervention",
            "US and Japan may have intervened to support yen",
            "Traders estimated that a joint intervention could have occurred.",
            "https://example.com/possible-joint-intervention",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="미·일, 엔화 방어 위한 공동 개입 가능성",
                    content="미국과 일본이 외환시장에 개입했을 가능성이 제기됐습니다.",
                    importance_score=8,
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_upgrades_confirmed_intervention_when_only_its_effect_is_expected(self):
        source = article(
            "confirmed-intervention-expected-effect",
            "US and Japan conduct joint yen-buying intervention",
            "Officials confirmed the action, which is expected to stabilize the yen.",
            "https://example.com/confirmed-intervention-expected-effect",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="미·일, 엔화 방어 위해 공동 시장 개입",
                    content="미국과 일본이 엔화 방어를 위해 외환시장에 공동 개입했습니다.",
                    importance_score=8,
                    category="market",
                    news_type="breaking",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 9)

    def test_rejects_clear_non_event_analysis_but_keeps_concrete_developments(self):
        rejected = [
            article(
                "stock-return-comparison",
                "Broadcom six-month return of 36% versus Nvidia",
                "A backward-looking comparison of historical stock performance.",
                "https://example.com/comparison",
            ),
            article(
                "business-model-explainer",
                "How McDonald's real-estate business model works",
                "An evergreen explainer with no new transaction or result.",
                "https://example.com/explainer",
            ),
            article(
                "soft-buyer-interest",
                "Middle East buyers show interest in Canadian LNG",
                "Potential buyers expressed interest but no deal or tender was announced.",
                "https://example.com/interest",
            ),
            article(
                "market-preview",
                "Metals rally faces a test from future risks",
                "The market outlook previews risks without a new action or price move.",
                "https://example.com/preview",
            ),
            article(
                "analyst-platform-dispute",
                "OpenAI blocks bitcoin analyst after account dispute",
                "The analyst moved to a Chinese chatbot after the platform dispute.",
                "https://example.com/dispute",
            ),
            article(
                "minor-airport-incident",
                "Aircraft narrowly avoid collision at Sydney airport",
                "There was no shutdown, cancellation, financial impact, or official action.",
                "https://example.com/airport",
            ),
        ]
        kept = [
            article(
                "lng-contract",
                "Buyer signs Canadian LNG supply contract",
                "A binding 20-year supply contract was signed.",
                "https://example.com/contract",
            ),
            article(
                "airport-disruption",
                "Sydney airport closes after collision",
                "The shutdown forced airlines to cancel 200 flights.",
                "https://example.com/disruption",
            ),
        ]
        response = json.dumps(
            [
                decision_for(
                    kept[0],
                    6,
                    title="캐나다 LNG, 20년 공급계약 체결",
                    content="캐나다 LNG 구매자가 20년 장기 공급계약을 체결했습니다.",
                ),
                decision_for(
                    kept[1],
                    7,
                    title="시드니 공항 충돌로 폐쇄·200편 취소",
                    content="시드니 공항이 충돌 사고로 폐쇄돼 항공편 200편이 취소됐습니다.",
                ),
            ],
            ensure_ascii=False,
        )
        generator = FakeGenerator(response)

        selected = select_and_summarize(rejected + kept, generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["lng-contract", "airport-disruption"],
        )
        for source in rejected:
            self.assertNotIn(source["raw_title"], generator.prompts[0])

    def test_normalizes_employment_data_and_share_repurchase_with_source_support(self):
        employment = article(
            "employment-data",
            "US employment data weaken in July",
            "The latest labor-market data showed slower hiring.",
            "https://example.com/employment-data",
        )
        buyback = article(
            "share-repurchase",
            "HG completes share repurchase",
            "The company completed its stock buyback program.",
            "https://example.com/share-repurchase",
        )
        response = json.dumps(
            [
                decision_for(
                    employment,
                    0,
                    title="미국 취업 데이터 약화",
                    content="미국 취업 데이터에서 고용 증가세가 둔화됐습니다.",
                    category="indicator",
                ),
                decision_for(
                    buyback,
                    1,
                    title="에이치지, 주식 매수 회수 완료",
                    content="에이치지가 주식 매수 회수를 완료했습니다.",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize(
            [employment, buyback],
            FakeGenerator(response),
        )

        self.assertEqual(len(selected), 2)
        self.assertIn("고용지표", selected[0]["normalized_title"])
        self.assertIn("고용지표", selected[0]["normalized_content"])
        self.assertIn("자사주 매입", selected[1]["normalized_title"])
        self.assertIn("자사주 매입", selected[1]["normalized_content"])

    def test_retries_definitive_title_when_source_and_summary_are_speculative(self):
        source = article(
            "possible-intervention",
            "US may have intervened in yen market",
            "Dealers estimated intervention could have occurred.",
            "https://example.com/possible-intervention",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="미국, 엔화 방어 위해 시장 개입",
                    content="미국이 엔화 방어에 개입한 것으로 추정됩니다.",
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        result = select_and_summarize(
            [source],
            FakeGenerator([response, response]),
        )

        self.assertEqual(result, [])
        self.assertEqual(
            result.retryable_urls,
            frozenset({source["original_url"]}),
        )

    def test_retries_quantified_trend_summary_that_omits_source_metric(self):
        source = article(
            "marvell-results",
            "Marvell quarterly revenue rises 42% to $2.0 billion",
            "Quarterly revenue increased 42% to $2.0 billion.",
            "https://example.com/marvell-results",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="마벨, 분기 매출 증가",
                    content="마벨의 분기 매출이 증가했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        result = select_and_summarize(
            [source],
            FakeGenerator([response, response]),
        )

        self.assertEqual(result, [])
        self.assertEqual(
            result.retryable_urls,
            frozenset({source["original_url"]}),
        )

    def test_does_not_treat_quarter_number_as_the_missing_change_metric(self):
        source = article(
            "marvell-quarter-results",
            "Marvell second-quarter revenue rises 42% to $2.0 billion",
            "Second-quarter revenue increased 42% to $2.0 billion.",
            "https://example.com/marvell-quarter-results",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="마벨, 2분기 매출 증가",
                    content="마벨의 2분기 매출이 증가했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        result = select_and_summarize(
            [source],
            FakeGenerator([response, response]),
        )

        self.assertEqual(result, [])
        self.assertEqual(
            result.retryable_urls,
            frozenset({source["original_url"]}),
        )

    def test_does_not_require_an_unrelated_operational_count_as_change_metric(self):
        source = article(
            "retail-store-expansion",
            "Retailer revenue increased after opening 2 stores",
            "Revenue increased as the retailer opened 2 additional stores.",
            "https://example.com/retail-store-expansion",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="소매업체, 매장 확장 후 매출 증가",
                    content="소매업체가 매장을 확장한 뒤 매출이 증가했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["retail-store-expansion"],
        )

    def test_deduplicates_same_five_bank_overdraft_delinquency_release(self):
        short = article(
            "overdraft-age-angle",
            "Youth and elderly credit-line delinquencies rise at five banks in July",
            "The five major banks reported higher July credit-line delinquencies by age.",
            "https://example.com/overdraft-age-angle",
        )
        complete = article(
            "overdraft-complete",
            "Five-bank overdraft delinquency rate rises to 0.22% in July",
            "The five major banks reported a July overdraft delinquency rate of 0.22%.",
            "https://example.com/overdraft-complete",
        )
        response = json.dumps(
            [
                decision_for(
                    short,
                    0,
                    title="청년·고령층 신용한도대출 연체 급증",
                    content="5대 은행의 7월 분석에서 청년·고령층 신용한도대출 연체가 늘었습니다.",
                    category="indicator",
                ),
                decision_for(
                    complete,
                    1,
                    title="5대 은행 마이너스통장 연체율 0.22%로 상승",
                    content="5대 은행의 7월 마이너스통장 연체율이 0.22%로 높아졌습니다.",
                    category="indicator",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize(
            [short, complete],
            FakeGenerator(response),
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["overdraft-complete"],
        )

    def test_keeps_overdraft_delinquency_reports_for_different_months(self):
        june = article(
            "overdraft-june",
            "Five-bank overdraft delinquency rate rises in June",
            "The five major banks published their June overdraft delinquency rate.",
            "https://example.com/overdraft-june",
        )
        july = article(
            "overdraft-july",
            "Five-bank overdraft delinquency rate rises in July",
            "The five major banks published their July overdraft delinquency rate.",
            "https://example.com/overdraft-july",
        )
        response = json.dumps(
            [
                decision_for(
                    june,
                    0,
                    title="5대 은행 6월 마이너스통장 연체율 상승",
                    content="5대 은행의 6월 마이너스통장 연체율이 상승했습니다.",
                    category="indicator",
                ),
                decision_for(
                    july,
                    1,
                    title="5대 은행 7월 마이너스통장 연체율 상승",
                    content="5대 은행의 7월 마이너스통장 연체율이 상승했습니다.",
                    category="indicator",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([june, july], FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["overdraft-june", "overdraft-july"],
        )

    def test_allows_policy_category_and_defines_its_boundary(self):
        source = article(
            "policy-tax-law",
            "Government introduces tax credit for 6 strategic industries",
            "The new tax credit applies across six strategic industries.",
            "https://example.com/policy-tax-law",
        )
        response = """[
            {
                "temp_id": 0,
                "source_ref": "policy-tax-law",
                "source_title": "Government introduces tax credit for 6 strategic industries",
                "title": "정부, 6대 전략산업 국내생산 세액공제 도입",
                "content": "정부가 6대 전략산업에 적용되는 국내생산 세액공제를 도입했습니다.",
                "importance_score": 8,
                "category": "policy",
                "news_type": "official_announcement",
                "selection_reason": "여러 전략산업에 적용되는 세제 정책이 발표됐습니다."
            }
        ]"""

        class PolicyAwareGenerator:
            def __init__(self):
                self.prompts = []

            def __call__(self, prompt):
                self.prompts.append(prompt)
                required_fragments = (
                    '"policy": laws, tax, government policy',
                    'enforcement aimed at one specific company',
                )
                text = response if all(
                    fragment in prompt for fragment in required_fragments
                ) else "[]"
                return SimpleNamespace(text=text)

        selected = select_and_summarize([source], PolicyAwareGenerator())

        self.assertEqual(
            [item["category"] for item in selected],
            ["policy"],
        )

    def test_rejects_currency_conversion_not_present_in_source(self):
        source = article(
            "spacex-currency-conversion",
            "SpaceX shares trade near the $135 issue price",
            "Shares traded near $135 while 191 million shares remained outstanding.",
            "https://example.com/spacex-currency-conversion",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="스페이스X 주가, 공모가 135달러 근접",
                    content="스페이스X 주가가 135달러(191원)에 근접했습니다.",
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_million_amount_mistranslated_as_man_unit(self):
        source = article(
            "spr-unit-drift",
            "US strategic petroleum reserve falls to 298.7 million barrels",
            "The reserve declined to 298.7 million barrels.",
            "https://example.com/spr-unit-drift",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="미국 전략비축유 298.7만 배럴로 감소",
                    content="미국 전략비축유가 298.7만 배럴로 감소했습니다.",
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_unlocalized_financial_and_foreign_script_fragments(self):
        sources = [
            article(
                "alcon-symbols",
                "Alcon lowers tariff impact estimate to $40M-$90M",
                "Alcon lowered its tariff estimate to between $40 million and $90 million.",
                "https://example.com/alcon-symbols",
            ),
            article(
                "copper-german-fragment",
                "Copper project moves nearer to a final investment decision",
                "The company said the project moved nearer to a final investment decision.",
                "https://example.com/copper-german-fragment",
            ),
            article(
                "ev-cjk-fragment",
                "New vehicle scrappage scheme begins",
                "The government introduced a new vehicle scrappage scheme.",
                "https://example.com/ev-cjk-fragment",
            ),
        ]
        response = json.dumps(
            [
                decision_for(
                    sources[0],
                    0,
                    title="알콘, 관세 영향 $40M-$90M로 하향",
                    content="알콘이 관세 영향 추정치를 $40M-$90M로 낮췄습니다.",
                ),
                decision_for(
                    sources[1],
                    1,
                    title="구리 프로젝트, 최종 투자 결정에 접근",
                    content="구리 프로젝트가 최종 투자 결정에 näher 다가갔습니다.",
                ),
                decision_for(
                    sources[2],
                    2,
                    title="정부, 차량 스크래피지制度 도입",
                    content="정부가 새로운 차량 스크래피지制度를 도입했습니다.",
                    category="policy",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize(sources, FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_title_that_replaces_backed_spc_with_parent_company(self):
        source = article(
            "kioxia-spc-actor",
            "SK Hynix-backed SPC2 becomes Kioxia's largest shareholder",
            "The special-purpose company backed by SK Hynix became Kioxia's largest shareholder.",
            "https://example.com/kioxia-spc-actor",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="SK하이닉스, 키옥시아 최대주주 지위 확보",
                    content="SK하이닉스가 투자한 특수목적법인(SPC2)이 키옥시아 최대주주가 됐습니다.",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_excludes_comparisons_guides_routine_filings_and_minor_market_quotes_before_ai(self):
        rejected = [
            article(
                "company-comparison",
                "TSMC versus ASML: market share and valuation compared",
                "TSMC has 73% share and ASML has 90% share; no new event was reported.",
                "https://example.com/company-comparison",
            ),
            article(
                "buyer-guide",
                "What to consider before buying an electric vehicle",
                "A guide to charging, range and battery warranties for buyers.",
                "https://example.com/buyer-guide",
            ),
            article(
                "routine-filing",
                "BRT Apartments files second-quarter financial statements with SEC",
                "The company made a routine filing without new earnings metrics.",
                "https://example.com/routine-filing",
            ),
            article(
                "airport-baggage",
                "Airport passengers board flights without luggage after baggage failure",
                "Some passengers arrived without bags; no flights were cancelled.",
                "https://example.com/airport-baggage",
            ),
            article(
                "cyber-trend",
                "Cyberattacks evolve toward destructive industrial control systems",
                "Experts described a broad trend without a new attack, loss or official action.",
                "https://example.com/cyber-trend",
            ),
            article(
                "minor-futures-move",
                "S&P 500 futures edge up 0.11% ahead of CPI",
                "The futures quote moved 0.11% before the scheduled data release.",
                "https://example.com/minor-futures-move",
            ),
            article(
                "minor-index-move",
                "KOSPI falls 0.95% as KOSDAQ also weakens",
                "The indexes declined without a newly reported catalyst.",
                "https://example.com/minor-index-move",
            ),
            article(
                "daily-mortgage-quote",
                "US 30-year mortgage rate slips from 6.706% to 6.688%",
                "The daily average changed without a new central-bank or government action.",
                "https://example.com/daily-mortgage-quote",
            ),
        ]
        kept = article(
            "intel-equity-offering",
            "Intel announces $15 billion equity offering",
            "Intel announced the offering to fund capital expenditure and working capital.",
            "https://example.com/intel-equity-offering",
        )
        response = json.dumps(
            [
                decision_for(
                    kept,
                    8,
                    title="인텔, 150억 달러 유상증자 발표",
                    content="인텔이 자본지출 등을 위해 150억 달러 규모 유상증자를 발표했습니다.",
                )
            ],
            ensure_ascii=False,
        )
        generator = FakeGenerator(response)

        selected = select_and_summarize([*rejected, kept], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["intel-equity-offering"],
        )
        for source in rejected:
            self.assertNotIn(source["raw_title"], generator.prompts[0])

    def test_deduplicates_price_updates_from_same_geopolitical_oil_catalyst(self):
        first = article(
            "iran-oil-first",
            "Brent rises to $88.10 after Trump demands compensation from Iran",
            "Brent rose after the US president demanded war compensation from Iran.",
            "https://example.com/iran-oil-first",
        )
        second = article(
            "iran-oil-second",
            "Oil rises to $87.72 on Trump Iran compensation demand",
            "Crude rose after Trump repeated the compensation demand to Iran.",
            "https://example.com/iran-oil-second",
        )
        response = json.dumps(
            [
                decision_for(
                    first,
                    0,
                    title="트럼프의 이란 보상 요구에 브렌트유 88.1달러",
                    content="트럼프 미국 대통령의 이란 보상 요구에 브렌트유가 88.1달러로 상승했습니다.",
                    category="geopolitics",
                ),
                decision_for(
                    second,
                    1,
                    title="트럼프의 이란 보상 요구로 원유 87.72달러",
                    content="트럼프 미국 대통령의 같은 보상 요구로 원유가 87.72달러로 상승했습니다.",
                    category="geopolitics",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([first, second], FakeGenerator(response))

        self.assertEqual(len(selected), 1)

    def test_drops_price_only_follow_up_for_recent_geopolitical_oil_event(self):
        source = article(
            "iran-oil-follow-up",
            "Oil trades at $87.72 after Trump Iran compensation demand",
            "The same compensation demand remained the only catalyst for the updated quote.",
            "https://example.com/iran-oil-follow-up",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="트럼프의 이란 보상 요구로 원유 87.72달러",
                    content="트럼프 미국 대통령의 기존 보상 요구로 원유가 87.72달러에 거래됐습니다.",
                    category="geopolitics",
                    news_type="follow_up",
                )
            ],
            ensure_ascii=False,
        )
        recent_news = [
            {
                "title": "트럼프의 이란 보상 요구에 브렌트유 88.1달러",
                "content": "트럼프 미국 대통령의 이란 보상 요구에 브렌트유가 88.1달러로 상승했습니다.",
            }
        ]

        selected = select_and_summarize(
            [source],
            FakeGenerator(response),
            recent_news=recent_news,
        )

        self.assertEqual(selected, [])

    def test_deduplicates_same_company_stock_quote_across_korean_and_english_names(self):
        first = article(
            "spacex-quote-first",
            "SpaceX shares approach the $135 IPO price amid retail selling",
            "The stock traded near its issue price as retail investors sold shares.",
            "https://example.com/spacex-quote-first",
        )
        second = article(
            "spacex-quote-second",
            "SpaceX stock recovers issue price as retail investors sell",
            "Shares returned to $135 while retail investors remained net sellers.",
            "https://example.com/spacex-quote-second",
        )
        response = json.dumps(
            [
                decision_for(
                    first,
                    0,
                    title="스페이스X 주가, 135달러 공모가 근접",
                    content="스페이스X 주가가 개인 매도 속에 135달러 공모가에 근접했습니다.",
                    category="market",
                ),
                decision_for(
                    second,
                    1,
                    title="SpaceX, 개인 매도 속 135달러 상장가 회복",
                    content="SpaceX 주가가 개인 매도 속에 135달러 상장 가격을 회복했습니다.",
                    category="market",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([first, second], FakeGenerator(response))

        self.assertEqual(len(selected), 1)

    def test_keeps_correctly_named_subsidiary_as_transaction_actor(self):
        source = article(
            "mobileye-direct-actor",
            "Intel subsidiary Mobileye acquires Autobrains",
            "Mobileye acquired Autobrains in a completed transaction.",
            "https://example.com/mobileye-direct-actor",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="모빌아이, 오토브레인스 인수",
                    content="인텔 자회사 모빌아이가 오토브레인스 인수를 완료했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(len(selected), 1)

    def test_keeps_3m_company_name_with_localized_financial_amount(self):
        source = article(
            "3m-investment",
            "3M announces $1 billion US factory investment",
            "3M announced a binding $1 billion factory investment.",
            "https://example.com/3m-investment",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="3M, 미국 공장에 10억 달러 투자",
                    content="3M이 미국 공장에 10억 달러를 투자한다고 발표했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(len(selected), 1)

    def test_normalizes_confirmed_mixed_language_names_and_oil_route_term(self):
        source = article(
            "contact-energy-oil-route",
            "Contact Energy warns that an oil route remains blocked",
            "Contact Energy said the blocked oil route affected fuel deliveries.",
            "https://example.com/contact-energy-oil-route",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="컨택트 에너지, 석유로드 차단 영향 발표",
                    content="컨택트 에너지가 석유로드 차단으로 연료 공급이 영향을 받았다고 밝혔습니다.",
                    category="geopolitics",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(
            selected[0]["normalized_title"],
            "콘택트 에너지, 원유 운송로 차단 영향 발표",
        )
        self.assertEqual(
            selected[0]["normalized_content"],
            "콘택트 에너지가 원유 운송로 차단으로 연료 공급이 영향을 받았다고 밝혔습니다.",
        )

    def test_excludes_nonbinding_mou_founder_profile_and_minor_recall_before_ai(self):
        rejected = [
            article(
                "nonbinding-mou",
                "Ricoh Hong Kong and Halo Energy sign smart-mobility MOU",
                "The nonbinding MOU announced a partnership without a contract, order, investment or deployment.",
                "https://example.com/nonbinding-mou",
            ),
            article(
                "founder-profile",
                "Founder launches personal-finance platform after family money problems",
                "The profile describes the founder's mother and the platform without funding, revenue or customers.",
                "https://example.com/founder-profile",
            ),
            article(
                "minor-food-recall",
                "Taylor Farms recalls jalapeno products over possible salmonella",
                "The voluntary product recall reported no illnesses, regulator order or material financial impact.",
                "https://example.com/minor-food-recall",
            ),
        ]
        kept = article(
            "major-food-recall",
            "FDA orders nationwide food recall after 100 hospitalizations",
            "The regulator ordered a nationwide recall after 100 people were hospitalized.",
            "https://example.com/major-food-recall",
        )
        response = json.dumps(
            [
                decision_for(
                    kept,
                    3,
                    title="미국 FDA, 입원 100명 발생 식품 전국 회수 명령",
                    content="미국 식품의약국(FDA)이 입원 환자 100명 발생 후 전국적인 식품 회수를 명령했습니다.",
                    category="policy",
                    news_type="official_announcement",
                )
            ],
            ensure_ascii=False,
        )
        generator = FakeGenerator(response)

        selected = select_and_summarize([*rejected, kept], generator)

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            ["major-food-recall"],
        )
        for source in rejected:
            self.assertNotIn(source["raw_title"], generator.prompts[0])

    def test_rejects_controlled_security_test_rewritten_as_real_incident(self):
        source = article(
            "controlled-ai-security-test",
            "AI agent reaches external service during controlled security test",
            "Researchers observed the behavior in an authorized evaluation environment.",
            "https://example.com/controlled-ai-security-test",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="AI 에이전트, 외부 시스템 침해 사고 발생",
                    content="AI 에이전트가 보안 시험 중 외부 시스템에 무단 침입해 데이터를 탈취하는 사고가 발생했습니다.",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_prompt_keeps_concrete_recall_product_feature_and_new_security_disclosure(self):
        recall = article(
            "toyota-camry-recall",
            "Toyota recalls 11,159 Camry vehicles in Canada",
            "Toyota recalled the vehicles over a display defect.",
            "https://example.com/toyota-camry-recall",
        )
        product_feature = article(
            "tesla-cybercab-starlink",
            "Tesla unveils first Cybercab with integrated Starlink",
            "Tesla officially revealed the new connectivity feature.",
            "https://example.com/tesla-cybercab-starlink",
        )
        security_disclosure = article(
            "trump-iran-security-flight",
            "Trump used secret military aircraft after Iran assassination threat",
            "The previously undisclosed presidential security response was newly reported.",
            "https://example.com/trump-iran-security-flight",
        )
        response = json.dumps(
            [
                decision_for(
                    recall,
                    0,
                    title="토요타, 캐나다서 캠리 11,159대 리콜",
                    content="토요타가 디스플레이 결함으로 캐나다에서 캠리 11,159대를 리콜했습니다.",
                ),
                decision_for(
                    product_feature,
                    1,
                    title="테슬라, 스타링크 탑재 사이버캡 공개",
                    content="테슬라가 스타링크 연결 기능을 탑재한 사이버캡을 공식 공개했습니다.",
                ),
                decision_for(
                    security_disclosure,
                    2,
                    title="트럼프, 이란 위협에 비밀 군용기 이용",
                    content="트럼프 미국 대통령이 이란의 암살 위협에 대응해 비밀 군용기를 이용한 사실이 새로 공개됐습니다.",
                    category="geopolitics",
                ),
            ],
            ensure_ascii=False,
        )

        class PolicyAwareGenerator:
            def __init__(self):
                self.prompts = []

            def __call__(self, prompt):
                self.prompts.append(prompt)
                required = (
                    'A specific recall',
                    'official product or feature release',
                    'previously undisclosed security response',
                )
                return SimpleNamespace(
                    text=response if all(fragment in prompt for fragment in required) else "[]"
                )

        selected = select_and_summarize(
            [recall, product_feature, security_disclosure],
            PolicyAwareGenerator(),
        )

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            [
                "toyota-camry-recall",
                "tesla-cybercab-starlink",
                "trump-iran-security-flight",
            ],
        )

    def test_caps_forward_gdp_forecast_below_breaking(self):
        source = article(
            "seychelles-gdp-forecast",
            "Seychelles forecasts 3.4% GDP growth for 2027",
            "The government published a forward economic growth forecast.",
            "https://example.com/seychelles-gdp-forecast",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="세이셸, 2027년 GDP 성장률 3.4% 전망",
                    content="세이셸 정부가 2027년 국내총생산 성장률을 3.4%로 전망했습니다.",
                    importance_score=9,
                    category="indicator",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 8)

    def test_upgrades_confirmed_attack_on_merchant_ship_in_strategic_waterway(self):
        source = article(
            "red-sea-merchant-attack",
            "Houthi missiles strike merchant ship in Bab el-Mandeb, killing four",
            "Authorities confirmed three ballistic missiles hit the commercial vessel.",
            "https://example.com/red-sea-merchant-attack",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="바브엘만데브 해협 상선 피격으로 4명 사망",
                    content="후티 반군의 탄도미사일이 바브엘만데브 해협의 상선을 타격해 4명이 사망했습니다.",
                    importance_score=8,
                    category="geopolitics",
                    news_type="breaking",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected[0]["importance_score"], 9)

    def test_reclassifies_domestic_company_operations_out_of_geopolitics(self):
        outage = article(
            "calgary-copper-theft-outage",
            "Copper theft attempt disrupts Rogers internet service in Calgary",
            "Rogers technicians were repairing the damaged network.",
            "https://example.com/calgary-copper-theft-outage",
        )
        grid = article(
            "kenya-power-grid",
            "Kenya Power warns variable renewable energy is straining the grid",
            "The electricity utility said wind and solar exceeded 20% of supply.",
            "https://example.com/kenya-power-grid",
        )
        response = json.dumps(
            [
                decision_for(
                    outage,
                    0,
                    title="캘거리 구리 절도 시도로 인터넷 서비스 중단",
                    content="구리 절도 시도로 로저스의 캘거리 일부 인터넷 서비스가 중단됐습니다.",
                    category="geopolitics",
                ),
                decision_for(
                    grid,
                    1,
                    title="케냐전력, 재생에너지 증가로 전력망 불안 우려",
                    content="케냐전력은 풍력과 태양광 비중이 20%를 넘으며 전력망 안정성이 영향을 받고 있다고 밝혔습니다.",
                    category="geopolitics",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([outage, grid], FakeGenerator(response))

        self.assertEqual([item["category"] for item in selected], ["corporate", "corporate"])

    def test_keeps_distinct_hormuz_negotiation_developments(self):
        articles = [
            article(
                "hormuz-concessions",
                "Iran demands more US concessions to reopen Strait of Hormuz",
                "Iran made a new demand while vessel traffic remained low.",
                "https://example.com/hormuz-concessions",
            ),
            article(
                "hormuz-technical-phase",
                "Iran and Oman reach technical phase in Hormuz reopening talks",
                "The negotiations moved into technical discussions over shipping routes.",
                "https://example.com/hormuz-technical-phase",
            ),
            article(
                "hormuz-us-compensation",
                "Trump says US will also seek compensation in Iran talks",
                "The US president introduced a separate American compensation demand.",
                "https://example.com/hormuz-us-compensation",
            ),
        ]
        response = json.dumps(
            [
                decision_for(
                    articles[0],
                    0,
                    title="이란, 호르무즈 재개 조건으로 미국에 추가 양보 요구",
                    content="이란이 호르무즈 해협 통행 재개 조건으로 미국에 추가 양보를 요구했습니다.",
                    category="geopolitics",
                    news_type="follow_up",
                ),
                decision_for(
                    articles[1],
                    1,
                    title="이란·오만, 호르무즈 재개 협상 기술 단계 진입",
                    content="이란과 오만의 호르무즈 해협 재개 협상이 운송 경로를 논의하는 기술 단계에 진입했습니다.",
                    category="geopolitics",
                    news_type="follow_up",
                ),
                decision_for(
                    articles[2],
                    2,
                    title="트럼프, 호르무즈 협상에서 미국도 보상 요구",
                    content="트럼프 미국 대통령이 이란과의 협상에서 미국도 보상을 요구하겠다고 밝혔습니다.",
                    category="geopolitics",
                    news_type="follow_up",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize(articles, FakeGenerator(response))

        self.assertEqual(
            [item["provider_article_id"] for item in selected],
            [
                "hormuz-concessions",
                "hormuz-technical-phase",
                "hormuz-us-compensation",
            ],
        )

    def test_normalizes_confirmed_fed_camry_flagged_ship_and_trade_deficit_terms(self):
        articles = [
            article(
                "cleveland-fed-hammack",
                "Cleveland Fed President Hammack says another rate increase may be needed",
                "The regional Federal Reserve Bank president discussed inflation policy.",
                "https://example.com/cleveland-fed-hammack",
            ),
            article(
                "toyota-camry-spelling",
                "Toyota recalls 11,159 Camry vehicles in Canada",
                "The automaker announced the recall over a display defect.",
                "https://example.com/toyota-camry-spelling",
            ),
            article(
                "panama-flagged-vessel",
                "Panama-flagged container ship attacked near Strait of Hormuz",
                "The vessel was hit by a missile in waters near Pakistan.",
                "https://example.com/panama-flagged-vessel",
            ),
            article(
                "us-trade-deficit",
                "US trade deficit reaches $109.26 billion for fourth month",
                "The trade deficit remained in deficit for a fourth consecutive month.",
                "https://example.com/us-trade-deficit",
            ),
        ]
        response = json.dumps(
            [
                decision_for(
                    articles[0],
                    0,
                    title="연방준비제도장장 하마크, 추가 금리 인상 필요성 강조",
                    content="클리블랜드 연방준비제도장장 하마크가 물가 대응을 위해 추가 금리 인상이 필요할 수 있다고 밝혔습니다.",
                    category="market",
                ),
                decision_for(
                    articles[1],
                    1,
                    title="토요타, 캐나다 카멜리 11,159대 리콜",
                    content="토요타가 디스플레이 결함으로 캐나다에서 카멜리 11,159대를 리콜했습니다.",
                ),
                decision_for(
                    articles[2],
                    2,
                    title="호르무즈 인근에서 팬아마 플래그십 컨테이너선 피격",
                    content="팬아마 플래그십 컨테이너선이 호르무즈 해협 인근에서 미사일 공격을 받았습니다.",
                    category="geopolitics",
                ),
                decision_for(
                    articles[3],
                    3,
                    title="미국 무역수지 1,092.6억 달러로 4개월 연속 적자",
                    content="미국 무역수지가 1,092.6억 달러를 기록하며 4개월 연속 적자를 이어갔습니다.",
                    category="indicator",
                ),
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize(articles, FakeGenerator(response))

        self.assertEqual(
            [item["normalized_title"] for item in selected],
            [
                "클리블랜드 연은 총재 하마크, 추가 금리 인상 필요성 강조",
                "토요타, 캐나다 캠리 11,159대 리콜",
                "호르무즈 인근에서 파나마 선적 컨테이너선 피격",
                "미국 무역적자 1,092.6억 달러로 4개월 연속 지속",
            ],
        )
        self.assertEqual(
            selected[3]["normalized_content"],
            "미국 무역적자가 1,092.6억 달러를 기록하며 4개월 연속 이어졌습니다.",
        )

    def test_rejects_from_to_level_rewritten_as_change_amount(self):
        source = article(
            "ai-margin-levels",
            "AI adopters report operating margins rising from 1.50% to 1.80%",
            "The survey reported the two margin levels, not a 1.5 percentage-point increase.",
            "https://example.com/ai-margin-levels",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="AI 도입 기업, 영업이익률 1.50% 이상 상승",
                    content="AI 도입 기업의 평균 영업이익률이 1.50%에서 1.80%로 높아졌습니다.",
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_rejects_number_borrowed_from_different_metric(self):
        source = article(
            "bok-cross-metric",
            "Bank of Korea official cites debt ratio and GDP growth figures",
            "The household debt ratio rose 17.1%. The GDP growth forecast is 1.7%.",
            "https://example.com/bok-cross-metric",
        )
        response = json.dumps(
            [
                decision_for(
                    source,
                    0,
                    title="한은, 추가 기준금리 인상 가능성 시사",
                    content="한국은행 관계자가 17.1%의 GDP 성장률을 근거로 추가 금리 인상 가능성을 언급했습니다.",
                    category="market",
                )
            ],
            ensure_ascii=False,
        )

        selected = select_and_summarize([source], FakeGenerator(response))

        self.assertEqual(selected, [])

    def test_response_schema_allows_complete_titles_up_to_fifty_five_characters(self):
        title_schema = NEWS_SELECTION_RESPONSE_FORMAT["json_schema"]["schema"][
            "items"
        ]["properties"]["title"]

        self.assertEqual(title_schema["maxLength"], 55)
        self.assertIn("policy", SELECTABLE_CATEGORIES)

if __name__ == "__main__":
    unittest.main()
