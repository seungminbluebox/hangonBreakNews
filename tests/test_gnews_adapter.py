from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, call
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from gnews_adapter import GNewsClient, collect_default_headlines, normalize_article


class FakeHttpResponse:
    def __init__(self, payload, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def normalized_item(article_id, market_scope, original_url):
    return {
        "provider": "gnews",
        "provider_article_id": article_id,
        "source_id": "example.com",
        "source_name": "Example News",
        "source_tier": "unrated",
        "source_url": "https://example.com",
        "source_country": "us",
        "original_url": original_url,
        "published_at": "2026-08-03T01:00:00+00:00",
        "updated_at": None,
        "fetched_at": "2026-08-03T02:00:00+00:00",
        "original_timezone": "UTC",
        "original_language": "en",
        "market_scope": market_scope,
        "raw_title": f"Headline {article_id}",
        "raw_description": f"Description {article_id}",
        "raw_content": f"Content {article_id}",
        "normalized_title": None,
        "normalized_content": None,
        "image_url": None,
    }


class NormalizeArticleTests(unittest.TestCase):
    def test_preserves_publisher_and_original_text(self):
        fetched_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        article = {
            "id": "gnews-article-123",
            "title": "Fed holds rates steady",
            "description": "The central bank left its policy rate unchanged.",
            "content": "The Federal Reserve held interest rates steady on Wednesday.",
            "url": "https://example.com/economy/fed-rates",
            "image": "https://example.com/images/fed.jpg",
            "publishedAt": "2026-08-03T10:30:00Z",
            "lang": "en",
            "source": {
                "name": "Example News",
                "url": "https://www.example.com",
                "country": "us",
            },
        }

        normalized = normalize_article(article, market_scope="us", fetched_at=fetched_at)

        self.assertEqual(
            normalized,
            {
                "provider": "gnews",
                "provider_article_id": "gnews-article-123",
                "source_id": "example.com",
                "source_name": "Example News",
                "source_tier": "unrated",
                "source_url": "https://www.example.com",
                "source_country": "us",
                "original_url": "https://example.com/economy/fed-rates",
                "published_at": "2026-08-03T10:30:00+00:00",
                "updated_at": None,
                "fetched_at": "2026-08-03T12:00:00+00:00",
                "original_timezone": "UTC",
                "original_language": "en",
                "market_scope": "us",
                "raw_title": "Fed holds rates steady",
                "raw_description": "The central bank left its policy rate unchanged.",
                "raw_content": "The Federal Reserve held interest rates steady on Wednesday.",
                "normalized_title": None,
                "normalized_content": None,
                "image_url": "https://example.com/images/fed.jpg",
            },
        )


class GNewsClientTests(unittest.TestCase):
    def test_fetch_top_headlines_sends_region_query_and_normalizes_results(self):
        response = FakeHttpResponse(
            {
                "totalArticles": 1,
                "articles": [
                    {
                        "id": "kr-article-1",
                        "title": "한국은행, 기준금리 동결",
                        "description": "한국은행이 기준금리를 유지했다.",
                        "content": "한국은행 금융통화위원회는 기준금리를 동결했다.",
                        "url": "https://news.example.kr/rates",
                        "image": None,
                        "publishedAt": "2026-08-03T01:00:00Z",
                        "lang": "ko",
                        "source": {
                            "name": "예시경제",
                            "url": "https://news.example.kr",
                            "country": "kr",
                        },
                    }
                ],
            },
        )
        opener = Mock(return_value=response)
        fetched_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
        client = GNewsClient(api_key="test-key", opener=opener)

        articles = client.fetch_top_headlines(
            market_scope="kr",
            country="kr",
            language="ko",
            category="business",
            max_articles=10,
            fetched_at=fetched_at,
        )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["provider_article_id"], "kr-article-1")
        self.assertEqual(articles[0]["market_scope"], "kr")
        request = opener.call_args.args[0]
        self.assertEqual(
            request.full_url.split("?", maxsplit=1)[0],
            "https://gnews.io/api/v4/top-headlines",
        )
        self.assertEqual(
            parse_qs(urlparse(request.full_url).query),
            {
                "category": ["business"],
                "lang": ["ko"],
                "country": ["kr"],
                "max": ["10"],
                "from": ["2026-08-02T23:00:00Z"],
                "to": ["2026-08-03T02:00:00Z"],
                "apikey": ["test-key"],
            },
        )
        self.assertEqual(opener.call_args.kwargs, {"timeout": 15})

    def test_discards_articles_older_than_the_three_hour_request_window(self):
        response = FakeHttpResponse(
            {
                "totalArticles": 2,
                "articles": [
                    {
                        "id": "outside-window",
                        "title": "Old recap",
                        "description": "An older article.",
                        "content": "Published just outside the collection window.",
                        "url": "https://example.com/outside-window",
                        "image": None,
                        "publishedAt": "2026-08-03T08:59:59Z",
                        "lang": "en",
                        "source": {
                            "name": "Example News",
                            "url": "https://example.com",
                            "country": "us",
                        },
                    },
                    {
                        "id": "window-boundary",
                        "title": "Current economic release",
                        "description": "A current article.",
                        "content": "Published at the collection window boundary.",
                        "url": "https://example.com/window-boundary",
                        "image": None,
                        "publishedAt": "2026-08-03T09:00:00Z",
                        "lang": "en",
                        "source": {
                            "name": "Example News",
                            "url": "https://example.com",
                            "country": "us",
                        },
                    },
                ],
            }
        )
        client = GNewsClient(api_key="test-key", opener=Mock(return_value=response))

        articles = client.fetch_top_headlines(
            market_scope="us",
            country="us",
            language="en",
            category="business",
            max_articles=10,
            fetched_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["provider_article_id"] for item in articles],
            ["window-boundary"],
        )

    def test_retries_rate_limit_after_retry_after_delay(self):
        rate_limit_error = HTTPError(
            "https://gnews.io/api/v4/top-headlines",
            429,
            "Too Many Requests",
            {"Retry-After": "2"},
            None,
        )
        response = FakeHttpResponse({"totalArticles": 0, "articles": []})
        opener = Mock(side_effect=[rate_limit_error, response])
        sleeper = Mock()
        client = GNewsClient(
            api_key="test-key",
            opener=opener,
            sleeper=sleeper,
            max_retries=2,
        )

        articles = client.fetch_top_headlines(
            market_scope="world",
            country=None,
            language="en",
            category="world",
            max_articles=10,
            fetched_at=datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(articles, [])
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(2.0)

    def test_counts_every_http_attempt_before_opening_the_request(self):
        rate_limit_error = HTTPError(
            "https://gnews.io/api/v4/top-headlines",
            429,
            "Too Many Requests",
            {"Retry-After": "1"},
            None,
        )
        opener = Mock(
            side_effect=[
                rate_limit_error,
                FakeHttpResponse({"totalArticles": 0, "articles": []}),
            ]
        )
        before_request = Mock()
        client = GNewsClient(
            api_key="test-key",
            opener=opener,
            sleeper=Mock(),
            max_retries=2,
            before_request=before_request,
        )

        client.fetch_top_headlines(
            market_scope="world",
            country=None,
            language="en",
            category="business",
            max_articles=10,
            fetched_at=datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(before_request.call_count, 2)
        self.assertEqual(opener.call_count, 2)

    def test_retries_temporary_server_failure_with_exponential_backoff(self):
        temporary_error = HTTPError(
            "https://gnews.io/api/v4/top-headlines",
            503,
            "Service Unavailable",
            {},
            None,
        )
        opener = Mock(
            side_effect=[
                temporary_error,
                FakeHttpResponse({"totalArticles": 0, "articles": []}),
            ]
        )
        sleeper = Mock()
        client = GNewsClient(
            api_key="test-key",
            opener=opener,
            sleeper=sleeper,
            max_retries=2,
        )

        result = client.fetch_top_headlines(
            market_scope="world",
            country=None,
            language="en",
            category="business",
            max_articles=10,
            fetched_at=datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result, [])
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(1.0)

    def test_retries_temporary_connection_failure(self):
        opener = Mock(
            side_effect=[
                URLError("temporary DNS failure"),
                FakeHttpResponse({"totalArticles": 0, "articles": []}),
            ]
        )
        sleeper = Mock()
        client = GNewsClient(
            api_key="test-key",
            opener=opener,
            sleeper=sleeper,
            max_retries=2,
        )

        result = client.fetch_top_headlines(
            market_scope="world",
            country=None,
            language="en",
            category="business",
            max_articles=10,
            fetched_at=datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result, [])
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(1.0)


class DefaultFeedCollectionTests(unittest.TestCase):
    def test_requests_plan_maximum_for_all_scopes_without_adding_api_calls(self):
        client = Mock()
        client.fetch_top_headlines.return_value = []
        fetched_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)

        collect_default_headlines(
            client,
            fetched_at=fetched_at,
            sleeper=Mock(),
        )

        self.assertEqual(client.fetch_top_headlines.call_count, 3)
        self.assertEqual(
            [
                request.kwargs["max_articles"]
                for request in client.fetch_top_headlines.call_args_list
            ],
            [25, 25, 25],
        )

    def test_collects_three_scopes_with_free_plan_delay_and_exact_deduplication(self):
        duplicate = normalized_item(
            "shared-article",
            "kr",
            "https://example.com/shared",
        )
        client = Mock()
        client.fetch_top_headlines.side_effect = [
            [duplicate],
            [
                normalized_item(
                    "us-article",
                    "us",
                    "https://example.com/us",
                ),
                {**duplicate, "market_scope": "us"},
            ],
            [
                normalized_item(
                    "world-article",
                    "world",
                    "https://example.com/world",
                )
            ],
        ]
        sleeper = Mock()
        fetched_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)

        articles = collect_default_headlines(
            client,
            fetched_at=fetched_at,
            sleeper=sleeper,
            delay_seconds=1.1,
            max_articles=10,
        )

        self.assertEqual(
            [article["provider_article_id"] for article in articles],
            ["shared-article", "us-article", "world-article"],
        )
        self.assertEqual(
            client.fetch_top_headlines.call_args_list,
            [
                call(
                    market_scope="kr",
                    country="kr",
                    language="ko",
                    category="business",
                    max_articles=10,
                    fetched_at=fetched_at,
                ),
                call(
                    market_scope="us",
                    country="us",
                    language="en",
                    category="business",
                    max_articles=10,
                    fetched_at=fetched_at,
                ),
                call(
                    market_scope="world",
                    country=None,
                    language="en",
                    category="business",
                    max_articles=10,
                    fetched_at=fetched_at,
                ),
            ],
        )
        self.assertEqual(sleeper.call_args_list, [call(1.1), call(1.1)])


if __name__ == "__main__":
    unittest.main()
