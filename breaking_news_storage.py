"""Explicit storage contract; never probe or change the production schema."""
import os


CONTENT_COLUMN_MODE_ENV = "BREAKING_NEWS_CONTENT_COLUMN_MODE"


def get_content_column_mode(environment=None):
    environment = os.environ if environment is None else environment
    mode = environment.get(CONTENT_COLUMN_MODE_ENV, "legacy")
    if mode not in ("legacy", "omit"):
        raise ValueError(f"{CONTENT_COLUMN_MODE_ENV} must be legacy or omit")
    return mode


def private_source(value):
    """Keep real provider text byte-for-byte; never substitute generated content."""
    return value if isinstance(value, str) and value.strip() else None


def prepare_breaking_news_row(row, *, mode=None):
    mode = (get_content_column_mode() if mode is None
            else get_content_column_mode({CONTENT_COLUMN_MODE_ENV: mode}))
    result = {key: value for key, value in row.items() if key != "content"}
    if mode == "legacy":
        # Current NOT NULL/no-default schema requires this compatibility field.
        result["content"] = ""
    return result


def recent_duplicate_context(row):
    """Internal content is raw duplicate evidence, never the retired DB summary."""
    result = {
        "title": row.get("title") or "",
        "content": private_source(row.get("source_content")) or "",
    }
    if "created_at" in row:
        result["created_at"] = row["created_at"]
    return result
