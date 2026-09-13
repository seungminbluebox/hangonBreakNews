"""GNews API adapter with no database or notification side effects."""

from datetime import datetime, timedelta, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cycle_logging import current_context, log_event, record_failure


GNEWS_TOP_HEADLINES_URL = "https://gnews.io/api/v4/top-headlines"
GNEWS_SEARCH_URL = "https://gnews.io/api/v4/search"
DEFAULT_LOOKBACK_HOURS = 3
BUSINESS_FEEDS = (
    {
        "market_scope": "kr",
        "country": "kr",
        "language": "ko",
        "category": "business",
        "max_articles": 25,
    },
    {
        "market_scope": "us",
        "country": "us",
        "language": "en",
        "category": "business",
        "max_articles": 25,
    },
    {
        "market_scope": "world",
        "country": None,
        "language": "en",
        "category": "business",
        "max_articles": 25,
    },
)
WORLD_FEED = {
    "market_scope": "world",
    "country": None,
    "language": "en",
    "category": "world",
    "max_articles": 25,
}
DEFAULT_FEEDS = BUSINESS_FEEDS + (WORLD_FEED,)
SEARCH_GROUPS = (
    ("ko_macro", "ko", "기준금리 OR 통화정책 OR 한국은행 OR 연준 OR 소비자물가 OR 인플레이션 OR 실업률 OR 비농업고용 OR 국내총생산 OR 경제성장률 OR 경상수지 OR 무역수지 OR 국가부채 OR (환율 AND (급등 OR 급락 OR 개입)) OR (국채 AND (금리 OR 발행))"),
    ("en_macro", "en", '"interest rate" OR "central bank" OR inflation OR CPI OR payrolls OR unemployment OR GDP OR recession OR "bond yields" OR "sovereign debt" OR "currency intervention" OR "trade balance"'),
    ("ko_policy", "ko", "(무역 OR 관세 OR 제재 OR 공급망 OR 에너지 OR 원유 OR 가스 OR 반도체 OR 인프라) AND (협상 OR 협력 OR 합의 OR 투자 OR 규제 OR 수출 OR 수입 OR 감산 OR 증산 OR 중단 OR 봉쇄 OR 지원 OR 요청 OR 논의 OR 재확인)"),
    ("en_policy", "en", "(trade OR tariffs OR sanctions OR energy OR oil OR semiconductor OR infrastructure) AND (talks OR cooperation OR agreement OR investment OR restrictions OR exports OR disruption OR supply)"),
)


class GNewsClient:
    """Small client for the GNews top-headlines endpoint."""

    def __init__(
        self,
        api_key: str,
        opener=urlopen,
        sleeper=time.sleep,
        max_retries=2,
        before_request=None,
    ):
        self.api_key = api_key
        self.opener = opener
        self.sleeper = sleeper
        self.max_retries = max_retries
        self.before_request = before_request or (lambda: None)

    def fetch_top_headlines(
        self,
        *,
        market_scope: str,
        country: str | None,
        language: str,
        category: str,
        max_articles: int,
        fetched_at: datetime,
    ) -> list[dict]:
        fetched_at_utc = fetched_at.astimezone(timezone.utc)
        window_start = fetched_at_utc - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

        def format_utc(value):
            return value.isoformat(timespec="seconds").replace("+00:00", "Z")

        params = {
            "category": category,
            "lang": language,
            "country": country,
            "max": max_articles,
            "from": format_utc(window_start),
            "to": format_utc(fetched_at_utc),
            "apikey": self.api_key,
        }
        query = urlencode({key: value for key, value in params.items() if value is not None})
        request = Request(
            f"{GNEWS_TOP_HEADLINES_URL}?{query}",
            headers={"User-Agent": "Hangon-BreakingNews/1.0"},
        )
        payload = self._fetch_payload(request)

        normalized_articles = [
            normalize_article(
                article,
                market_scope=market_scope,
                fetched_at=fetched_at,
            )
            for article in payload["articles"]
        ]
        return [
            article
            for article in normalized_articles
            if datetime.fromisoformat(article["published_at"]) >= window_start
        ]

    def fetch_search(
        self,
        *,
        query: str,
        language: str,
        market_scope: str,
        max_articles: int,
        fetched_at: datetime,
    ) -> list[dict]:
        fetched_at_utc = fetched_at.astimezone(timezone.utc)
        window_start = fetched_at_utc - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

        def format_utc(value):
            return value.isoformat(timespec="seconds").replace("+00:00", "Z")

        params = {
            "q": query,
            "lang": language,
            "in": "title,description",
            "sortby": "publishedAt",
            "max": max_articles,
            "from": format_utc(window_start),
            "to": format_utc(fetched_at_utc),
            "apikey": self.api_key,
        }
        request = Request(
            f"{GNEWS_SEARCH_URL}?{urlencode(params)}",
            headers={"User-Agent": "Hangon-BreakingNews/1.0"},
        )
        payload = self._fetch_payload(request)
        normalized_articles = [
            normalize_article(article, market_scope=market_scope, fetched_at=fetched_at)
            for article in payload["articles"]
        ]
        return [
            article
            for article in normalized_articles
            if datetime.fromisoformat(article["published_at"]) >= window_start
        ]

    def _fetch_payload(self, request):
        for attempt in range(self.max_retries + 1):
            try:
                self.before_request()
                with self.opener(request, timeout=15) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                is_temporary = error.code == 429 or 500 <= error.code < 600
                if not is_temporary or attempt == self.max_retries:
                    raise
                retry_after = error.headers.get("Retry-After") if error.headers else None
                delay = float(retry_after) if retry_after else float(2**attempt)
                self.sleeper(delay)
            except (URLError, TimeoutError):
                if attempt == self.max_retries:
                    raise
                self.sleeper(float(2**attempt))


