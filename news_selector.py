"""AI-assisted selection and Korean summarization for normalized news articles."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import json
import logging
import re


LOGGER = logging.getLogger(__name__)


class SelectionResult(list):
    """List-compatible selector output with per-article retry metadata."""

    __slots__ = ("_retryable_urls",)

    def __init__(self, items=(), *, retryable_urls=()):
        super().__init__(items)
        self._retryable_urls = frozenset(retryable_urls)

    @property
    def retryable_urls(self) -> frozenset[str]:
        return self._retryable_urls


SELECTABLE_NEWS_TYPES = {
    "breaking",
    "new_development",
    "official_announcement",
    "follow_up",
}
SELECTABLE_CATEGORIES = {
    "market",
    "indicator",
    "geopolitics",
    "corporate",
    "policy",
}
NEWS_SELECTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "news_selection",
        "strict": True,
        "schema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "temp_id": {"type": "integer"},
                    "source_ref": {"type": "string"},
                    "source_title": {"type": "string", "maxLength": 500},
                    "title": {"type": "string", "maxLength": 55},
                    "content": {"type": "string", "maxLength": 110},
                    "importance_score": {"type": "integer", "minimum": 7, "maximum": 10},
                    "category": {
                        "type": "string",
                        "enum": sorted(SELECTABLE_CATEGORIES),
                    },
                    "news_type": {
                        "type": "string",
                        "enum": sorted(SELECTABLE_NEWS_TYPES),
                    },
                    "selection_reason": {"type": "string"},
                },
                "required": [
                    "temp_id",
                    "source_ref",
                    "source_title",
                    "title",
                    "content",
                    "importance_score",
                    "category",
                    "news_type",
                    "selection_reason",
                ],
                "additionalProperties": False,
            },
        },
    },
}
OBVIOUS_ANALYSIS_TITLE_PHRASES = (
    "looks fairly valued",
    "what's going on with",
    "what is going on with",
    "mixed moves",
    "has muted effect",
    "p/e doubts",
    "p e doubts",
    "last month",
    "what the market is saying",
    "says buy",
    "time to sell",
    "study finds",
    "survey finds",
    "price target",
    "stock picks",
    "bullish turn",
    "critical support",
    "predicts when",
    "faces new threats",
    "faces new competition",
    "may offer some protection",
    "investing lessons",
    "the effect of",
    "consortium study",
    "ahead of",
    "외환-마감",
    "지난달",
    "반등할까",
    "경제브리핑",
)
EXCLUDED_FEED_SOURCE_IDS = {
    "simplywall.st",
    "www.simplywall.st",
    "cmcmarkets.com",
    "www.cmcmarkets.com",
    "nature.com",
    "www.nature.com",
    "cancernetwork.com",
    "www.cancernetwork.com",
}
CARD_PRODUCT_MARKERS = (" card", "카드")
MINOR_CARD_CHANGE_MARKERS = (
    "annual fee",
    "higher fee",
    "new benefits",
    "benefit changes",
    "rewards changes",
    "welcome bonus",
    "연회비",
    "혜택 변경",
    "혜택 개편",
    "포인트 변경",
    "마일리지 변경",
)
MATERIAL_FOLLOW_UP_MARKERS = (
    "합의",
    "타결",
    "승인",
    "완료",
    "확정",
    "발효",
    "취소",
    "철회",
    "재개",
    "중단",
    "상향",
    "하향",
    "확대",
    "추가",
)
ROUTINE_MARKET_QUOTE_MARKERS = (
    "안정세",
    "보합",
    "달러당",
    "선에서",
    "held near",
    "holds near",
    "held steady",
    "holds steady",
    "stabilized",
    "remained stable",
    "traded at",
)
EVENT_TOKEN_SYNONYMS = (
    ("연방항공청", "faa"),
    ("인공지능", "ai"),
    ("주력 산업", "주력산업"),
    ("ai 관련 주식", "기술주"),
    ("ai 관련주", "기술주"),
    ("ai주식", "기술주"),
    ("kospi", "코스피"),
    ("미국주식시장", "미국 주식시장"),
    ("역사적", "사상"),
    ("고점", "최고치"),
    ("국제 유가", "유가"),
    ("원유 가격", "유가"),
    ("원유", "유가"),
    ("급등", "상승"),
    ("급락", "하락"),
    ("매도세", "하락"),
    ("약세", "하락"),
)
EVENT_CONCEPT_PATTERNS = {
    "labor_release": (
        r"고용\s*지표",
        r"실업률",
        r"일자리\s*(?:보고서|지표)?",
        r"\bemployment\b",
        r"\bunemployment\b",
        r"\bjobs?\s+report\b",
        r"\bjobless\s+rate\b",
    ),
}
EVENT_GENERIC_TOKENS = {
    "상반기",
    "하반기",
    "발표",
    "발표됐습니다",
    "증가",
    "증가했습니다",
    "감소",
    "감소했습니다",
    "기록",
    "최대",
    "역대",
    "급증",
    "급감",
    "상승",
    "하락",
    "지수",
    "통계",
    "수치",
    "전년",
    "전월",
    "분기",
    "연간",
    "올해",
    "신규",
    "정부",
    "기업",
    "시장",
}
SPECIALIST_ACRONYMS_REQUIRING_EXPLANATION = {
    "FSSAI",
    "GPIF",
    "KBRA",
    "LIC",
    "OFS",
    "OGDC",
    "PFII",
}
MACRO_SOURCE_TERMS = (
    "consumer price",
    "inflation",
    "producer price",
    "unemployment",
    "payroll",
    "employment",
    "retail sales",
    "manufacturing index",
    "manufacturing activity",
    "gross domestic product",
    " gdp",
    " pmi",
    "wage",
)
SOURCE_GEOGRAPHY_PATTERNS = (
    (r"\bnorth korea\b", ("북한", "조선민주주의인민공화국")),
    (r"\b(?:south korea|republic of korea|korea)\b", ("한국", "대한민국", "국내")),
    (r"\b(?:united states|u\.s\.?|us)\b", ("미국",)),
    (r"\bchina\b", ("중국",)),
    (r"\bjapan\b", ("일본",)),
    (r"\bindia\b", ("인도",)),
    (r"\b(?:eurozone|euro area)\b", ("유로존", "유로 지역")),
    (r"\b(?:united kingdom|uk|britain)\b", ("영국",)),
    (r"\bgermany\b", ("독일",)),
    (r"\bfrance\b", ("프랑스",)),
    (r"\bspain\b", ("스페인",)),
    (r"\bitaly\b", ("이탈리아",)),
    (r"\bcanada\b", ("캐나다",)),
    (r"\baustralia\b", ("호주", "오스트레일리아")),
    (r"\bnew zealand\b", ("뉴질랜드",)),
    (r"\bindonesia\b", ("인도네시아",)),
    (r"\bmalaysia\b", ("말레이시아",)),
    (r"\bnigeria\b", ("나이지리아",)),
    (r"\bpakistan\b", ("파키스탄",)),
    (r"\bbrazil\b", ("브라질",)),
    (r"\bmexico\b", ("멕시코",)),
    (r"\brussia\b", ("러시아",)),
    (r"\bsaudi arabia\b", ("사우디아라비아", "사우디")),
    (r"\b(?:turkey|türkiye)\b", ("튀르키예", "터키")),
)
SUMMARY_PLACEHOLDER_PHRASES = (
    "text_too_short",
    "n/a",
    "내용이 부족",
    "본문이 부족",
    "정보가 부족",
    "요약 불가",
    "요약할 수 없",
)
SUMMARY_FORBIDDEN_TEMPLATE_PHRASES = (
    "시장 영향:",
    "관전 포인트",
)
ENGLISH_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "january": "1",
    "february": "2",
    "march": "3",
    "april": "4",
    "may": "5",
    "june": "6",
    "july": "7",
    "august": "8",
    "september": "9",
    "october": "10",
    "november": "11",
    "december": "12",
}
EVENT_MONTH_NUMBERS = {
    month: int(ENGLISH_NUMBER_WORDS[month])
    for month in (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
}
MAX_REPACKAGED_EVENT_AGE_DAYS = 3


def _is_obvious_analysis_title(title: str) -> bool:
    normalized = title.casefold()
    return any(phrase in normalized for phrase in OBVIOUS_ANALYSIS_TITLE_PHRASES)


def _contains_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def _is_minor_card_product_change(article: dict) -> bool:
    text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description")
    ).casefold()
    return any(marker in text for marker in CARD_PRODUCT_MARKERS) and any(
        marker in text for marker in MINOR_CARD_CHANGE_MARKERS
    )


def _is_repackaged_old_event(article: dict) -> bool:
    source_text = " ".join(
        article.get(field) or "" for field in ("raw_title", "raw_description")
    ).casefold()
    if any(
        marker in source_text
        for marker in (
            "today",
            "now ",
            "begins enforcement",
            "started enforcement",
            "takes effect",
            "effective today",
            "new enforcement",
            "new court challenge",
            "new filing",
            "new data",
            "latest update",
            "오늘",
            "시행 시작",
            "발효",
            "후속 조치",
            "새 수치",
        )
    ):
        return False

    published_at = article.get("published_at") or ""
    try:
        published_date = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        ).date()
    except ValueError:
        return False

    event_dates = []
    month_pattern = "|".join(EVENT_MONTH_NUMBERS)
    for match in re.finditer(
        rf"\bon\s+({month_pattern})\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?",
        source_text,
    ):
        month_name, day_text, year_text = match.groups()
        year = int(year_text) if year_text else published_date.year
        try:
            event_date = published_date.replace(
                year=year,
                month=EVENT_MONTH_NUMBERS[month_name],
                day=int(day_text),
            )
        except ValueError:
            continue
        if year_text is None and event_date > published_date:
            event_date = event_date.replace(year=year - 1)
        event_dates.append(event_date)

    for match in re.finditer(
        r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일",
        source_text,
    ):
        date_context = source_text[match.end() : match.end() + 20]
        if any(marker in date_context for marker in ("기준", "마감", "종료")):
            continue
        year_text, month_text, day_text = match.groups()
        year = int(year_text) if year_text else published_date.year
        try:
            event_date = published_date.replace(
                year=year,
                month=int(month_text),
                day=int(day_text),
            )
        except ValueError:
            continue
        if year_text is None and event_date > published_date:
            event_date = event_date.replace(year=year - 1)
        event_dates.append(event_date)

    return any(
        (published_date - event_date).days > MAX_REPACKAGED_EVENT_AGE_DAYS
        for event_date in event_dates
    )


def _has_conflicting_revenue_growth_series(text: str) -> bool:
    if "revenue" not in text or not any(
        marker in text
        for marker in ("surging", "growth", "rose from", "increased from")
    ):
        return False

    values_by_year = {}
    for match in re.finditer(
        r"\$?\s*(\d+(?:\.\d+)?)\s*(million|billion)\b[^.]{0,35}?\b((?:19|20)\d{2})\b",
        text,
    ):
        amount_text, unit, year_text = match.groups()
        amount = Decimal(amount_text)
        if unit == "billion":
            amount *= Decimal("1000")
        values_by_year[int(year_text)] = amount

    ordered_values = [values_by_year[year] for year in sorted(values_by_year)]
    return len(ordered_values) >= 3 and any(
        later < earlier
        for earlier, later in zip(ordered_values, ordered_values[1:])
    )


def _is_low_value_item(article: dict) -> bool:
    title = (article.get("raw_title") or "").casefold()
    description = (article.get("raw_description") or "").casefold()
    text = f"{title} {description}"
    material_company_context = any(
        marker in text
        for marker in (
            "revenue",
            "profit",
            "earnings",
            "guidance",
            "market share",
            "contract",
            "customer",
            "order",
            "production",
            "capacity",
            "approval",
            "매출",
            "영업이익",
            "순이익",
            "실적",
            "가이던스",
            "시장점유율",
            "점유율",
            "계약",
            "수주",
            "생산",
            "승인",
        )
    )
    affirmative_text = re.sub(
        r"\b(?:no|without)\s+(?:new\s+)?(?:deal|contract|tender|investment|"
        r"transaction|result|shutdown|closure|cancellations?|financial impact|"
        r"official action)\b",
        "",
        text,
    )
    material_news_event = any(
        marker in affirmative_text
        for marker in (
            "binding agreement",
            "binding contract",
            "signed agreement",
            "signed contract",
            "supply contract",
            "launches tender",
            "launched tender",
            "completed acquisition",
            "announced investment",
            "reports revenue",
            "reported revenue",
            "reports profit",
            "reported profit",
            "raises guidance",
            "cuts guidance",
            "official investigation",
            "regulatory action",
            "government action",
            "airport closes",
            "airport closed",
            "forced airlines to cancel",
            "supply disruption",
        )
    )
    backward_stock_comparison = (
        any(marker in title for marker in (" versus ", " vs ", " vs. "))
        and any(
            marker in text
            for marker in (
                "return",
                "past performance",
                "historical stock performance",
                "six-month",
                "year-to-date",
            )
        )
        and any(marker in text for marker in ("stock", "share", "performance"))
    )
    if backward_stock_comparison and not material_news_event:
        return True
    if (
        any(marker in title for marker in (" versus ", " vs ", " vs. ", " compared"))
        and any(
            marker in text
            for marker in ("market share", "valuation", "performance", "which is better")
        )
        and not material_news_event
    ):
        return True
    if (
        any(
            title.startswith(marker)
            for marker in (
                "what to consider",
                "things to consider",
                "what to know before buying",
                "buying guide",
            )
        )
        and any(
            marker in text
            for marker in ("buyer", "buying", "purchase", "warranty", "charging")
        )
        and not material_news_event
    ):
        return True
    if (
        re.match(r"^(?:how|why)\b", title)
        and any(marker in text for marker in ("business model", "how it works"))
        and any(marker in text for marker in ("evergreen", "explainer", "works"))
        and not material_news_event
    ):
        return True
    if (
        any(
            marker in text
            for marker in (
                "show interest in",
                "shows interest in",
                "express interest in",
                "expressed interest in",
                "potential buyers",
            )
        )
        and not material_news_event
    ):
        return True
    if (
        any(
            marker in title
            for marker in ("faces a test", "faces test", "what to watch")
        )
        and any(marker in text for marker in ("outlook", "previews", "future risk"))
        and not material_news_event
    ):
        return True
    if (
        any(marker in title for marker in ("blocks", "blocked", "suspends"))
        and any(marker in text for marker in ("analyst", "account dispute", "platform dispute"))
        and not any(
            marker in text
            for marker in (
                "fraud",
                "scam",
                "lawsuit",
                "court",
                "regulator",
                "government",
                "material loss",
            )
        )
        and not material_company_context
    ):
        return True
    if (
        "airport" in text
        and any(
            marker in text
            for marker in (
                "narrowly avoid collision",
                "narrowly avoids collision",
                "near collision",
                "near miss",
            )
        )
        and not material_news_event
        and not any(
            marker in affirmative_text
            for marker in (
                "fatal",
                "injured",
                "investigation opened",
                "regulator ordered",
                "supply disruption",
            )
        )
    ):
        return True
    if (
        "airport" in text
        and any(marker in text for marker in ("baggage", "luggage", "bags"))
        and any(marker in text for marker in ("failure", "without", "missing", "delayed"))
        and not any(
            marker in affirmative_text
            for marker in (
                "flights cancelled",
                "flight cancellations",
                "airport closed",
                "airport closes",
                "shutdown",
                "regulator ordered",
                "material financial impact",
            )
        )
    ):
        return True
    if _has_conflicting_revenue_growth_series(description):
        return True
    if (
        any(marker in title for marker in ("launch", "unveil", "introduce"))
        and any(
            marker in text
            for marker in (
                "whisky",
                "whiskey",
                "bottle",
                "beer",
                "four-door model",
                "pickup name",
                "starting price",
            )
        )
        and not any(
            marker in text
            for marker in (
                "sales rose",
                "orders reached",
                "production started",
                "began production",
                "factory investment",
                "signed contract",
            )
        )
    ):
        return True
    if (
        any(marker in text for marker in ("rand", "yen", "currency"))
        and any(
            marker in text
            for marker in ("holds steady", "held steady", "remained stable")
        )
        or (
            any(marker in text for marker in ("rand", "yen", "currency"))
            and re.search(r"\bstable\b", text)
        )
    ) and not any(
        marker in text
        for marker in (
            "intervention",
            "rate decision",
            "capital control",
            "new rule",
        )
    ):
        return True
    if (
        any(marker in title for marker in ("warns", "warning"))
        and any(marker in text for marker in ("could", " may ", "future", "risk"))
        and any(marker in text for marker in ("interview", "said", "opinion"))
        and not any(
            marker in text
            for marker in ("new report", "test results", "new data", "enforcement")
        )
    ):
        return True
    if (
        "impersonation scam" in text
        and any(marker in text for marker in ("warns", "warning", "advisory"))
        and (
            any(
                marker in text
                for marker in ("no enforcement", "no material loss", "no arrests")
            )
            or not any(
                marker in text
                for marker in ("ordered", "froze assets", "arrested", "charged")
            )
        )
    ):
        return True
    if "market outlook" in title and any(
        marker in title for marker in ("historical high", "what to expect")
    ):
        return True
    if re.match(r"^(researchers|scientists)\s+(turn|convert)\b", title):
        return True
    if any(
        marker in title
        for marker in ("annual membership fee", "admirals club", "resort fee")
    ):
        return True
    if (
        "airport" in text
        and "wheelchair" in text
        and any(
            marker in text
            for marker in ("rolls out", "introduces", "launches", "deploys")
        )
    ):
        return True
    if (
        any(
            marker in text
            for marker in (
                "managing director",
                "portfolio chief",
                "regional head",
                "division head",
            )
        )
        and any(
            marker in title
            for marker in ("appoints", "appointed", "names", "named", "hires", "fills")
        )
        and not any(
            marker in text
            for marker in (
                "chief executive officer",
                "chief financial officer",
                " ceo",
                " cfo",
            )
        )
    ):
        return True
    if (
        any(marker in text for marker in ("bottle shop", "liquor store"))
        and any(
            marker in text
            for marker in ("ordered to shut", "closure order", "shut down")
        )
    ):
        return True
    if "healthwashing" in text or (
        any(marker in text for marker in ("study", "research"))
        and any(marker in text for marker in ("avocado oil", "consumer product"))
    ):
        return True
    if (
        any(marker in text for marker in ("earnings", "results"))
        and any(
            marker in text
            for marker in (
                "set to report",
                "will report",
                "scheduled to report",
                "due to report",
                "expected to report",
                "earnings preview",
            )
        )
    ):
        return True
    if (
        any(
            marker in text
            for marker in (
                "files financial statements",
                "filed financial statements",
                "submits financial statements",
                "routine filing",
            )
        )
        and not any(
            marker in affirmative_text
            for marker in (
                "reports revenue",
                "reported revenue",
                "reports profit",
                "reported profit",
                "raises guidance",
                "cuts guidance",
            )
        )
    ):
        return True
    if "university" in text and "campus" in text and any(
        marker in text for marker in (" opens ", "will open", "new campus")
    ):
        return True
    if (
        "report" in text
        and any(marker in text for marker in (" could ", " may ", " can "))
        and any(marker in text for marker in ("growth", "future", "potential"))
    ):
        return True
    if (
        any(marker in text for marker in ("account", "accounts"))
        and any(marker in text for marker in ("blocks", "blocked", "disabled"))
        and any(marker in text for marker in ("scam", "fraud", "abusive activity"))
    ):
        return True
    if (
        any(marker in title for marker in ("launches", "introduces", "unveils"))
        and any(marker in text for marker in ("platform", "new product"))
        and any(marker in text for marker in ("designed for", "optimized for"))
        and not material_company_context
        and not any(marker in text for marker in ("government", "federal"))
    ):
        return True
    if any(marker in text for marker in (" memorandum of understanding", " mou")):
        binding_outcome_markers = (
            "binding agreement",
            "binding contract",
            "purchase order",
            "contract value",
            "commercial deployment",
            "began deployment",
            "announced investment of",
            "secured funding of",
            "reports revenue",
        )
        if "nonbinding" in text or not any(
            marker in affirmative_text for marker in binding_outcome_markers
        ):
            return True
    if (
        "founder" in text
        and any(marker in text for marker in ("profile", "mother", "family"))
        and any(marker in text for marker in ("platform", "startup"))
        and not any(
            marker in affirmative_text
            for marker in (
                "raised $",
                "raised funding",
                "secured funding",
                "reports revenue",
                "signed contract",
                "paying customers",
            )
        )
    ):
        return True
    recall_text = re.sub(
        r"\b(?:no|without)\s+(?:reported\s+)?"
        r"(?:illnesses?|hospitali[sz]ations?|deaths?|regulator order|"
        r"material financial impact)\b",
        "",
        text,
    )
    if (
        any(marker in text for marker in (" recall", "recalls", "recalled"))
        and any(marker in text for marker in ("food", "product", "salmonella"))
        and not any(
            marker in recall_text
            for marker in (
                "fda orders",
                "regulator ordered",
                "nationwide",
                "class i recall",
                "hospitalized",
                "hospitalisation",
                "hospitalization",
                "death",
                "fatal",
                "production halted",
                "sales suspended",
            )
        )
    ):
        return True
    if (
        any(marker in text for marker in ("survey", "poll"))
        and any(marker in text for marker in ("consumer", "shopper", "respondent"))
        and not any(marker in text for marker in ("consumer confidence", "소비자심리"))
    ):
        return True
    if (
        any(
            marker in text
            for marker in ("forecast to", "expected to grow", "is forecast", "outlook")
        )
        and any(marker in text for marker in ("demand", "growth", "market size"))
        and not material_company_context
    ):
        return True
    if (
        "youtube" in text
        and any(marker in text for marker in ("warns", "warning"))
        and (
            "no sanction" in text
            or not any(
                marker in text for marker in ("fine", "ban", "sanction", "new rule")
            )
        )
    ):
        return True
    if (
        "meeting" in text
        and any(marker in text for marker in ("discussed", "discussion"))
        and (
            any(marker in text for marker in ("no decision", "no agreement"))
            or not any(
                marker in text
                for marker in (
                    "agreed",
                    "approved",
                    "signed",
                    "adopted",
                    "new framework",
                    "new rule",
                )
            )
        )
    ):
        return True
    cyber_affirmative_text = re.sub(
        r"\b(?:no|without)\s+(?:a\s+)?(?:new\s+)?"
        r"(?:attack|breach|loss|official action)\b",
        "",
        affirmative_text,
    )
    if (
        any(marker in text for marker in ("cyberattack", "cyber attack"))
        and any(marker in text for marker in ("trend", "evolves", "evolving"))
        and any(marker in text for marker in ("experts", "broad trend"))
        and not any(
            marker in cyber_affirmative_text
            for marker in (
                "new attack",
                "confirmed breach",
                "data stolen",
                "material loss",
                "official investigation",
                "regulatory action",
            )
        )
    ):
        return True
    if (
        any(marker in title for marker in ("calls", "describes"))
        and any(marker in title for marker in ("sales decline", "sales drop"))
        and any(marker in text for marker in ("good month", "consistent with"))
    ):
        return True
    if (
        any(marker in text for marker in ("duty free", "retailer", "airport store"))
        and any(
            marker in text
            for marker in ("crypto.com pay", "cryptocurrency payment", "crypto payment")
        )
        and any(marker in text for marker in ("introduces", "launches", "accepts"))
    ):
        return True
    market_move = re.search(r"(\d+(?:\.\d+)?)%", text)
    if (
        market_move
        and float(market_move.group(1)) <= 2.0
        and any(
            marker in text
            for marker in ("stock index", "benchmark index", "kse-100", "psx")
        )
        and any(marker in text for marker in (" rose ", "jumps", "gains", "rally"))
        and not any(
            marker in text
            for marker in (
                "central bank",
                "rate decision",
                "market intervention",
                "default",
                "war",
                "sanction",
                "government decision",
            )
        )
    ):
        return True
    material_market_driver = any(
        marker in affirmative_text
        for marker in (
            "central bank",
            "rate decision",
            "market intervention",
            "government decision",
            "official data",
            "data released",
            "earnings announcement",
            "profit warning",
            "default",
            "war began",
            "war escalated",
            "sanction imposed",
            "tariff imposed",
            "ceasefire",
        )
    )
    if (
        market_move
        and float(market_move.group(1)) <= 2.0
        and any(
            marker in text
            for marker in (
                "stock index",
                "benchmark index",
                "indexes",
                "kospi",
                "kosdaq",
                "s&p 500",
                "ftse 100",
                "stock futures",
            )
        )
        and any(
            marker in text
            for marker in (
                " rose ",
                "rises",
                "gains",
                "rally",
                "falls",
                "fell",
                "declines",
                "declined",
                "weakens",
                "weakness",
            )
        )
        and not material_market_driver
    ):
        return True
    if (
        "mortgage rate" in text
        and any(marker in text for marker in ("daily average", "from "))
        and not any(
            marker in affirmative_text
            for marker in (
                "central bank cut",
                "central bank raised",
                "central bank announced",
                "government announced",
                "government imposed",
                "new rule took effect",
                "rate decision changed",
            )
        )
    ):
        return True
    if (
        any(marker in text for marker in ("recycling body", "deposit return"))
        and any(marker in text for marker in ("executive pay", "board pay", "compensation"))
    ):
        return True
    if (
        "starship" in text
        and "test" in text
        and any(
            marker in text
            for marker in ("plans", "will attempt", "scheduled", "later this month")
        )
    ):
        return True
    if (
        "organoid" in text
        and "oecd" in text
        and any(marker in text for marker in ("aims", "target", "goal", "by 2028"))
        and (
            "not yet" in text
            or not any(marker in text for marker in ("adopted", "approved"))
        )
    ):
        return True
    if "investor letter" in text and any(
        marker in text
        for marker in (
            "fund reported",
            "fund returned",
            "portfolio returned",
            "net return",
            "discusses",
            "highlighted",
        )
    ):
        return True
    if (
        any(
            marker in text
            for marker in (
                "considers",
                "considering",
                "explores",
                "exploring",
                "could launch",
                "may launch",
                "no decision",
            )
        )
        and any(
            marker in text
            for marker in (
                "streaming service",
                "investment vehicle",
                "financing vehicle",
            )
        )
        and not any(
            marker in text
            for marker in (
                "approved",
                "signed",
                "binding agreement",
                "completed",
                "launched",
            )
        )
    ):
        return True
    if (
        any(marker in text for marker in ("trade association", "industry group"))
        and any(marker in text for marker in ("guideline", "guidance"))
        and any(marker in text for marker in ("voluntary", "urging", "urges"))
        and not any(
            marker in text
            for marker in ("regulator adopted", "binding", "enforceable", "new rule")
        )
    ):
        return True
    if "codeshare" in text and any(
        marker in text
        for marker in ("route", "routes", "destination", "destinations")
    ):
        return True
    if (
        any(marker in text for marker in ("court", "judge"))
        and any(
            marker in text
            for marker in ("questions", "questioned", "doubts", "skepticism")
        )
        and (
            any(marker in text for marker in ("no ruling", "no order"))
            or not any(
                marker in text
                for marker in (
                    "ruling",
                    "order",
                    "approved",
                    "rejected",
                    "granted",
                    "dismissed",
                )
            )
        )
    ):
        return True
    if (
        "stock" in title
        and re.search(r"\d+(?:\.\d+)?%", title)
        and re.search(r"\bsince\s+(?:19|20)\d{2}\b", title)
        and any(
            marker in text
            for marker in (
                "past performance",
                "without a new",
                "historical performance",
                "looks back",
            )
        )
    ):
        return True
    product_unit_context = any(
        marker in text
        for marker in (
            "since launch",
            "cumulative",
            "preorder",
            " units",
            "consumer product",
            "사전판매",
            "누적 판매",
            "만 대",
            "만개",
        )
    )
    product_sales_story = product_unit_context and any(
        marker in text
        for marker in (" sales", " sold ", "preorder", "사전판매", "누적 판매")
    ) and any(
        marker in text
        for marker in (
            "since launch",
            "cumulative",
            "record",
            "top ",
            "surpass",
            "신기록",
            "역대",
            "돌파",
        )
    )
    if product_sales_story and not material_company_context:
        return True
    if (
        any(
            marker in text
            for marker in ("forbes asia", "best under a billion", "포브스 아시아")
        )
        and any(
            marker in text
            for marker in (" list", "named", "ranking", "선정", "명단")
        )
    ):
        return True
    if (
        any(
            marker in text
            for marker in ("interim corporate tax", "법인세 중간예납")
        )
        and any(
            marker in text
            for marker in (
                "remind",
                "filing deadline",
                "payment schedule",
                "신고·납부 안내",
                "납부기한",
            )
        )
    ):
        return True
    return (
        "smartphone makers" in title
        and "hardware innovation" in title
        and "broad review" in description
    )


def _billion_to_eok_equivalents(source_text: str) -> list[tuple[str, str]]:
    equivalents = []
    for match in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s+billion\b",
        source_text,
        flags=re.IGNORECASE,
    ):
        source_amount = match.group(1)
        try:
            eok_amount = Decimal(source_amount) * Decimal("10")
        except InvalidOperation:
            continue
        formatted = format(eok_amount.normalize(), "f")
        equivalents.append((source_amount, formatted))
    return equivalents


def _format_korean_integer_amount(amount: int) -> str:
    parts = []
    eok_count, amount = divmod(amount, 100_000_000)
    if eok_count:
        parts.append(f"{eok_count}억")
        remainder_units = (
            (10_000_000, "천만"),
            (1_000_000, "백만"),
            (100_000, "십만"),
            (10_000, "만"),
            (1_000, "천"),
        )
    else:
        remainder_units = (
            (10_000, "만"),
            (1_000, "천"),
        )
    for unit_value, unit_name in remainder_units:
        unit_count, amount = divmod(amount, unit_value)
        if unit_count:
            parts.append(f"{unit_count}{unit_name}")
    if amount:
        parts.append(f"{amount:,}")
    return "".join(parts) or "0"


def _format_decimal_eok_amount(amount_text: str) -> str:
    won_amount = Decimal(amount_text) * Decimal("100000000")
    if won_amount != won_amount.to_integral_value():
        return f"{amount_text}억"
    return _format_korean_integer_amount(int(won_amount))


def _million_to_korean_equivalents(source_text: str) -> list[tuple[str, str]]:
    equivalents = []
    for match in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:m\b|million\b)",
        source_text,
        flags=re.IGNORECASE,
    ):
        source_amount = match.group(1)
        full_amount = Decimal(source_amount) * Decimal("1000000")
        if full_amount != full_amount.to_integral_value():
            continue
        equivalents.append(
            (source_amount, _format_korean_integer_amount(int(full_amount)))
        )
    return equivalents


def _normalize_known_korean_terms(article: dict, value: str) -> str:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    if "nissan" in source_text:
        value = value.replace("니산", "닛산")
    if "goldman sachs" in source_text:
        value = value.replace("골드만 사스", "골드만삭스")
        value = value.replace("골드만 사츠", "골드만삭스")
    if "chevron" in source_text:
        value = value.replace("체비론", "셰브론")
    if "lpg" in source_text or "liquefied petroleum gas" in source_text:
        value = re.sub(r"액화석유가(?!스)", "액화석유가스", value)
    if "strait of hormuz" in source_text:
        value = value.replace("만유원지", "호르무즈 해협")
    if "oil route" in source_text:
        value = value.replace("석유로드", "원유 운송로")
    if "contact energy" in source_text:
        value = value.replace("컨택트 에너지", "콘택트 에너지")
    if "camry" in source_text:
        value = value.replace("카멜리", "캠리")
    if "cleveland fed president" in source_text or (
        "cleveland federal reserve" in source_text and "president" in source_text
    ):
        value = value.replace("클리블랜드 연방준비제도장장", "클리블랜드 연은 총재")
        value = value.replace("연방준비제도장장", "클리블랜드 연은 총재")
    if "panama-flagged" in source_text or "panamanian-flagged" in source_text:
        value = value.replace("팬아마 플래그십", "파나마 선적")
        value = value.replace("파나마 플래그십", "파나마 선적")
    if "trade deficit" in source_text:
        value = value.replace("무역수지가", "무역적자가")
        value = re.sub(r"(\d+개월\s+연속)\s+적자를\s+이어", r"\1 이어", value)
        value = re.sub(
            r"(\d+개월\s+연속)\s+이어갔습니다",
            r"\1 이어졌습니다",
            value,
        )
        value = re.sub(
            r"무역수지\s+([\d,.]+(?:억|조)?\s*달러)로\s+"
            r"(\d+개월\s+연속)\s+적자",
            r"무역적자 \1로 \2 지속",
            value,
        )
    if "anglo american" in source_text:
        value = value.replace("앙골라 아메리칸", "앵글로 아메리칸")
        value = value.replace("앵글로우 아메리칸", "앵글로 아메리칸")
    if "network advertising initiative" in source_text:
        value = value.replace(
            "네트워크 광고 주도권",
            "네트워크 광고 이니셔티브(NAI)",
        )
        value = value.replace("이니셔티브(NAI)이", "이니셔티브(NAI)가")
    if any(
        marker in source_text
        for marker in (
            "employment data",
            "jobs data",
            "labor-market data",
            "labour-market data",
        )
    ):
        value = value.replace("취업 데이터", "고용지표")
    if any(
        marker in source_text
        for marker in (
            "share repurchase",
            "stock repurchase",
            "buyback",
        )
    ):
        value = value.replace("주식 매수 회수", "자사주 매입")
    for source_amount, eok_amount in _billion_to_eok_equivalents(source_text):
        value = re.sub(
            rf"(?<![\d.]){re.escape(source_amount)}\s*억\s*달러",
            f"{eok_amount}억 달러",
            value,
        )
    value = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*M\s*(유로|달러)",
        lambda match: (
            f"{_format_korean_integer_amount(int(Decimal(match.group(1)) * Decimal('1000000')))} "
            f"{match.group(2)}"
        ),
        value,
        flags=re.IGNORECASE,
    )
    if "s$" in source_text or "singapore cent" in source_text:
        value = re.sub(
            r"S\$\s*(\d+(?:\.\d+)?)\s*억(?:\s*달러)?",
            lambda match: (
                f"{_format_decimal_eok_amount(match.group(1))} 싱가포르달러"
            ),
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"(?<!싱가포르)(\d+(?:\.\d+)?)센트",
            r"\1싱가포르센트",
            value,
        )
    if "south african rand" in source_text:
        value = value.replace("남아공Rand은", "남아프리카공화국 랜드화는")
        value = value.replace("남아공Rand", "남아프리카공화국 랜드화")
        value = value.replace("남아공 Rand", "남아프리카공화국 랜드화")
    if "jobless claims" in source_text and re.search(
        r"(?:rise|rose|increase|increased)\s+to\s+\d",
        source_text,
    ):
        value = re.sub(r"(\d[\d,]*)명으로 증가", r"\1건으로 증가", value)
        value = re.sub(r"(\d[\d,]*)명 증가", r"\1건으로 증가", value)
    if "jobless claims" in source_text:
        value = re.sub(
            r"(\d[\d,.]*(?:만|천)?)(\s*)명",
            r"\1\2건",
            value,
        )
    if "auckland" in source_text:
        value = value.replace("아크랜드", "오클랜드")
    if "boe/d" in source_text:
        value = re.sub(
            r"(?<![A-Za-z])boe\s*/\s*d를",
            "석유환산배럴/일을",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"(?<![A-Za-z])boe\s*/\s*d(?![A-Za-z])",
            "석유환산배럴/일",
            value,
            flags=re.IGNORECASE,
        )
    if "maybank" in source_text:
        value = value.replace("마이칸은행은", "메이뱅크는")
        value = value.replace("마이칸은행이", "메이뱅크가")
        value = value.replace("마이칸은행", "메이뱅크")
        value = value.replace("마이칸 은행", "메이뱅크")
    if "maybank" in source_text and "etiqa" in source_text:
        value = value.replace(
            "은행 주도의 배분을",
            "은행 채널을 통한 보험 판매를",
        )
    if "smrt trains" in source_text:
        value = value.replace("SMRT 기계수익", "SMRT 트레인스 순이익")
        value = value.replace("SMRT의 세후 이익", "SMRT 트레인스의 세후 이익")
        value = value.replace("SMRT Trains", "SMRT 트레인스")
        if "s$" in source_text:
            value = re.sub(r"(?<!싱가포르)달러", "싱가포르달러", value)
    if " isr" in f" {source_text}" and "정보·감시·정찰(ISR)" not in value:
        value = re.sub(
            r"(?<![A-Za-z])ISR(?![A-Za-z])",
            "정보·감시·정찰(ISR)",
            value,
            flags=re.IGNORECASE,
        )
    if (
        "security institute" in source_text
        and any(marker in source_text for marker in ("test", "evaluation"))
    ):
        value = value.replace(
            "통제 범위를 벗어난 행동을 보였습니다",
            "시험 환경에서 문제 행동을 보였습니다",
        )
    if "unemployment" in source_text:
        value = re.sub(
            r"\s*이는 노동시장 약화를 보여주는 중요한 지표입니다\.",
            "",
            value,
        )
    return value


def _has_untranslated_english_prose(title: str, content: str) -> bool:
    return bool(
        re.search(
            r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])[a-zà-öø-ÿ]{4,}"
            r"(?![A-Za-zÀ-ÖØ-öø-ÿ])",
            f"{title} {content}",
        )
    )


def _has_unlocalized_financial_or_foreign_notation(title: str, content: str) -> bool:
    text = f"{title} {content}"
    return bool(
        re.search(r"[$€£¥]", text)
        or re.search(
            r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*[MBT]\s*"
            r"(?:(?:[-~–]\s*\d+(?:\.\d+)?\s*[MBT])|"
            r"(?:원|달러|유로|엔|위안|루피|페소))",
            text,
        )
        or re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]", text)
    )


def _has_unsupported_currency_conversion(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    currency_source_markers = {
        "원": (" won", "krw", "₩", " 원"),
        "달러": ("dollar", "usd", "$", "달러"),
        "유로": ("euro", "eur", "€", "유로"),
        "엔": (" yen", "jpy", "¥", " 엔"),
        "위안": ("yuan", "cny", "rmb", "위안"),
        "루피": ("rupee", "inr", "루피"),
        "페소": ("peso", "페소"),
    }
    pattern = re.compile(
        r"\d[\d,.]*\s*(원|달러|유로|엔|위안|루피|페소)\s*"
        r"\([^)]*?\d[\d,.]*\s*(원|달러|유로|엔|위안|루피|페소)\)"
    )
    for source_currency, converted_currency in pattern.findall(f"{title} {content}"):
        if source_currency == converted_currency:
            continue
        if not any(
            marker in source_text
            for marker in currency_source_markers[converted_currency]
        ):
            return True
    return False


def _has_mistranslated_english_large_unit(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    summary_text = f"{title} {content}"
    source_amounts = {
        (match.group(1), match.group(2).casefold())
        for match in re.finditer(
            r"(?<![\d.])(\d+(?:\.\d+)?)\s*(million|billion|trillion|[MBT])\b",
            source_text,
            flags=re.IGNORECASE,
        )
    }
    for amount, source_unit in source_amounts:
        escaped_amount = re.escape(amount)
        if source_unit in {"million", "m"}:
            malformed_suffix = r"(?<![백천])만|억|조"
        elif source_unit in {"billion", "b"}:
            malformed_suffix = r"백만|천만|(?<![백천])만|억|조"
        else:
            malformed_suffix = r"백만|천만|(?<![백천])만|억"
        if re.search(
            rf"(?<![\d.]){escaped_amount}\s*(?:{malformed_suffix})",
            summary_text,
        ):
            return True
    return False


def _has_unexplained_specialist_acronym(title: str, content: str) -> bool:
    text = f"{title} {content}"
    acronyms = set(
        re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{2,7}(?![A-Za-z0-9])", text)
    )
    for acronym in acronyms & SPECIALIST_ACRONYMS_REQUIRING_EXPLANATION:
        escaped = re.escape(acronym)
        korean_first = re.search(
            rf"[가-힣][가-힣·\s]{{1,30}}\(\s*{escaped}\s*\)",
            text,
        )
        acronym_first = re.search(rf"\b{escaped}\s*\(\s*[가-힣]", text)
        if not korean_first and not acronym_first:
            return True
    return False


def _has_ambiguous_percentage_growth(content: str) -> bool:
    metric_markers = (
        "매출",
        "수익",
        "영업이익",
        "순이익",
        "주당순이익",
        "생산량",
        "판매량",
        "출하량",
        "시장점유율",
        "점유율",
        "이용자",
        "가입자",
        "수요",
        "가격",
        "물가",
        "임금",
        "고용",
        "국내총생산",
        "GDP",
        "경제",
        "거래량",
        "자산",
        "주문",
        "발행",
        "회사채",
        "단기사채",
        "CP",
    )
    for sentence in re.split(r"(?<=[.!?])\s+", content):
        has_percentage_growth = re.search(
            r"\d+(?:\.\d+)?%[^.!?]{0,12}(?:성장률|성장|증가)",
            sentence,
        )
        if has_percentage_growth and not any(
            marker in sentence for marker in metric_markers
        ):
            return True
    return False


def _has_misattributed_fund_return(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or "" for field in ("raw_title", "raw_description")
    ).casefold()
    if not any(
        marker in source_text
        for marker in ("fund", "portfolio", "국부펀드", "기금", "포트폴리오")
    ):
        return False
    if not any(
        marker in source_text
        for marker in ("return", "수익률", "운용수익")
    ):
        return False

    summary_text = f"{title} {content}"
    if not re.search(r"\d+(?:\.\d+)?%", summary_text):
        return False
    if not any(marker in summary_text for marker in ("수익", "수익률")):
        return False
    return not any(
        marker in summary_text
        for marker in ("펀드", "기금", "포트폴리오")
    )


def _misstates_primary_transaction_actor(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_title = (article.get("raw_title") or "").casefold().strip()
    primary_actor_requirements = (
        (r"^anglo american\b", "앵글로 아메리칸", ("오펜하이머",)),
    )
    for pattern, korean_actor, misleading_leads in primary_actor_requirements:
        if not re.search(pattern, source_title):
            continue
        if korean_actor not in title or korean_actor not in content:
            return True
        if any(
            value.lstrip().startswith(misleading_lead)
            for value in (title, content)
            for misleading_lead in misleading_leads
        ):
            return True
    return False


def _misstates_indirect_transaction_actor(
    article: dict,
    title: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    backed_actor = re.search(
        r"[a-z0-9 .&'-]+-backed\s+([a-z0-9][a-z0-9 .&'-]*?)\s+"
        r"(?:becomes|acquires|acquired|buys|bought|purchases|purchased|takes control)",
        source_text,
    )
    if backed_actor is None:
        return False
    direct_actor = backed_actor.group(1).strip()
    definitive_title_markers = (
        "최대주주",
        "인수",
        "매입",
        "지분 확보",
        "경영권 확보",
    )
    relationship_title_markers = (
        "자회사",
        "관계사",
        "투자한",
        "지원하는",
        "특수목적법인",
        "SPC",
        "SPV",
        "컨소시엄",
    )
    return (
        any(marker in title for marker in definitive_title_markers)
        and direct_actor not in title.casefold()
        and not any(marker in title for marker in relationship_title_markers)
    )


def _overstates_controlled_security_test(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    controlled_test_markers = (
        "controlled security test",
        "controlled test",
        "authorized evaluation",
        "evaluation environment",
        "security evaluation",
        "red-team",
        "red team",
        "sandbox",
    )
    if not any(marker in source_text for marker in controlled_test_markers):
        return False

    actual_incident_markers = (
        "real-world incident",
        "breach occurred",
        "stole data",
        "data theft",
        "data exfiltration",
        "unauthorized intrusion",
    )
    if any(marker in source_text for marker in actual_incident_markers):
        return False

    generated_text = f"{title} {content}"
    overstated_incident_markers = (
        "해킹 사고",
        "침해 사고",
        "사고 발생",
        "무단 침입",
        "데이터 탈취",
    )
    return any(marker in generated_text for marker in overstated_incident_markers)


def _misstates_from_to_level_as_change(article: dict, title: str) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    for match in re.finditer(
        r"\bfrom\s+(\d+(?:\.\d+)?)%\s+to\s+(\d+(?:\.\d+)?)%",
        source_text,
        flags=re.IGNORECASE,
    ):
        start = Decimal(match.group(1))
        end = Decimal(match.group(2))
        claimed_change = re.search(
            rf"(?<![\d.]){re.escape(match.group(1))}%\s*(?:이상\s*)?"
            r"(?:상승|증가|개선)",
            title,
        )
        if claimed_change and abs(end - start) != start:
            return True
    return False


def _misattributes_metric_percentage(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = ". ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    generated_text = f"{title}. {content}"
    metric_patterns = (
        r"\bgdp\s+growth\b",
        r"\bgross domestic product\s+growth\b",
        r"GDP\s*성장률",
        r"국내총생산\s*성장률",
    )

    def metric_percentages(value: str) -> set[str]:
        percentages = set()
        for sentence in re.split(r"(?<=[.!?])\s+|(?<=다\.)", value):
            if not any(re.search(pattern, sentence, flags=re.IGNORECASE) for pattern in metric_patterns):
                continue
            percentages.update(
                _normalize_number(number)
                for number in re.findall(r"(\d+(?:\.\d+)?)\s*%", sentence)
            )
        return percentages

    source_percentages = metric_percentages(source_text)
    generated_percentages = metric_percentages(generated_text)
    return bool(
        source_percentages
        and generated_percentages
        and not generated_percentages.issubset(source_percentages)
    )


def _has_speculative_event_language(value: str) -> bool:
    normalized = value.casefold()
    marker_match = any(
        marker in normalized
        for marker in (
            " may ",
            "may have",
            " might ",
            " could ",
            "reportedly",
            "estimated",
            "suspected",
            "possible intervention",
            "intervention possible",
            "possibility of",
            "appears to have",
            "가능성",
            "추정",
            "관측",
            "의혹",
            "전망",
        )
    )
    speculative_action = re.search(
        r"\bexpected to\s+(?:intervene|conduct|cut|raise|halt|suspend|declare)\b",
        normalized,
    )
    return marker_match or bool(speculative_action)


def _has_confirmed_systemic_event(source_text: str) -> bool:
    if _has_speculative_event_language(source_text):
        return False

    strategic_shipping_attack = (
        any(
            marker in source_text
            for marker in (
                "strait of hormuz",
                "bab el-mandeb",
                "bab al-mandab",
                "red sea",
                "호르무즈 해협",
                "바브엘만데브",
                "홍해",
            )
        )
        and any(
            marker in source_text
            for marker in (
                "merchant ship",
                "commercial vessel",
                "container ship",
                "tanker",
                "상선",
                "컨테이너선",
                "유조선",
            )
        )
        and any(
            marker in source_text
            for marker in (
                "missile strike",
                "missiles strike",
                "missiles hit",
                "vessel was hit",
                "ship was hit",
                "attacked",
                "피격",
                "미사일 공격",
            )
        )
    )
    if strategic_shipping_attack:
        return True

    confirmed_action = any(
        marker in source_text
        for marker in (
            "conduct joint",
            "conducted joint",
            "conducts joint",
            "confirmed direct",
            "intervened directly",
            "carried out",
            "implemented",
            "announced emergency",
            "declared default",
            "공동 개입을 실시",
            "공동 개입 실시",
            "직접 개입",
            "긴급 금리",
            "긴급 유동성",
            "거래 전면 중단",
            "국가 부도 선언",
        )
    )
    if not confirmed_action:
        return False

    foreign_exchange_intervention = (
        any(
            marker in source_text
            for marker in (
                "foreign-exchange",
                "foreign exchange",
                "fx market",
                "currency market",
                "yen-buying",
                "yen intervention",
                "외환시장",
                "환율시장",
                "엔화",
            )
        )
        and "interven" in source_text
        or "시장 개입" in source_text
    )
    emergency_central_bank_action = any(
        marker in source_text
        for marker in (
            "emergency rate cut",
            "emergency rate increase",
            "emergency liquidity",
            "긴급 금리 인하",
            "긴급 금리 인상",
            "긴급 유동성",
        )
    )
    market_wide_halt = any(
        marker in source_text
        for marker in (
            "market-wide trading halt",
            "marketwide trading halt",
            "all trading halted",
            "거래 전면 중단",
        )
    )
    sovereign_default = any(
        marker in source_text
        for marker in (
            "sovereign default",
            "declared default",
            "국가 부도 선언",
        )
    )
    return any(
        (
            foreign_exchange_intervention,
            emergency_central_bank_action,
            market_wide_halt,
            sovereign_default,
        )
    )


def _normalize_importance_score(article: dict, importance_score):

    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    forward_gdp_forecast = (
        any(
            marker in source_text
            for marker in (
                "gdp growth forecast",
                "forecasts",
                "forecast for",
                "projected gdp growth",
                "growth projection",
                "국내총생산 성장률 전망",
                "gdp 성장률 전망",
            )
        )
        and any(marker in source_text for marker in ("gdp", "gross domestic product", "국내총생산"))
        and not any(
            marker in source_text
            for marker in (
                "unexpected contraction",
                "unexpected recession",
                "emergency revision",
                "예상 밖 역성장",
                "긴급 전망 수정",
            )
        )
    )
    if forward_gdp_forecast:
        return min(importance_score, 8)
    if importance_score < 9:
        return 9 if _has_confirmed_systemic_event(source_text) else importance_score
    systemic_markers = (
        "intervention",
        "emergency",
        "trading halt",
        "market crash",
        "financial crisis",
        "bank run",
        "sovereign default",
        "invasion",
        "unexpected rate",
        "surprise rate",
        "시장 개입",
        "비상 조치",
        "거래 중단",
        "시장 붕괴",
        "금융 위기",
        "뱅크런",
        "국가 부도",
        "전쟁",
        "침공",
        "예상 밖 금리",
        "깜짝 금리",
    )
    has_systemic_marker = any(
        marker in source_text for marker in systemic_markers
    ) or bool(re.search(r"\bwar\b", source_text))

    trend_only_framing = any(
        marker in source_text
        for marker in (
            "development constrained by",
            "growth constrained by",
            "constrained by shortage",
            "faces constraints",
            "faces headwinds",
            "broad trend",
            "industry trend",
            "market outlook",
            "growth outlook",
            "개발 제약",
            "성장 제약",
            "부족으로 제약",
            "산업 동향",
            "시장 전망",
            "성장 전망",
        )
    )
    concrete_action = any(
        marker in source_text
        for marker in (
            "announces",
            "announced",
            "approves",
            "approved",
            "adopts",
            "adopted",
            "orders",
            "ordered",
            "signs",
            "signed",
            "bans",
            "banned",
            "imposes",
            "imposed",
            "removes",
            "removed",
            "cuts rate",
            "raises rate",
            "공식 발표",
            "승인",
            "채택",
            "명령",
            "서명",
            "금지",
            "부과",
            "해임",
            "금리 인하",
            "금리 인상",
        )
    )
    abrupt_market_move = any(
        marker in source_text
        for marker in (
            "market plunged",
            "markets plunged",
            "stocks plunged",
            "index plunged",
            "yield surged",
            "currency plunged",
            "시장 급락",
            "증시 급락",
            "지수 급락",
            "금리 급등",
            "통화 급락",
        )
    ) and any(
        float(value) >= 5
        for value in re.findall(r"(\d+(?:\.\d+)?)%", source_text)
    )
    if (
        trend_only_framing
        and not has_systemic_marker
        and not concrete_action
        and not abrupt_market_move
    ):
        return 8

    routine_market_record = any(
        marker in source_text
        for marker in (
            "record high",
            "all-time high",
            "historical high",
            "사상 최고",
            "역대 최고",
            "최고치",
            "신고가",
        )
    ) and any(
        marker in source_text
        for marker in (
            "s&p",
            "nasdaq",
            "dow",
            "stock market",
            "stock index",
            "코스피",
            "코스닥",
            "증시",
            "주식시장",
            "주가지수",
        )
    )
    if routine_market_record and not has_systemic_marker:
        return 8

    nonfinal_regulatory_review = any(
        marker in source_text
        for marker in (
            "delays decision",
            "defers decision",
            "postponed approval",
            "approval delayed",
            "approval on hold",
            "continues reviewing",
            "승인 보류",
            "심사 연기",
            "결정 연기",
        )
    ) and any(
        marker in source_text
        for marker in ("rule", "regulation", "proposal", "listing", "규칙", "규제")
    )
    if nonfinal_regulatory_review and not has_systemic_marker:
        return 8

    ordinary_corporate_transaction = any(
        marker in source_text
        for marker in (
            "agrees to acquire",
            "agreement to acquire",
            "plans sale",
            "stake sale",
            "acquisition agreement",
            "merger agreement",
            "agreed to buy",
            "deal to buy",
            "takeover agreement",
            "acquisition of",
            "buyout",
            "인수 계약",
            "지분 매각",
            "합병 계약",
        )
    ) or bool(
        re.search(
            r"\b(?:(?:agrees?|agreed|plans?|planned)\s+to\s+acquire|takeover)\b",
            source_text,
        )
    )
    if ordinary_corporate_transaction and not has_systemic_marker:
        return 8

    earnings_story = any(
        marker in source_text
        for marker in (
            "earnings",
            "quarterly revenue",
            "quarterly profit",
            "quarter results",
            "분기 매출",
            "분기 영업이익",
            "분기 순이익",
            "분기 실적",
        )
    )
    exceptional_earnings_markers = (
        "accounting fraud",
        "restatement",
        "bankruptcy",
        "default",
        "record loss",
        "withdraws guidance",
        "suspends guidance",
        "회계 부정",
        "실적 정정",
        "파산",
        "채무불이행",
        "사상 최대 손실",
        "가이던스 철회",
        "가이던스 중단",
    )
    if earnings_story and not any(
        marker in source_text for marker in exceptional_earnings_markers
    ):
        return 8
    return importance_score


def _normalize_category(article: dict, category: str) -> str:
    if category != "geopolitics":
        return category
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    geopolitical_markers = (
        "war",
        "military",
        "missile",
        "airstrike",
        "invasion",
        "sanction",
        "export control",
        "blockade",
        "ceasefire",
        "diplomatic",
        "peace talks",
        "assassination threat",
        "hostage",
        "전쟁",
        "군사",
        "미사일",
        "공습",
        "침공",
        "제재",
        "수출 통제",
        "봉쇄",
        "휴전",
        "외교",
        "평화 협상",
        "암살 위협",
    )
    if any(
        (
            re.search(rf"\b{re.escape(marker)}\b", source_text)
            if re.fullmatch(r"[a-z ]+", marker)
            else marker in source_text
        )
        for marker in geopolitical_markers
    ):
        return category
    company_operation_markers = (
        "internet service",
        "network outage",
        "technicians",
        "electricity utility",
        "power grid",
        "grid stability",
        "service outage",
        "인터넷 서비스",
        "통신망",
        "전력망",
        "전력 회사",
        "서비스 중단",
    )
    if any(marker in source_text for marker in company_operation_markers):
        return "corporate"
    return category


def _missing_source_geography(
    article: dict,
    title: str,
    content: str,
    category: str,
) -> bool:
    if category != "indicator":
        return False
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    if not any(term in source_text for term in MACRO_SOURCE_TERMS):
        return False

    summary_text = f"{title} {content}"
    for source_pattern, korean_markers in SOURCE_GEOGRAPHY_PATTERNS:
        if re.search(source_pattern, source_text):
            return not any(marker in summary_text for marker in korean_markers)
    return False


def _is_valid_report_summary(content: str) -> bool:
    normalized = content.strip().casefold()
    if "~" in content:
        return False
    if any(phrase in normalized for phrase in SUMMARY_PLACEHOLDER_PHRASES):
        return False
    if any(phrase in content for phrase in SUMMARY_FORBIDDEN_TEMPLATE_PHRASES):
        return False

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", content.strip())
        if sentence.strip()
    ]
    return 1 <= len(sentences) <= 2 and all(
        re.search(r"(?:습니다|니다)[.!?]?$", sentence) is not None
        for sentence in sentences
    )


INCOMPLETE_TITLE_ENDINGS = (
    "규모 가",
    "위한",
    "관련",
    "따른",
    "통해",
    "대해",
    "하며",
    "하고",
)


def _has_unbalanced_title_delimiters(title: str) -> bool:
    pairs = (("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ("‘", "’"))
    return any(title.count(opening) != title.count(closing) for opening, closing in pairs)


def _has_truncated_numeric_prefix(title: str, content: str) -> bool:
    title_number = re.search(r"(?<![\d.])(\d[\d,.]*)$", title)
    if title_number is None:
        return False
    prefix = title_number.group(1).replace(",", "")
    for match in re.finditer(r"(?<![\d.])(\d[\d,.]*)([조억만천백십])", content):
        if match.group(1).replace(",", "") == prefix:
            return True
    return False


def _has_incomplete_title(title: str, content: str) -> bool:
    normalized = title.strip().rstrip(".!?…")
    if any(normalized.endswith(ending) for ending in INCOMPLETE_TITLE_ENDINGS):
        return True
    if _has_unbalanced_title_delimiters(normalized):
        return True
    if re.search(r"(?:을위|를위)$", normalized):
        return True
    if re.search(r"관련자\s+[가-힣]$", normalized):
        return True
    if re.search(
        r"\d(?:[\d,.]*%?)\s*(?:로|에|에서|까지|부터|보다)$",
        normalized,
    ):
        return True
    return _has_truncated_numeric_prefix(normalized, content)


DIRECTION_MARKER_PAIRS = (
    (
        ("상승", "올랐", "증가", "급증", "확대", "상향"),
        ("하락", "내렸", "감소", "급감", "축소", "하향"),
    ),
    (("승인", "허가", "통과"), ("거절", "불허", "기각")),
    (("흑자",), ("적자", "순손실")),
)


def _has_opposite_title_content_direction(title: str, content: str) -> bool:
    for positive_markers, negative_markers in DIRECTION_MARKER_PAIRS:
        title_positive = any(marker in title for marker in positive_markers)
        title_negative = any(marker in title for marker in negative_markers)
        content_positive = any(marker in content for marker in positive_markers)
        content_negative = any(marker in content for marker in negative_markers)
        if (
            title_positive
            and not title_negative
            and not content_positive
            and content_negative
        ):
            return True
        if (
            title_negative
            and not title_positive
            and not content_negative
            and content_positive
        ):
            return True
    return False


def _has_malformed_korean_amount(text: str) -> bool:
    large_unit_order = {"조": 3, "억": 2, "만": 1}
    amount_pattern = re.compile(
        r"\d+(?:[,\d.]|\s|[조억만천백십])*(?:조|억|만|천|백|십)"
    )
    for match in amount_pattern.finditer(text):
        amount = match.group(0)
        units = [character for character in amount if character in large_unit_order]
        if len(units) != len(set(units)):
            return True
        orders = [large_unit_order[unit] for unit in units]
        if any(current <= following for current, following in zip(orders, orders[1:])):
            return True
    return False


def _title_numbers_missing_from_content(title: str, content: str) -> set[str]:
    return _event_numeric_tokens(title) - _event_numeric_tokens(content)


def _normalize_number(token: str) -> str:
    token = token.replace(",", "")
    whole, separator, fraction = token.partition(".")
    whole = whole.lstrip("0") or "0"
    if not separator:
        return whole
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def _numeric_tokens(text: str, *, include_english_words=False) -> set[str]:
    tokens = {
        _normalize_number(match)
        for match in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)
    }
    if include_english_words:
        lowered = text.casefold()
        for word, value in ENGLISH_NUMBER_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                tokens.add(value)
    return tokens


def _unsupported_summary_numbers(article: dict, title: str, content: str) -> set[str]:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    published_date = (article.get("published_at") or "").split("T", maxsplit=1)[0]
    source_text = f"{source_text} {published_date}"
    source_text = re.sub(r"\[\s*\d+\s+chars?\s*\]", "", source_text, flags=re.IGNORECASE)
    source_numbers = _numeric_tokens(source_text, include_english_words=True)
    source_numbers.update(
        _normalize_number(eok_amount)
        for _, eok_amount in _billion_to_eok_equivalents(source_text)
    )
    for _, eok_amount in _billion_to_eok_equivalents(source_text):
        source_numbers.update(_numeric_tokens(_format_decimal_eok_amount(eok_amount)))
    for _, korean_amount in _million_to_korean_equivalents(source_text):
        source_numbers.update(_numeric_tokens(korean_amount))
    summary_numbers = _numeric_tokens(f"{title} {content}")
    return summary_numbers - source_numbers


def _has_definitive_title_for_speculative_event(
    article: dict,
    title: str,
    content: str,
) -> bool:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    if not _has_speculative_event_language(source_text):
        return False
    if not _has_speculative_event_language(content):
        return False
    if _has_speculative_event_language(title):
        return False
    return any(
        marker in title
        for marker in (
            "개입",
            "인수",
            "승인",
            "사임",
            "체결",
            "실시",
            "금지",
            "중단",
            "발표",
            "결정",
        )
    )


def _source_metric_numbers(article: dict) -> set[str]:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    )
    numbers = set()
    metric_unit_pattern = re.compile(
        r"^\s*(?:%|percent(?:age)?|basis points?|bps|billion|million|trillion|"
        r"thousand|dollars?|won|yuan|yen|euros?|pounds?|barrels?(?:\s+per\s+day)?|"
        r"boe\s*/\s*d|tons?|tonnes?|megawatts?|gigawatts?|mw|gw|억|조|원|달러|"
        r"엔|유로|위안|배럴|톤)",
        flags=re.IGNORECASE,
    )
    for match in re.finditer(r"\d+(?:,\d{3})*(?:\.\d+)?", source_text):
        prefix = source_text[max(0, match.start() - 2) : match.start()]
        suffix = source_text[match.end() : match.end() + 24]
        if metric_unit_pattern.search(suffix) or any(
            prefix.endswith(symbol) for symbol in ("$", "€", "£", "₩")
        ):
            numbers.add(_normalize_number(match.group(0)))
    for _, eok_amount in _billion_to_eok_equivalents(source_text):
        numbers.add(_normalize_number(eok_amount))
        numbers.update(_numeric_tokens(_format_decimal_eok_amount(eok_amount)))
    for _, korean_amount in _million_to_korean_equivalents(source_text):
        numbers.update(_numeric_tokens(korean_amount))
    return numbers


def _is_missing_primary_change_metric(
    article: dict,
    title: str,
    content: str,
) -> bool:
    generated_text = f"{title} {content}"
    generated_change = any(
        marker in generated_text
        for marker in (
            "상승",
            "하락",
            "증가",
            "감소",
            "급증",
            "급감",
            "성장",
            "개선",
            "악화",
            "늘었",
            "줄었",
        )
    )
    if not generated_change:
        return False

    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    source_change = any(
        marker in source_text
        for marker in (
            " rises ",
            " rise ",
            " rose ",
            " increases ",
            " increased ",
            " grows ",
            " grew ",
            " growth ",
            " falls ",
            " fell ",
            " declines ",
            " declined ",
            " drops ",
            " dropped ",
            "상승",
            "하락",
            "증가",
            "감소",
            "급증",
            "급감",
        )
    )
    if not source_change:
        return False

    source_numbers = _source_metric_numbers(article)
    generated_numbers = _event_key_metric_tokens(generated_text)
    return bool(source_numbers) and not bool(source_numbers & generated_numbers)


def _similarity_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", value.casefold()).strip()


def _is_same_event(first: dict, second: dict) -> bool:
    first_raw_title = first["normalized_title"]
    second_raw_title = second["normalized_title"]
    first_raw_content = first["normalized_content"]
    second_raw_content = second["normalized_content"]
    if _has_explicit_event_dimension_conflict(
        f"{first_raw_title} {first_raw_content}",
        f"{second_raw_title} {second_raw_content}",
    ):
        return False
    first_title = _similarity_text(first_raw_title)
    second_title = _similarity_text(second_raw_title)
    first_content = _similarity_text(first_raw_content)
    second_content = _similarity_text(second_raw_content)
    comparisons = (
        (first_title, second_title),
        (first_content, second_content),
        (f"{first_title} {first_content}", f"{second_title} {second_content}"),
    )
    return _has_shared_event_signature(
        first_title,
        first_content,
        second_title,
        second_content,
    ) or any(
        left
        and right
        and SequenceMatcher(None, left, right).ratio() >= 0.7
        for left, right in comparisons
    )


def _event_tokens(value: str) -> set[str]:
    normalized = value.casefold()
    for original, replacement in EVENT_TOKEN_SYNONYMS:
        normalized = normalized.replace(original, replacement)
    return {
        token
        for token in re.findall(r"[0-9a-z가-힣]+", normalized)
        if len(token) >= 2
    }


def _event_concept_tokens(value: str) -> set[str]:
    normalized = value.casefold()
    return {
        concept
        for concept, patterns in EVENT_CONCEPT_PATTERNS.items()
        if any(re.search(pattern, normalized) for pattern in patterns)
    }


def _event_geography_tokens(value: str) -> set[str]:
    normalized = value.casefold()
    geographies = set()
    for _, korean_markers in SOURCE_GEOGRAPHY_PATTERNS:
        if any(
            re.search(
                rf"(?<![0-9a-z가-힣]){re.escape(marker.casefold())}"
                rf"(?![0-9a-z가-힣])",
                normalized,
            )
            for marker in korean_markers
        ):
            geographies.add(korean_markers[0])
    return geographies


def _event_period_tokens(value: str) -> set[str]:
    periods = set()
    for year, month in re.findall(
        r"(?:(20\d{2})년\s*)?(\d{1,2})월",
        value,
    ):
        periods.add(f"{year or '*'}-month-{int(month)}")
    for year, quarter in re.findall(
        r"(?:(20\d{2})년\s*)?(\d)분기",
        value,
    ):
        periods.add(f"{year or '*'}-quarter-{int(quarter)}")
    return periods


def _event_key_metric_tokens(value: str) -> set[str]:
    metrics = _event_numeric_tokens(value)
    period_numbers = {
        _normalize_number(number)
        for number in re.findall(r"(?<!\d)(\d{1,2})(?=월|분기)", value)
    }
    return metrics - period_numbers


def _has_overdraft_delinquency_terms(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in ("마이너스통장", "신용한도대출", "한도대출")
    ) and any(marker in normalized for marker in ("연체율", "연체"))


def _has_five_major_bank_context(value: str) -> bool:
    normalized = re.sub(r"\s+", "", value.casefold())
    return any(
        marker in normalized
        for marker in (
            "5대은행",
            "5대시중은행",
            "다섯대은행",
        )
    )


def _overdraft_delinquency_direction(value: str) -> str | None:
    if any(marker in value for marker in ("상승", "증가", "급증", "늘었", "높아")):
        return "up"
    if any(marker in value for marker in ("하락", "감소", "급감", "줄었", "낮아")):
        return "down"
    return None


def _is_same_overdraft_delinquency_release(
    first_text: str,
    second_text: str,
) -> bool:
    if not all(
        _has_overdraft_delinquency_terms(text)
        and _has_five_major_bank_context(text)
        for text in (first_text, second_text)
    ):
        return False

    first_periods = _event_period_tokens(first_text)
    second_periods = _event_period_tokens(second_text)
    if not first_periods or not second_periods:
        return False
    if first_periods.isdisjoint(second_periods):
        return False

    shared_metrics = (
        _event_key_metric_tokens(first_text) - {"5"}
    ) & (_event_key_metric_tokens(second_text) - {"5"})
    if shared_metrics:
        return True
    first_direction = _overdraft_delinquency_direction(first_text)
    second_direction = _overdraft_delinquency_direction(second_text)
    return bool(first_direction and first_direction == second_direction)


def _has_explicit_event_dimension_conflict(
    first_text: str,
    second_text: str,
) -> bool:
    overdraft_pair = all(
        _has_overdraft_delinquency_terms(text)
        and _has_five_major_bank_context(text)
        for text in (first_text, second_text)
    )
    if overdraft_pair:
        first_periods = _event_period_tokens(first_text)
        second_periods = _event_period_tokens(second_text)
        return bool(
            first_periods
            and second_periods
            and first_periods.isdisjoint(second_periods)
        )

    shared_concepts = _event_concept_tokens(first_text) & _event_concept_tokens(
        second_text
    )
    if "labor_release" not in shared_concepts:
        return False

    first_geographies = _event_geography_tokens(first_text)
    second_geographies = _event_geography_tokens(second_text)
    if (
        first_geographies
        and second_geographies
        and first_geographies.isdisjoint(second_geographies)
    ):
        return True

    first_periods = _event_period_tokens(first_text)
    second_periods = _event_period_tokens(second_text)
    if first_periods and second_periods and first_periods.isdisjoint(second_periods):
        return True

    first_metrics = _event_key_metric_tokens(first_text)
    second_metrics = _event_key_metric_tokens(second_text)
    if first_metrics and second_metrics and first_metrics.isdisjoint(second_metrics):
        return True
    return False


def _event_numeric_tokens(value: str) -> set[str]:
    numbers = set()
    for token in _numeric_tokens(value):
        if token.isdigit() and 1900 <= int(token) <= 2100:
            continue
        numbers.add(token)
    return numbers


def _distinctive_event_tokens(value: str) -> set[str]:
    return {
        token
        for token in _event_tokens(value) - EVENT_GENERIC_TOKENS
        if not re.match(r"^(?:(?:19|20)\d{2}년?|\d{1,2}월|\d+분기)$", token)
    }


def _has_same_company_reporting_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
    first_text = f"{first_title} {first_content}"
    second_text = f"{second_title} {second_content}"
    first_periods = set(re.findall(r"\d+분기", first_text))
    second_periods = set(re.findall(r"\d+분기", second_text))
    if not first_periods.intersection(second_periods):
        return False

    reporting_markers = (
        "실적",
        "매출",
        "이익",
        "순이익",
        "배당",
        "earnings",
        "revenue",
        "profit",
        "dividend",
    )
    if not all(
        any(marker in text.casefold() for marker in reporting_markers)
        for text in (first_text, second_text)
    ):
        return False

    reporting_tokens = {
        "실적",
        "매출",
        "이익",
        "순이익",
        "배당",
        "기록",
        "증가",
    }
    first_entities = _distinctive_event_tokens(first_title) - reporting_tokens
    second_entities = _distinctive_event_tokens(second_title) - reporting_tokens
    return bool(first_entities & second_entities)


def _has_same_market_selloff_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
    first_tokens = _event_tokens(f"{first_title} {first_content}")
    second_tokens = _event_tokens(f"{second_title} {second_content}")
    if not all(
        {"기술주", "하락"}.issubset(tokens)
        for tokens in (first_tokens, second_tokens)
    ):
        return False
    korean_market_markers = {
        "한국",
        "코스피",
        "아시아",
        "삼성전자",
        "sk하이닉스",
    }
    return all(
        bool(tokens & korean_market_markers)
        for tokens in (first_tokens, second_tokens)
    )


def _has_same_industrial_ai_policy_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
    first_tokens = _event_tokens(f"{first_title} {first_content}")
    second_tokens = _event_tokens(f"{second_title} {second_content}")
    required = {"정부", "ai", "주력산업"}
    if not all(required.issubset(tokens) for tokens in (first_tokens, second_tokens)):
        return False
    industry_tokens = {"철강", "석유화학", "조선", "자동차", "바이오"}
    return len((first_tokens & second_tokens) & industry_tokens) >= 2


def _has_same_labor_release_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
    first_text = f"{first_title} {first_content}"
    second_text = f"{second_title} {second_content}"
    if not all(
        "labor_release" in _event_concept_tokens(text)
        for text in (first_text, second_text)
    ):
        return False

    first_geographies = _event_geography_tokens(first_text)
    second_geographies = _event_geography_tokens(second_text)
    if not first_geographies.intersection(second_geographies):
        return False

    first_periods = _event_period_tokens(first_text)
    second_periods = _event_period_tokens(second_text)
    if not first_periods.intersection(second_periods):
        return False

    first_metrics = _event_key_metric_tokens(first_text)
    second_metrics = _event_key_metric_tokens(second_text)
    return bool(first_metrics.intersection(second_metrics))


def _has_shared_event_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
    if _has_explicit_event_dimension_conflict(
        f"{first_title} {first_content}",
        f"{second_title} {second_content}",
    ):
        return False

    if _is_same_overdraft_delinquency_release(
        f"{first_title} {first_content}",
        f"{second_title} {second_content}",
    ) or _has_same_labor_release_signature(
        first_title,
        first_content,
        second_title,
        second_content,
    ) or _has_same_company_reporting_signature(
        first_title,
        first_content,
        second_title,
        second_content,
    ) or _has_same_market_selloff_signature(
        first_title,
        first_content,
        second_title,
        second_content,
    ) or _has_same_industrial_ai_policy_signature(
        first_title,
        first_content,
        second_title,
        second_content,
    ):
        return True

    first_tokens = _distinctive_event_tokens(first_title)
    second_tokens = _distinctive_event_tokens(second_title)
    shared_tokens = first_tokens & second_tokens
    token_overlap = (
        len(shared_tokens) / min(len(first_tokens), len(second_tokens))
        if first_tokens and second_tokens
        else 0
    )
    if len(shared_tokens) >= 3 and token_overlap >= 0.5:
        return True

    first_all_tokens = _distinctive_event_tokens(f"{first_title} {first_content}")
    second_all_tokens = _distinctive_event_tokens(f"{second_title} {second_content}")
    shared_all_tokens = first_all_tokens & second_all_tokens
    all_token_overlap = (
        len(shared_all_tokens) / min(len(first_all_tokens), len(second_all_tokens))
        if first_all_tokens and second_all_tokens
        else 0
    )
    if (
        len(shared_tokens) >= 2
        and len(shared_all_tokens) >= 5
        and all_token_overlap >= 0.4
    ):
        return True

    shared_numbers = _event_numeric_tokens(
        f"{first_title} {first_content}"
    ) & _event_numeric_tokens(f"{second_title} {second_content}")
    return len(shared_tokens) >= 2 and bool(shared_numbers)


def _is_same_recent_event(article: dict, recent_item: dict) -> bool:
    current_title = article.get("normalized_title") or ""
    recent_title = recent_item.get("title") or ""
    current_content = article.get("normalized_content") or ""
    recent_content = recent_item.get("content") or ""
    if _has_explicit_event_dimension_conflict(
        f"{current_title} {current_content}",
        f"{recent_title} {recent_content}",
    ):
        return False
    current_tokens = _event_tokens(current_title)
    recent_tokens = _event_tokens(recent_title)
    shared_tokens = current_tokens & recent_tokens
    token_overlap = (
        len(shared_tokens) / min(len(current_tokens), len(recent_tokens))
        if current_tokens and recent_tokens
        else 0
    )
    if len(shared_tokens) >= 3 and token_overlap >= 0.65:
        return True

    if _has_shared_event_signature(
        current_title,
        current_content,
        recent_title,
        recent_content,
    ):
        return True

    current_text = _similarity_text(current_title)
    recent_text = _similarity_text(recent_title)
    return bool(
        current_text
        and recent_text
        and SequenceMatcher(None, current_text, recent_text).ratio() >= 0.82
    )


def _is_price_quote_only_follow_up(current_text: str, recent_text: str) -> bool:
    asset_markers = (
        "유가",
        "브렌트",
        "원유",
        "주가",
        "환율",
        "지수",
        "선물",
        "달러",
        "엔화",
        "oil",
        "brent",
        "stock",
        "shares",
        "index",
        "futures",
    )
    quote_markers = (
        "상승",
        "하락",
        "거래",
        "기록",
        "회복",
        "근접",
        "유지",
        "rose",
        "fell",
        "traded",
        "recovered",
        "held",
    )
    state_change_markers = (
        *MATERIAL_FOLLOW_UP_MARKERS,
        "공격",
        "확전",
        "휴전",
        "제재",
        "봉쇄",
        "파산",
        "인수",
        "실적",
        "금리 결정",
        "attack",
        "escalation",
        "ceasefire",
        "sanction",
        "blockade",
        "bankruptcy",
        "acquisition",
        "earnings",
        "rate decision",
    )
    return (
        any(marker in current_text.casefold() for marker in asset_markers)
        and any(marker in current_text.casefold() for marker in quote_markers)
        and bool(_numeric_tokens(current_text))
        and not any(
            marker in current_text.casefold()
            and marker not in recent_text.casefold()
            for marker in state_change_markers
        )
    )


def _has_material_follow_up(article: dict, recent_item: dict) -> bool:
    if article.get("news_type") != "follow_up":
        return False
    current_text = " ".join(
        (
            article.get("normalized_title") or "",
            article.get("normalized_content") or "",
        )
    )
    recent_text = " ".join(
        (
            recent_item.get("title") or "",
            recent_item.get("content") or "",
        )
    )
    same_angle_event = _has_same_labor_release_signature(
        article.get("normalized_title") or "",
        article.get("normalized_content") or "",
        recent_item.get("title") or "",
        recent_item.get("content") or "",
    ) or _has_same_company_reporting_signature(
        article.get("normalized_title") or "",
        article.get("normalized_content") or "",
        recent_item.get("title") or "",
        recent_item.get("content") or "",
    ) or _has_same_market_selloff_signature(
        article.get("normalized_title") or "",
        article.get("normalized_content") or "",
        recent_item.get("title") or "",
        recent_item.get("content") or "",
    ) or _has_same_industrial_ai_policy_signature(
        article.get("normalized_title") or "",
        article.get("normalized_content") or "",
        recent_item.get("title") or "",
        recent_item.get("content") or "",
    )
    if same_angle_event and not any(
        marker in current_text
        for marker in (
            "정정",
            "수정",
            "개정",
            "재공시",
            "거래 중단",
            "서킷브레이커",
        )
    ):
        return False
    if any(
        marker in current_text and marker not in recent_text
        for marker in MATERIAL_FOLLOW_UP_MARKERS
    ):
        return True
    if any(
        marker in current_text.casefold()
        for marker in ROUTINE_MARKET_QUOTE_MARKERS
    ):
        return False
    if _is_price_quote_only_follow_up(current_text, recent_text):
        return False
    return bool(_numeric_tokens(current_text) - _numeric_tokens(recent_text))


def _deduplicate_against_recent(
    articles: list[dict],
    recent_news: list[dict],
) -> list[dict]:
    unique = []
    for article in articles:
        duplicate = next(
            (
                recent_item
                for recent_item in recent_news
                if _is_same_recent_event(article, recent_item)
            ),
            None,
        )
        if duplicate is None or _has_material_follow_up(article, duplicate):
            unique.append(article)
    return unique


def _source_completeness(article: dict) -> int:
    source_length = sum(
        len(article.get(field) or "")
        for field in ("raw_title", "raw_description", "raw_content")
    )
    normalized_text = " ".join(
        article.get(field) or ""
        for field in ("normalized_title", "normalized_content")
    )
    source_backed_metrics = _event_key_metric_tokens(normalized_text)
    return source_length + len(normalized_text) + (100 * len(source_backed_metrics))


def _deduplicate_selected(articles: list[dict]) -> list[dict]:
    unique = []
    for article in articles:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(unique)
                if _is_same_event(existing, article)
            ),
            None,
        )
        if duplicate_index is None:
            unique.append(article)
        elif _source_completeness(article) > _source_completeness(
            unique[duplicate_index]
        ):
            unique[duplicate_index] = article
    return unique


def _response_text(response) -> str:
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("AI 응답 텍스트가 비어 있습니다.")

    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    return text


def _decode_json_array(response_text: str) -> list[dict]:
    try:
        value = json.loads(response_text)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, character in enumerate(response_text):
            if character != "[":
                continue
            try:
                value, _ = decoder.raw_decode(response_text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, list):
                return value
        raise original_error

    if not isinstance(value, list):
        raise ValueError("AI 응답은 JSON 배열이어야 합니다.")
    return value


def _load_decisions(response_text: str, generator) -> list[dict]:
    try:
        decisions = _decode_json_array(response_text)
    except (json.JSONDecodeError, ValueError):
        repair_prompt = f"""
