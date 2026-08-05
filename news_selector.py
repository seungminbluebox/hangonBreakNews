"""AI-assisted selection and Korean summarization for normalized news articles."""

from difflib import SequenceMatcher
import json
import logging
import re


LOGGER = logging.getLogger(__name__)


SELECTABLE_NEWS_TYPES = {
    "breaking",
    "new_development",
    "official_announcement",
    "follow_up",
}
SELECTABLE_CATEGORIES = {"market", "indicator", "geopolitics", "corporate"}
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
                    "title": {"type": "string", "maxLength": 35},
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
EVENT_TOKEN_SYNONYMS = (
    ("연방항공청", "faa"),
    ("미국주식시장", "미국 주식시장"),
    ("역사적", "사상"),
    ("고점", "최고치"),
    ("국제 유가", "유가"),
    ("원유 가격", "유가"),
    ("원유", "유가"),
    ("급등", "상승"),
    ("급락", "하락"),
)
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
    if (
        any(marker in title for marker in ("calls", "describes"))
        and any(marker in title for marker in ("sales decline", "sales drop"))
        and any(marker in text for marker in ("good month", "consistent with"))
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


def _normalize_known_korean_terms(article: dict, value: str) -> str:
    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    if "nissan" in source_text:
        value = value.replace("니산", "닛산")
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
    return value


def _has_untranslated_english_prose(title: str, content: str) -> bool:
    return bool(
        re.search(r"(?<![A-Za-z])[a-z]{4,}(?![A-Za-z])", f"{title} {content}")
    )


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


def _normalize_importance_score(article: dict, importance_score):
    if importance_score < 9:
        return importance_score

    source_text = " ".join(
        article.get(field) or ""
        for field in ("raw_title", "raw_description", "raw_content")
    ).casefold()
    systemic_markers = (
        "intervention",
        "emergency",
        "trading halt",
        "market crash",
        "financial crisis",
        "bank run",
        "sovereign default",
        "war",
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
    has_systemic_marker = any(marker in source_text for marker in systemic_markers)

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
    summary_numbers = _numeric_tokens(f"{title} {content}")
    return summary_numbers - source_numbers


def _similarity_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", value.casefold()).strip()


def _is_same_event(first: dict, second: dict) -> bool:
    first_title = _similarity_text(first["normalized_title"])
    second_title = _similarity_text(second["normalized_title"])
    first_content = _similarity_text(first["normalized_content"])
    second_content = _similarity_text(second["normalized_content"])
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


def _has_shared_event_signature(
    first_title: str,
    first_content: str,
    second_title: str,
    second_content: str,
) -> bool:
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

    shared_numbers = _event_numeric_tokens(
        f"{first_title} {first_content}"
    ) & _event_numeric_tokens(f"{second_title} {second_content}")
    return len(shared_tokens) >= 2 and bool(shared_numbers)


def _is_same_recent_event(article: dict, recent_item: dict) -> bool:
    current_title = article.get("normalized_title") or ""
    recent_title = recent_item.get("title") or ""
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
        article.get("normalized_content") or "",
        recent_title,
        recent_item.get("content") or "",
    ):
        return True

    current_text = _similarity_text(current_title)
    recent_text = _similarity_text(recent_title)
    return bool(
        current_text
        and recent_text
        and SequenceMatcher(None, current_text, recent_text).ratio() >= 0.82
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
    if any(
        marker in current_text and marker not in recent_text
        for marker in MATERIAL_FOLLOW_UP_MARKERS
    ):
        return True
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
    return sum(
        len(article.get(field) or "")
        for field in ("raw_title", "raw_description", "raw_content")
    )


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

다음은 제외하세요.
- 새로운 사실이 없는 전망, 칼럼, 비교, 순위, 추천, 사용법, 회고, 단순 해설
- 생활·상품·자동차 소개와 홍보성 기사
- 과거 사실만 다시 설명하는 기사
- 주가·코인 가격의 혼조나 등락만 나열한 시황. 단, 같은 기사에서 실적 발표·가이던스 변경·정책 결정 같은 새 원인을 명시하면 그 원인만 선택 가능
- `적정 가치인가`, `무슨 일이 있나`, `영향은 미미했다`처럼 기자가 기존 사실을 평가하는 기사
- 증권사 매수·매도 의견, 목표주가, 종목 추천 및 `시장이 말하는 것` 형식의 분석 기사
- 공식 통계나 정책 발표가 아닌 설문조사·연구 결과만 소개하는 기사
- 지난달·지난 분기 사건에 대한 새로운 조치나 결과 없이 관계자의 평가만 추가한 기사
- 후보 안에 동일 사건을 다룬 기사가 여러 개면 원문 정보가 가장 구체적인 하나만 선택
- 최근 저장 뉴스와 같은 사건이면 제외. 단, 합의·승인·완료·취소·새 수치처럼 상태가 실제로 달라진 후속 보도는 `follow_up`으로 선택
- 새 발표나 조치가 없는 업계 전망·역사적 수준 평가, 단순 실험 연구, 개인용 멤버십·혜택·수수료 변경
- 회사 실적·가이던스·시장점유율과 연결되지 않은 개별 상품 판매 기록, 정기 신고·납부 안내, 기업 순위·수상·명단 기사
- 공항 편의 서비스 도입, 지역 상점 단속, 소비재 연구·생활 정보처럼 금융시장이나 주요 산업에 미치는 영향이 작은 기사
- 최고경영자·최고재무책임자 교체가 아닌 통상적인 중간관리자 선임 기사
- 실적 수치가 아직 발표되지 않은 실적 발표 예정·미리보기 기사
- 대학 캠퍼스 개설, 제재 없는 단순 경고, 계정 차단, 결과나 합의가 없는 회의처럼 경제적 파급력이 작은 단발성 소식
- 계약·고객·매출·생산·정부 도입처럼 상업적 결과가 확인되지 않은 제품·플랫폼 출시
- 소비자 쇼핑 설문, 가능성만 설명한 보고서, 기업 가이던스가 아닌 일반 수요 전망

속보일 필요는 없습니다. 의미 있는 새 경제 소식이면 모두 선택하세요. 기사에 있는 사실만 사용하세요.
선택하려면 누가 무엇을 새로 발표·결정·변경했거나 어떤 사건이 새로 발생했는지 명확히 말할 수 있어야 합니다.
경제 초급 독자도 주체를 알 수 있도록 국가·기관·기업 이름을 제목이나 요약에 명시하세요. 원문에 국가가 있는데 생략하지 마세요.
일반 영어 단어를 한국어 문장에 남기지 말고 자연스럽게 번역하세요. GPIF·FSSAI·OFS처럼 낯선 약어는 한국어 기관명이나 뜻을 먼저 쓰고 괄호 안에 약어를 적으세요.
`boe/d` 같은 전문 단위는 `석유환산배럴/일`처럼 초급 독자가 뜻을 알 수 있게 풀어 쓰세요.
직역하면 뜻이 어색한 금융·경영 용어는 한국에서 통용되는 표현으로 옮기고, 확신할 수 없으면 해당 기사를 제외하세요.
요약은 핵심 사실과 주요 수치·시점을 먼저 쓰고 110자 이내의 1~2문장으로 작성하세요.
퍼센트 수치를 쓸 때는 매출·순이익·생산량·가격처럼 무엇이 변했는지 반드시 같은 문장에 명시하세요. `40% 성장률`처럼 지표가 불분명한 표현은 금지합니다.
요약의 모든 문장은 뉴스 독자에게 보고하듯 자연스러운 정중한 보고체로 쓰고 `했습니다.`, `됐습니다.`, `입니다.`처럼 끝내세요. 제목처럼 `상승`, `발표` 같은 명사형으로 끝내지 마세요.
기사 정보가 부족하면 `TEXT_TOO_SHORT`, `N/A`, `내용이 부족합니다` 같은 대체 문구를 만들지 말고 해당 기사를 선택 결과에서 제외하세요.
`시장 영향:`, `관전 포인트` 같은 상투적 해설을 덧붙이거나 물결표가 포함된 `~입니다`를 기계적으로 붙이지 마세요.
기사에 직접 명시된 시장 반응만 덧붙이고, 원문에 없는 전망·인과관계·투자 판단이나 상투적인 시장 영향 문구를 만들지 마세요.
원문의 숫자와 단위를 그대로 사용하고 임의로 환산하거나 새로운 숫자를 만들지 마세요.
기사 종류와 무관하게 `영향 범위`, `변화 규모`, `시장 즉시성` 세 기준으로 중요도를 판단하세요.
- 7~8점은 주요 경제 소식, 9~10점은 화면과 알림에서 긴급 속보로 사용됩니다.
- 9점은 국가·전체 시장·주요 산업·세계적 기업에 미치는 범위, 평소보다 현저하거나 예상 밖인 변화, 가격과 기대에 빠르게 반영될 즉시성 중 두 가지 이상을 강하게 충족하는 굵직한 속보에만 사용하세요.
- 10점은 세 기준을 모두 충족하며 세계 시장이나 금융시스템에 충격을 줄 수 있는 극히 드문 사건에만 사용하세요.
- 8점은 주요 기업 실적·산업 변화·정책·규제처럼 영향이 크지만 광범위한 즉시 재평가까지 요구하지 않는 주요 경제 소식입니다.
- 7점은 의미 있는 새 경제 사실이지만 영향 범위가 제한적인 소식입니다.
- 일반적인 분기 실적, 단순 지수 최고치, 제품 공개, 결과 없는 회의에는 9~10점을 주지 마세요. 반대로 기업·정책·지정학 등 어떤 종류든 위 세 기준을 충족하면 속보로 평가하세요.

분류 기준:
- `indicator`: 정부·중앙은행·공식기관이 발표한 물가·고용·성장률·생산·소비 등 수치형 경제지표
- `market`: 주식·채권·외환·원자재·가상자산 시장과 통화·금융정책
- `geopolitics`: 전쟁·외교·제재·국가 간 갈등
- `corporate`: 기업 실적·인수합병·기술·공급망과 특정 산업에 직접 적용되는 규제

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
  "title": 핵심 사건이 드러나는 35자 이내 한국어 제목,
  "content": 확인된 사실만 담은 한국어 110자 이내 1~2문장 요약,
  "importance_score": 기존 저장 기준과 동일한 7~10,
  "category": "market" | "indicator" | "geopolitics" | "corporate",
  "news_type": "breaking" | "new_development" | "official_announcement" | "follow_up",
  "selection_reason": 새로 발생·발표·결정·변경된 사실을 구체적으로 적은 한 문장
    }}
