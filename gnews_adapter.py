"""GNews API adapter with no database or notification side effects."""

from datetime import datetime
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GNEWS_TOP_HEADLINES_URL = "https://gnews.io/api/v4/top-headlines"
DEFAULT_FEEDS = (
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
        "max_articles": 10,
    },
    {
        "market_scope": "world",
        "country": None,
        "language": "en",
        "category": "business",
        "max_articles": 10,
    },
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
        params = {
            "category": category,
            "lang": language,
            "country": country,
            "max": max_articles,
            "apikey": self.api_key,
        }
        query = urlencode({key: value for key, value in params.items() if value is not None})
        request = Request(
            f"{GNEWS_TOP_HEADLINES_URL}?{query}",
            headers={"User-Agent": "Hangon-BreakingNews/1.0"},
        )
        for attempt in range(self.max_retries + 1):
            try:
                self.before_request()
                with self.opener(request, timeout=15) as response:
                    import json

                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                is_temporary = error.code == 429 or 500 <= error.code < 600
                if not is_temporary or attempt == self.max_retries:
                    raise
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                self.sleeper(delay)
            except (URLError, TimeoutError):
                if attempt == self.max_retries:
                    raise
                self.sleeper(float(2**attempt))

        return [
            normalize_article(
                article,
                market_scope=market_scope,
                fetched_at=fetched_at,
            )
            for article in payload["articles"]
        ]


def collect_default_headlines(
    client: GNewsClient,
    *,
    fetched_at: datetime,
    sleeper=time.sleep,
    delay_seconds: float = 1.1,
    max_articles: int | None = None,
) -> list[dict]:
    """Fetch Korea, US, and world feeds sequentially and remove exact duplicates."""
    collected = []
    seen_article_ids = set()
    seen_urls = set()

    for index, feed in enumerate(DEFAULT_FEEDS):
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

        if index < len(DEFAULT_FEEDS) - 1:
            sleeper(delay_seconds)

    return collected


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