아래 응답의 내용과 항목을 바꾸지 말고 JSON 문법만 수정하세요.
문자열 값에는 빠짐없이 큰따옴표를 사용하고, 순수한 JSON 배열만 반환하세요.
원문 후보의 사실을 추가하거나 변경하지 마세요.

[수정할 응답]
{response_text}
"""
        repaired_text = _response_text(generator(repair_prompt))
        try:
            decisions = _decode_json_array(repaired_text)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("AI 응답을 한 차례 수정했지만 JSON 형식이 아닙니다.") from error
    return decisions


def _selection_prompt(candidates: list[dict], recent_news: list[dict]) -> str:
    return f"""
당신은 경제 뉴스 편집자입니다. 아래 후보 중 두 조건을 모두 충족하는 기사만 선택하세요.

1. 새로운 사실: 최근 발생·발표·변경된 사건이거나 기존 사건에 새로운 수치나 진행 상황이 추가됨.
2. 경제 관련성: 경제, 금융시장, 산업, 주요 기업, 규제·정책 또는 경제에 영향을 줄 지정학적 사건과 직접 관련됨.

기업 규모나 지역 범위가 작더라도 주가·재무·고용·산업·규제에 영향을 주는 구체적인 경제 사건이면 7점 후보로 유지하세요.
기업의 구체적인 리콜, 주요 기업의 공식 제품·기능 공개, 생산·공급·서비스 변화는 영향 범위가 제한적이어도 새로운 기업 소식으로 7점 후보에 포함하세요. 단순 홍보 문구만 있고 무엇이 새로 공개·변경됐는지 불명확하면 제외하세요.
지정학 기사는 전쟁 발발·확전·휴전, 국가 간 직접 공격, 경제 제재·수출 통제·관세, 에너지 시설·주요 해상 운송로 차질, 시장에 영향을 줄 협상 타결·결렬처럼 새롭고 구체적인 상태 변화가 있으면 선택하세요.
지정학 사건 자체가 국가·에너지·교역·공급망에 광범위한 영향을 줄 수 있다면 기사에 시장 반응이 아직 적혀 있지 않아도 선택할 수 있습니다. 다만 원문에 없는 경제 영향이나 가격 전망을 요약에 만들어 넣지 마세요.
새롭게 확인·보도된 과거의 안보 조치는 공개 시점에 새 사실로 취급할 수 있습니다. 특히 현직 국가 지도자에 대한 국가 차원의 위협과 공식 경호 대응은 구체적인 지정학 소식으로 유지하세요.
원인·행동·대응 주체를 뒤바꾸지 말고, 누가 무엇을 결정했고 누가 반대하거나 대응했는지 원문 관계를 그대로 쓰세요.
자회사·특수목적법인(SPC)·컨소시엄이 직접 행동한 경우 모회사 이름만으로 행동 주체를 바꾸지 말고, 직접 행위자와 관계를 함께 밝히세요.
탐사 결과를 확인된 매장지나 매장량 발견으로 확대하지 말고, 원문이 확정적으로 표현한 경우에만 `발견`을 사용하세요.
통제된 보안 시험과 실제 외부 시스템 침해를 구분하고, 시험 환경인지 실제 사고인지 원문 범위를 보존하세요.
법률·규제 기사는 영향받는 기업·기관·규칙·사건 중 식별 가능한 주체를 제목이나 요약에 명시하세요.

