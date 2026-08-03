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
    return any(
        left
        and right
        and SequenceMatcher(None, left, right).ratio() >= 0.62
        for left, right in comparisons
    )


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


def _selection_prompt(candidates: list[dict]) -> str:
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

속보일 필요는 없습니다. 의미 있는 새 경제 소식이면 모두 선택하세요. 기사에 있는 사실만 사용하세요.
선택하려면 누가 무엇을 새로 발표·결정·변경했거나 어떤 사건이 새로 발생했는지 명확히 말할 수 있어야 합니다.
요약은 핵심 사실과 주요 수치·시점을 먼저 쓰고 110자 이내의 1~2문장으로 작성하세요.
기사에 직접 명시된 시장 반응만 덧붙이고, 원문에 없는 전망·인과관계·투자 판단이나 상투적인 시장 영향 문구를 만들지 마세요.
원문의 숫자와 단위를 그대로 사용하고 임의로 환산하거나 새로운 숫자를 만들지 마세요.
중요도 9~10은 주요국 중앙은행·정부의 중대 정책, 전쟁·금융시스템 충격, 세계적 대기업의 중대 사건에만 사용하세요.
일반 기업 실적·산업 소식은 보통 7, 국가나 대형 시장에 직접 영향이 큰 경우에만 8을 사용하세요.

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
) -> list[dict]:
    """Keep new, economically relevant developments and summarize them in Korean."""
    if not articles:
        return []

    candidate_indexes = [
        index
        for index, article in enumerate(articles)
        if article.get("source_id") not in EXCLUDED_FEED_SOURCE_IDS
        and not _is_obvious_analysis_title(article["raw_title"])
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
        selection_prompt = _selection_prompt(candidates)
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
        if not _contains_korean(title) or not _contains_korean(content):
            LOGGER.warning(
                "Discarding AI decision that is not Korean: source_ref=%s",
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

        item = articles[temp_id].copy()
        item["normalized_title"] = title.strip()
        item["normalized_content"] = content.strip()
        item["importance_score"] = importance_score
        item["category"] = category
        item["news_type"] = news_type
        item["selection_reason"] = selection_reason.strip()
        selected.append(item)
        seen_refs.add(source_ref)

    return _deduplicate_selected(selected)