def collect_default_headlines(
    client: GNewsClient,
    *,
    fetched_at: datetime,
    sleeper=time.sleep,
    delay_seconds: float = 1.1,
    max_articles: int | None = None,
) -> list[dict]:
    """Fetch all business and world feeds and remove exact duplicates."""
    return _collect_headlines(
        client,
        feeds=DEFAULT_FEEDS,
        fetched_at=fetched_at,
        sleeper=sleeper,
        delay_seconds=delay_seconds,
        max_articles=max_articles,
    )


def _collect_headlines(
    client: GNewsClient,
    *,
    feeds,
    fetched_at: datetime,
    sleeper=time.sleep,
    delay_seconds: float = 1.1,
    max_articles: int | None = None,
) -> list[dict]:
    collected = []
    seen_article_ids = set()
    seen_urls = set()

    for index, feed in enumerate(feeds):
        feed = feed.copy()
        feed_max_articles = feed.pop("max_articles")
        articles = client.fetch_top_headlines(
            **feed,
            max_articles=(
                max_articles if max_articles is not None else feed_max_articles
            ),
            fetched_at=fetched_at,
        )
        for article in articles:
            article_id = article["provider_article_id"]
            original_url = article["original_url"]
            if article_id in seen_article_ids or original_url in seen_urls:
                continue
            seen_article_ids.add(article_id)
            seen_urls.add(original_url)
            collected.append(article)

        if index < len(feeds) - 1:
            sleeper(delay_seconds)

    return collected


def _merge_unique_articles(articles) -> list[dict]:
    collected = []
    seen_article_ids = set()
    seen_urls = set()
    for article in articles:
        article_id = article.get("provider_article_id")
        original_url = article.get("original_url")
        if (article_id and article_id in seen_article_ids) or (
            original_url and original_url in seen_urls
        ):
            continue
        if article_id:
            seen_article_ids.add(article_id)
        if original_url:
            seen_urls.add(original_url)
        collected.append(article)
    return collected


def _safe_fetch_failure_reason(error) -> str:
    if isinstance(error, HTTPError):
        return "http_error"
    if isinstance(error, (URLError, TimeoutError)):
        return "network_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    return "unexpected_error"


class ScheduledHeadlineCollector:
    """Fetch world every cycle and one search every other cycle, revisiting groups every 40 minutes."""

    def __init__(self):
        self.cycle_index = 0

    def __call__(
        self,
        client: GNewsClient,
        *,
        fetched_at: datetime,
        sleeper=time.sleep,
        delay_seconds: float = 1.1,
        max_articles: int | None = None,
    ) -> list[dict]:
        cycle_index = self.cycle_index
        self.cycle_index += 1
        if cycle_index % 2 == 0:
            return _collect_headlines(
                client,
                feeds=DEFAULT_FEEDS,
                fetched_at=fetched_at,
                sleeper=sleeper,
                delay_seconds=delay_seconds,
                max_articles=max_articles,
            )

        world = _collect_headlines(
            client,
            feeds=(WORLD_FEED,),
            fetched_at=fetched_at,
            sleeper=sleeper,
            delay_seconds=delay_seconds,
            max_articles=max_articles,
        )
        group_name, language, query = SEARCH_GROUPS[(cycle_index // 2) % len(SEARCH_GROUPS)]
        if delay_seconds:
            sleeper(delay_seconds)
        try:
            search = client.fetch_search(
                query=query,
                language=language,
                market_scope="kr" if language == "ko" else "world",
                max_articles=max_articles if max_articles is not None else 25,
                fetched_at=fetched_at,
            )
            search_count = len(search)
            search = _merge_unique_articles(search)
        except Exception as error:
            reason = _safe_fetch_failure_reason(error)
            record_failure("fetch_failures", stage="fetch", reason=reason)
            log_event("gnews_search_failed", level=40, stage="fetch", reason=reason)
            search = []
            search_count = 0
        context = current_context()
        if context is not None:
            context.update(search_group=group_name, search_fetched=search_count)
        return _merge_unique_articles(world + search)


def normalize_article(article: dict, market_scope: str, fetched_at: datetime) -> dict:
    """Convert one GNews article into the project's provider-neutral shape."""
    source = article["source"]
    source_host = (urlparse(source["url"]).hostname or "").lower()
    if source_host.startswith("www."):
        source_host = source_host[4:]

    published_at = datetime.fromisoformat(
        article["publishedAt"].replace("Z", "+00:00")
    ).isoformat()

    return {
        "provider": "gnews",
        "provider_article_id": article["id"],
        "source_id": source_host,
        "source_name": source["name"],
        "source_tier": "unrated",
        "source_url": source["url"],
        "source_country": source.get("country"),
        "original_url": article["url"],
        "published_at": published_at,
        "updated_at": None,
        "fetched_at": fetched_at.isoformat(),
        "original_timezone": "UTC",
        "original_language": article.get("lang"),
        "market_scope": market_scope,
        "raw_title": article["title"],
        "raw_description": article.get("description"),
        "raw_content": article.get("content"),
        "normalized_title": None,
        "normalized_content": None,
        "image_url": article.get("image"),
    }