다음은 제외하세요.
- 새로운 사실이 없는 전망, 칼럼, 비교, 순위, 추천, 사용법, 회고, 단순 해설
- 생활·상품·자동차 소개와 홍보성 기사
- 과거 사실만 다시 설명하는 기사
- 구체적인 새 공격·결정·제재·합의 없이 전황이나 사상자 수만 반복하거나 정치인의 기존 입장과 가능성만 전하는 지정학 기사
- 주가·코인 가격의 혼조나 등락만 나열한 시황. 단, 같은 기사에서 실적 발표·가이던스 변경·정책 결정 같은 새 원인을 명시하면 그 원인만 선택 가능
- `적정 가치인가`, `무슨 일이 있나`, `영향은 미미했다`처럼 기자가 기존 사실을 평가하는 기사
- 증권사 매수·매도 의견, 목표주가, 종목 추천 및 `시장이 말하는 것` 형식의 분석 기사
- 공식 통계나 정책 발표가 아닌 설문조사·연구 결과만 소개하는 기사
- 지난달·지난 분기 사건에 대한 새로운 조치나 결과 없이 관계자의 평가만 추가한 기사
- 후보 안에 동일한 사실을 표현만 바꿔 다룬 기사가 여러 개면 원문 정보가 가장 구체적인 하나만 선택
- 최근 저장 뉴스와 동일한 사실이면 제외. 같은 지역·기업·협상 주제라도 새 요구, 새 발언, 협상 단계 변화, 공격, 합의·승인·완료·취소·새 수치가 있으면 별도의 `follow_up`으로 선택
- 새 발표나 조치가 없는 업계 전망·역사적 수준 평가, 단순 실험 연구, 개인용 멤버십·혜택 변경
- 회사 실적·가이던스·시장점유율과 연결되지 않은 개별 상품 판매 기록, 정기 신고·납부 안내, 기업 순위·수상·명단 기사
- 공항 편의 서비스 도입, 지역 상점 단속, 소비재 연구·생활 정보처럼 금융시장이나 주요 산업에 미치는 영향이 작은 기사
- 최고경영자·최고재무책임자 교체가 아닌 통상적인 중간관리자 선임 기사
- 실적 수치가 아직 발표되지 않은 실적 발표 예정·미리보기 기사
- 대학 캠퍼스 개설, 제재 없는 단순 경고, 계정 차단, 결과나 합의가 없는 회의처럼 경제적 파급력이 작은 단발성 소식
- 무엇이 새로 공개됐는지 불명확하고 계약·고객·매출·생산·정부 도입 등 구체성이 없는 홍보성 제품·플랫폼 소개
- 구속력 있는 계약·발주·투자·상용 도입이 없는 비구속적 업무협약(MOU)
- 소비자 쇼핑 설문, 가능성만 설명한 보고서, 기업 가이던스가 아닌 일반 수요 전망
- 개별 소매점의 결제수단 도입, 뚜렷한 새 원인이 없는 2% 안팎의 일반 지수 등락, 비핵심 기관의 경영진 보수 기사
- 아직 실행되지 않은 시험 일정·장기 목표와 기사 작성일보다 3일 넘게 오래된 사건을 새 후속 사실이나 새 공개 없이 다시 소개한 기사
- 자산운용사의 투자자 서한·보유종목 평가, 결정되지 않은 서비스·투자수단 검토, 업계 단체의 비구속적 지침
- 판결·명령 없이 법원이 의문만 표시한 기사, 통상적인 항공 노선·코드셰어 확대, 새 촉매 없는 과거 주가 수익률 재소개
- 실적·판매·생산·투자 변화 없는 소비재·주류·자동차 신제품 소개
- 새로운 정책·시장 개입 없이 통화가 안정세라는 단순 시황, 새 자료 없는 전문가 위험 경고, 피해 규모 없는 사기 주의보
- 성장 기사 안에서 같은 매출 지표의 연도별 금액이 서로 모순되는 등 원문 자체의 수치 관계를 신뢰하기 어려운 기사