"""


def select_and_summarize(
    articles: list[dict],
    generator,
    *,
    batch_size: int = 10,
    recent_news: list[dict] | None = None,
) -> list[dict]:
    """Keep new, economically relevant developments and summarize them in Korean."""
    if not articles:
        return []

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
        and not _is_low_value_item(article)
    ]
    if not candidate_indexes:
        return []

    decisions = []
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
                decisions.extend(batch_decisions)
                break
            except ValueError:
                if selection_attempt == 1:
                    raise

    selected = []
    seen_refs = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            continue

        temp_id = decision.get("temp_id")
        source_ref = decision.get("source_ref")
        source_title = decision.get("source_title")
        news_type = decision.get("news_type")
        title = decision.get("title")
        content = decision.get("content")
        importance_score = decision.get("importance_score")
        category = decision.get("category")
        selection_reason = decision.get("selection_reason")
        if not isinstance(temp_id, int) or not 0 <= temp_id < len(articles):
            continue
        if (
            isinstance(source_ref, str)
            and isinstance(source_title, str)
            and (
                source_ref != articles[temp_id].get("provider_article_id")
                or source_title != articles[temp_id].get("raw_title")
            )
        ):
            LOGGER.warning(
                "Discarding AI decision with mismatched source identity: "
                "temp_id=%s source_ref=%s",
                temp_id,
                source_ref,
            )
            continue
        if (
            not isinstance(source_ref, str)
            or source_ref in seen_refs
            or not isinstance(source_title, str)
            or news_type not in SELECTABLE_NEWS_TYPES
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
            continue
        title = _normalize_known_korean_terms(articles[temp_id], title.strip())
        content = _normalize_known_korean_terms(articles[temp_id], content.strip())
        if not _contains_korean(title) or not _contains_korean(content):
            LOGGER.warning(
                "Discarding AI decision that is not Korean: source_ref=%s",
                source_ref,
            )
            continue
        if _has_untranslated_english_prose(title, content):
            LOGGER.warning(
                "Discarding AI decision with untranslated English prose: "
                "source_ref=%s",
                source_ref,
            )
            continue
        if _has_unexplained_specialist_acronym(title, content):
            LOGGER.warning(
                "Discarding AI decision with unexplained specialist acronym: "
                "source_ref=%s",
                source_ref,
            )
            continue
        if _has_ambiguous_percentage_growth(content):
            LOGGER.warning(
                "Discarding AI decision with unnamed percentage metric: "
                "source_ref=%s",
                source_ref,
            )
            continue
        if _missing_source_geography(
            articles[temp_id],
            title,
            content,
            category,
        ):
            LOGGER.warning(
                "Discarding AI decision that omitted source geography: "
                "source_ref=%s",
                source_ref,
            )
            continue
        if not _is_valid_report_summary(content):
            LOGGER.warning(
                "Discarding AI decision with invalid report-style summary: "
                "source_ref=%s",
                source_ref,
            )
            continue
        unsupported_numbers = _unsupported_summary_numbers(
            articles[temp_id],
            title,
            content,
        )
        if unsupported_numbers:
            values = ", ".join(sorted(unsupported_numbers))
            LOGGER.warning(
                "Discarding AI decision with unsupported source numbers: "
                "source_ref=%s values=%s",
                source_ref,
                values,
            )
            continue

        importance_score = _normalize_importance_score(
            articles[temp_id],
            importance_score,
        )

        item = articles[temp_id].copy()
        item["normalized_title"] = title
        item["normalized_content"] = content
        item["importance_score"] = importance_score
        item["category"] = category
        item["news_type"] = news_type
        item["selection_reason"] = selection_reason.strip()
        selected.append(item)
        seen_refs.add(source_ref)

    return _deduplicate_against_recent(
        _deduplicate_selected(selected),
        recent_news_context,
    )