속보일 필요는 없습니다. 의미 있는 새 경제 소식이면 모두 선택하세요. 기사에 있는 사실만 사용하세요.
같은 시장 하락을 지수·개별 종목·산업 관점으로 나눈 기사, 같은 회사의 한 분기 실적을 배당·순이익 관점으로 나눈 기사, 같은 정부 정책의 세부 분야만 바꾼 기사는 동일 사건입니다. 가장 구체적인 하나만 선택하세요.
선택하려면 누가 무엇을 새로 발표·결정·변경했거나 어떤 사건이 새로 발생했는지 명확히 말할 수 있어야 합니다.
경제 초급 독자도 주체를 알 수 있도록 국가·기관·기업 이름을 제목이나 요약에 명시하세요. 원문에 국가가 있는데 생략하지 마세요.
일반 영어 단어를 한국어 문장에 남기지 말고 자연스럽게 번역하세요. GPIF·FSSAI·OFS처럼 낯선 약어는 한국어 기관명이나 뜻을 먼저 쓰고 괄호 안에 약어를 적으세요.
`boe/d` 같은 전문 단위는 `석유환산배럴/일`처럼 초급 독자가 뜻을 알 수 있게 풀어 쓰세요.
`ISR`은 `정보·감시·정찰(ISR)`처럼 뜻을 먼저 설명하고, 현지 통화는 어느 나라 달러인지 명시하세요.
직역하면 뜻이 어색한 금융·경영 용어는 한국에서 통용되는 표현으로 옮기고, 확신할 수 없으면 해당 기사를 제외하세요.
요약은 핵심 사실과 주요 수치·시점을 먼저 쓰고 110자 이내의 1~2문장으로 작성하세요.
제목의 모든 핵심 숫자를 본문에도 같은 의미로 포함하고, 본문이 설명하지 않는 숫자를 제목에만 쓰지 마세요.
퍼센트 수치를 쓸 때는 매출·순이익·생산량·가격처럼 무엇이 변했는지 반드시 같은 문장에 명시하세요. `40% 성장률`처럼 지표가 불분명한 표현은 금지합니다.
요약의 모든 문장은 뉴스 독자에게 보고하듯 자연스러운 정중한 보고체로 쓰고 `했습니다.`, `됐습니다.`, `입니다.`처럼 끝내세요. 제목처럼 `상승`, `발표` 같은 명사형으로 끝내지 마세요.
기사 정보가 부족하면 `TEXT_TOO_SHORT`, `N/A`, `내용이 부족합니다` 같은 대체 문구를 만들지 말고 해당 기사를 선택 결과에서 제외하세요.
`시장 영향:`, `관전 포인트` 같은 상투적 해설을 덧붙이거나 물결표가 포함된 `~입니다`를 기계적으로 붙이지 마세요.
기사에 직접 명시된 시장 반응만 덧붙이고, 원문에 없는 전망·인과관계·투자 판단이나 상투적인 시장 영향 문구를 만들지 마세요.
통제된 시험에서 관찰된 행동을 실제 시스템 탈출이나 현실 사고처럼 과장하지 말고, `중요한 지표입니다` 같은 편집자 평가를 덧붙이지 마세요.
원문의 숫자와 단위를 그대로 사용하고 임의로 환산하거나 새로운 숫자를 만들지 마세요.
펀드·지수·자회사의 수익률이나 실적을 기사에 함께 언급된 다른 기업의 수치로 바꾸지 마세요. 숫자를 쓸 때는 원문에서 그 숫자를 발표한 주체를 같은 문장에 명시하세요.
`199,000건으로 증가`처럼 도달한 수준과 `199,000건 증가`처럼 증가분을 구분하세요. M·B 같은 영문 축약 단위와 S$ 같은 통화 표시는 한국어 독자가 오해하지 않도록 풀어 쓰세요.
기사 종류와 무관하게 `영향 범위`, `변화 규모`, `시장 즉시성` 세 기준으로 중요도를 판단하세요.
- 7~8점은 주요 경제 소식, 9~10점은 화면과 알림에서 긴급 속보로 사용됩니다.
- 9점은 국가·전체 시장·주요 산업·세계적 기업에 미치는 범위, 평소보다 현저하거나 예상 밖인 변화, 가격과 기대에 빠르게 반영될 즉시성 중 두 가지 이상을 강하게 충족하는 굵직한 속보에만 사용하세요.
- 10점은 세 기준을 모두 충족하며 세계 시장이나 금융시스템에 충격을 줄 수 있는 극히 드문 사건에만 사용하세요.
- 8점은 주요 기업 실적·산업 변화·정책·규제처럼 영향이 크지만 광범위한 즉시 재평가까지 요구하지 않는 주요 경제 소식입니다.
- 7점은 의미 있는 새 경제 사실이지만 영향 범위가 제한적인 소식입니다.
- 통상적인 리콜과 공식 제품·기능 공개는 원칙적으로 7점이며, 단순 미래 GDP 전망과 통상적인 경제 전망은 9~10점을 주지 마세요.
- 호르무즈·바브엘만데브·홍해 등 핵심 운송로에서 상선 피격과 사망·운항 차질이 확인되면 세계 교역과 에너지 공급에 즉시 영향을 줄 수 있으므로 9점 후보로 평가하세요.
- 산업 제약·부족·전망·동향을 다룬 폭넓은 분석은 주요 경제 소식으로 남길 수 있지만, 새로 확정된 조치나 즉각적인 시장 충격이 없으면 최대 8점입니다.
- 일반적인 분기 실적, 단순 지수 최고치, 제품 공개, 결과 없는 회의에는 9~10점을 주지 마세요. 반대로 기업·정책·지정학 등 어떤 종류든 위 세 기준을 충족하면 속보로 평가하세요.
- 규제안 심사·승인 보류처럼 최종 결정이 아닌 절차와 일반적인 기업 인수·지분 매각은 원칙적으로 최대 8점입니다.

분류 기준:
- `indicator`: 정부·중앙은행·공식기관이 발표한 물가·고용·성장률·생산·소비 등 수치형 경제지표
- `market`: 주식·채권·외환·원자재·가상자산 시장과 중앙은행 통화정책
- `geopolitics`: 전쟁·외교·제재·국가 간 갈등. 국내 절도·통신 장애·기업 전력망 운영 문제는 여기에 넣지 않음
- `policy`: 법률·세제·정부 정책과 시장·산업·다수 기업 또는 소비자에게 적용되는 규제
- `corporate`: 기업 실적·인수합병·기술·공급망·소송과 특정 기업만 대상으로 한 규제 집행

[최근 24시간 저장 뉴스 - 중복 비교 전용]
{json.dumps(recent_news, ensure_ascii=False)}
이 목록은 중복 비교에만 사용하세요. 새 기사의 번역·요약에서 사실 근거로 사용하지 마세요.

[후보]
{json.dumps(candidates, ensure_ascii=False)}

[출력]
JSON 리스트만 반환하세요. 선택할 기사가 없으면 []를 반환하세요.
각 항목 형식:
{{
  "temp_id": 후보의 temp_id,
  "source_ref": 후보의 source_ref를 한 글자도 바꾸지 않고 복사,
  "source_title": 후보의 title을 한 글자도 바꾸지 않고 복사,
  "title": 핵심 사건이 드러나는 가급적 35자, 완결성을 위해 최대 55자 한국어 제목,
  "content": 확인된 사실만 담은 한국어 110자 이내 1~2문장 요약,
  "importance_score": 기존 저장 기준과 동일한 7~10,
  "category": "market" | "indicator" | "geopolitics" | "corporate" | "policy",
  "news_type": "breaking" | "new_development" | "official_announcement" | "follow_up",
  "selection_reason": 새로 발생·발표·결정·변경된 사실을 구체적으로 적은 한 문장
    }}
"""


def _quality_repair_prompt(repair_candidates: list[dict]) -> str:
    return f"""
아래 기사는 경제 뉴스로 선택됐지만 생성된 제목 또는 요약이 품질 검사에 실패했습니다.
기사 선택 자체를 다시 판단하지 말고 제목과 요약의 품질 오류만 수정하세요.
원문 후보에 없는 사실·숫자·인과관계를 추가하지 마세요.
원문이 가능성·추정으로 보도한 사건은 제목도 확정형으로 쓰지 마세요.
원문이 수치로 증가·감소를 보도했다면 핵심 변화 수치 하나 이상을 요약에 포함하세요.
원문 금액을 임의로 다른 통화로 환산하지 말고, 원문에 함께 나온 통화와 금액만 사용하세요.
million·billion·trillion이나 M·B·T 단위는 자릿수를 정확히 계산해 자연스러운 한국어 단위로 쓰고 영문 축약을 남기지 마세요.
직접 행동한 자회사·특수목적법인(SPC)·컨소시엄을 모회사로 바꾸지 말고, 직접 행위자와 관계를 정확히 쓰세요.
통제된 보안 시험이나 승인된 평가 환경의 관찰 결과를 현실의 해킹·침해 사고로 확대하지 마세요.
각 항목의 temp_id, source_ref, source_title은 한 글자도 바꾸지 마세요.
완전히 고칠 수 없는 항목은 결과에서 제외하세요.

[수정 대상]
{json.dumps(repair_candidates, ensure_ascii=False)}

[출력]
수정된 항목만 아래 형식의 순수한 JSON 배열로 반환하세요.
{{
  "temp_id": 원래 temp_id,
  "source_ref": 원래 source_ref,
  "source_title": 원래 source_title,
  "title": 가급적 35자, 완결성을 위해 최대 55자 한국어 제목,
  "content": 확인된 사실만 담은 한국어 110자 이내 1~2문장 요약,
  "importance_score": 7~10 정수,
  "category": "market" | "indicator" | "geopolitics" | "corporate" | "policy",
  "news_type": "breaking" | "new_development" | "official_announcement" | "follow_up",
  "selection_reason": 새로 발생·발표·결정·변경된 사실 한 문장
}}
"""


def _decision_to_item(
    decision: dict,
    articles: list[dict],
    seen_refs: set[str],
) -> tuple[dict | None, str | None]:
    if not isinstance(decision, dict):
        return None, None

    temp_id = decision.get("temp_id")
    source_ref = decision.get("source_ref")
    source_title = decision.get("source_title")
    if not isinstance(temp_id, int) or not 0 <= temp_id < len(articles):
        return None, None
    if (
        not isinstance(source_ref, str)
        or not isinstance(source_title, str)
        or source_ref != articles[temp_id].get("provider_article_id")
        or source_title != articles[temp_id].get("raw_title")
    ):
        LOGGER.warning(
            "Discarding AI decision with mismatched source identity: "
            "temp_id=%s source_ref=%s",
            temp_id,
            source_ref,
        )
        return None, None
    if source_ref in seen_refs:
        return None, None

    news_type = decision.get("news_type")
    title = decision.get("title")
    content = decision.get("content")
    importance_score = decision.get("importance_score")
    category = decision.get("category")
    selection_reason = decision.get("selection_reason")

    def failed(reason: str) -> tuple[None, str]:
        LOGGER.warning(
            "Discarding AI decision that failed quality validation: "
            "source_ref=%s reason=%s",
            source_ref,
            reason,
        )
        return None, reason

    if (
        news_type not in SELECTABLE_NEWS_TYPES
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(content, str)
        or not content.strip()
        or isinstance(importance_score, bool)
        or not isinstance(importance_score, (int, float))
        or importance_score < 7
        or category not in SELECTABLE_CATEGORIES
        or not isinstance(selection_reason, str)
        or not selection_reason.strip()
    ):
        return failed("invalid_fields")

    title = _normalize_known_korean_terms(articles[temp_id], title.strip())
    content = _normalize_known_korean_terms(articles[temp_id], content.strip())
    category = _normalize_category(articles[temp_id], category)
    if _has_incomplete_title(title, content):
        return failed("incomplete_title")
    if _has_opposite_title_content_direction(title, content):
        return failed("opposing_title_content_direction")
    if _has_definitive_title_for_speculative_event(
        articles[temp_id],
        title,
        content,
    ):
        return failed("confidence_mismatch")
    if _overstates_controlled_security_test(articles[temp_id], title, content):
        return failed("controlled_test_overstated_as_incident")
    if _misstates_from_to_level_as_change(articles[temp_id], title):
        return failed("level_rewritten_as_change_amount")
    if _misattributes_metric_percentage(articles[temp_id], title, content):
        return failed("metric_percentage_misattribution")
    if _has_malformed_korean_amount(f"{title} {content}"):
        return failed("malformed_korean_amount")
    if _has_unsupported_currency_conversion(articles[temp_id], title, content):
        return failed("unsupported_currency_conversion")
    if _has_mistranslated_english_large_unit(articles[temp_id], title, content):
        return failed("mistranslated_large_unit")
    if _title_numbers_missing_from_content(title, content):
        return failed("title_numbers_missing_from_content")
    if not _contains_korean(title) or not _contains_korean(content):
        return failed("not_korean")
    if _has_untranslated_english_prose(title, content):
        return failed("untranslated_english_prose")
    if _has_unlocalized_financial_or_foreign_notation(title, content):
        return failed("unlocalized_financial_or_foreign_notation")
    if _has_unexplained_specialist_acronym(title, content):
        return failed("unexplained_specialist_acronym")
    if _has_ambiguous_percentage_growth(content):
        return failed("unnamed_percentage_metric")
    if _is_missing_primary_change_metric(articles[temp_id], title, content):
        return failed("missing_primary_metric")
    if _has_misattributed_fund_return(articles[temp_id], title, content):
        return failed("misattributed_fund_return")
    if _misstates_primary_transaction_actor(articles[temp_id], title, content):
        return failed("incorrect_primary_transaction_actor")
    if _misstates_indirect_transaction_actor(articles[temp_id], title):
        return failed("incorrect_indirect_transaction_actor")
    if _missing_source_geography(articles[temp_id], title, content, category):
        return failed("missing_source_geography")
    if not _is_valid_report_summary(content):
        return failed("invalid_report_style_summary")
    if _unsupported_summary_numbers(articles[temp_id], title, content):
        return failed("unsupported_source_numbers")

    item = articles[temp_id].copy()
    item["normalized_title"] = title
    item["normalized_content"] = content
    item["importance_score"] = _normalize_importance_score(
        articles[temp_id],
        importance_score,
    )
    item["category"] = category
    item["news_type"] = news_type
    item["selection_reason"] = selection_reason.strip()
    return item, None


def select_and_summarize(
    articles: list[dict],
    generator,
    *,
    batch_size: int = 10,
    recent_news: list[dict] | None = None,
) -> SelectionResult:
    """Keep new, economically relevant developments and summarize them in Korean."""
    if not articles:
        return SelectionResult()

    recent_news_context = [
        {
            "title": item.get("title") or "",
            "content": item.get("content") or "",
        }
        for item in (recent_news or [])[:300]
        if item.get("title") or item.get("content")
    ]

    candidate_indexes = [
        index
        for index, article in enumerate(articles)
        if article.get("source_id") not in EXCLUDED_FEED_SOURCE_IDS
        and not _is_obvious_analysis_title(article["raw_title"])
        and not _is_minor_card_product_change(article)
        and not _is_repackaged_old_event(article)
        and not _is_low_value_item(article)
    ]
    if not candidate_indexes:
        return SelectionResult()

    selected = []
    seen_refs = set()
    retryable_urls = set()
    for start in range(0, len(candidate_indexes), batch_size):
        batch_indexes = candidate_indexes[start : start + batch_size]
        candidates = [
            {
                "temp_id": index,
                "source_ref": article["provider_article_id"],
                "market_scope": article["market_scope"],
                "source_name": article["source_name"],
                "published_at": article["published_at"],
                "title": article["raw_title"],
                "description": article["raw_description"],
                "content": article["raw_content"],
            }
            for index in batch_indexes
            for article in [articles[index]]
        ]
        selection_prompt = _selection_prompt(candidates, recent_news_context[:100])
        for selection_attempt in range(2):
            try:
                response_text = _response_text(generator(selection_prompt))
                batch_decisions = _load_decisions(response_text, generator)
                break
            except ValueError:
                if selection_attempt == 1:
                    raise

        repair_payload_by_ref = {}
        for decision in batch_decisions:
            item, failure_reason = _decision_to_item(
                decision,
                articles,
                seen_refs,
            )
            if item is not None:
                selected.append(item)
                seen_refs.add(item["provider_article_id"])
                continue
            if failure_reason is None:
                continue

            temp_id = decision["temp_id"]
            source_ref = decision["source_ref"]
            candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["temp_id"] == temp_id
                    and candidate["source_ref"] == source_ref
                ),
                None,
            )
            if candidate is not None:
                repair_payload_by_ref[source_ref] = {
                    "source_article": candidate,
                    "rejected_draft": decision,
                    "validation_error": failure_reason,
                }

        if not repair_payload_by_ref:
            continue

        unresolved_refs = set(repair_payload_by_ref)
        try:
            repair_response = generator(
                _quality_repair_prompt(list(repair_payload_by_ref.values()))
            )
            repaired_decisions = _decode_json_array(_response_text(repair_response))
        except Exception as error:
            LOGGER.warning(
                "Focused AI quality repair failed for %s article(s): %s",
                len(unresolved_refs),
                error,
            )
        else:
            for repaired_decision in repaired_decisions:
                if not isinstance(repaired_decision, dict):
                    continue
                repaired_ref = repaired_decision.get("source_ref")
                if repaired_ref not in unresolved_refs:
                    continue
                item, failure_reason = _decision_to_item(
                    repaired_decision,
                    articles,
                    seen_refs,
                )
                if item is None:
                    continue
                selected.append(item)
                seen_refs.add(item["provider_article_id"])
                unresolved_refs.remove(repaired_ref)

        for unresolved_ref in unresolved_refs:
            temp_id = repair_payload_by_ref[unresolved_ref]["rejected_draft"][
                "temp_id"
            ]
            retryable_urls.add(articles[temp_id]["original_url"])

    deduplicated = _deduplicate_against_recent(
        _deduplicate_selected(selected),
        recent_news_context,
    )
    return SelectionResult(deduplicated, retryable_urls=retryable_urls)
