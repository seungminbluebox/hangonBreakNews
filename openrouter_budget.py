"""Persistent request budgeting and rate-limit state for OpenRouter free models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import sqlite3


DEFAULT_FREE_DAILY_LIMIT = 990
DEFAULT_BUDGET_PATH = str(Path(__file__).resolve().parent / ".openrouter-budget.sqlite3")
SLOT_COUNT = 288
SLOT_SECONDS = 5 * 60


class OpenRouterBudgetError(RuntimeError):
    """Base class for safe, retry-free OpenRouter budget failures."""

    def __init__(self, message, *, partial_results=()):
        super().__init__(message)
        self.partial_results = tuple(partial_results)


class OpenRouterDailyLimitError(OpenRouterBudgetError):
    """The configured free-model daily or current-slot budget is exhausted."""


class OpenRouterRequestBlocked(OpenRouterBudgetError):
    """A persisted OpenRouter 429 breaker currently prevents a free request."""


class OpenRouterBudgetStorageError(OpenRouterBudgetError):
    """The request state could not be read or written safely."""


def is_free_model(model_name: str | None) -> bool:
    normalized = str(model_name or "").strip().casefold()
    return normalized == "openrouter/free" or normalized.endswith(":free")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unix(value: datetime) -> float:
    return _as_utc(value).timestamp()


def _parse_retry_after(value, now: datetime) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        return _unix(parsed) if parsed else None
    if not math.isfinite(number):
        return None
    return _unix(now) + max(0.0, number)


def _parse_reset_value(value, now: datetime) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        return _unix(parsed) if parsed else None
    if not math.isfinite(number):
        return None
    if number > 100_000_000_000:
        number /= 1000
    if number <= _unix(now):
        return None
    return number


def _header_value(headers, names):
    if not headers:
        return None
    for name in names:
        try:
            value = headers.get(name)
        except AttributeError:
            value = None
        if value is not None:
            return value
    lowered = {str(key).casefold(): value for key, value in headers.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


def classify_rate_limit(*, status_code=None, body="") -> str | None:
    """Return ``daily`` only for the explicit OpenRouter daily free-tier marker."""
    if int(status_code or 0) != 429:
        return None
    text = str(body or "").casefold()
    if "free-models-per-day" in text or "openrouter_free_tier_daily" in text:
        return "daily"
    return "temporary"


class OpenRouterBudget:
    """SQLite-backed UTC daily and five-minute free-model request budget."""

    def __init__(self, path=None, limit=DEFAULT_FREE_DAILY_LIMIT, clock=None):
        self.path = str(path or os.getenv("OPENROUTER_BUDGET_PATH", DEFAULT_BUDGET_PATH))
        self.limit = int(limit)
        if self.limit < 0:
            raise ValueError("OPENROUTER_FREE_DAILY_LIMIT must be non-negative")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_environment(cls, environment=None, *, clock=None):
        environment = environment or os.environ
        return cls(
            path=environment.get("OPENROUTER_BUDGET_PATH", DEFAULT_BUDGET_PATH),
            limit=int(
                environment.get(
                    "OPENROUTER_FREE_DAILY_LIMIT", DEFAULT_FREE_DAILY_LIMIT
                )
            ),
            clock=clock,
        )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS openrouter_budget_usage (
                day TEXT NOT NULL,
                slot INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, slot)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS openrouter_budget_breaker (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                blocked_until REAL NOT NULL,
                reason TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        return connection

    def reserve(self, model_name: str | None) -> dict | None:
        """Reserve one actual HTTP attempt, atomically, before ``requests.post``."""
        if not is_free_model(model_name):
            return

        now = _as_utc(self.clock())
        day = now.date().isoformat()
        slot = (now.hour * 3600 + now.minute * 60 + now.second) // SLOT_SECONDS
        slot = min(SLOT_COUNT - 1, slot)
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT blocked_until, reason FROM openrouter_budget_breaker WHERE id=1"
                ).fetchone()
                if row and float(row[0]) > _unix(now):
                    raise OpenRouterRequestBlocked(
                        f"OpenRouter free requests blocked ({row[1]})."
                    )
                breaker_released = bool(row)
                if breaker_released:
                    connection.execute("DELETE FROM openrouter_budget_breaker WHERE id=1")

                quota = math.floor((slot + 1) * self.limit / SLOT_COUNT) - math.floor(
                    slot * self.limit / SLOT_COUNT
                )
                used = connection.execute(
                    "SELECT attempts FROM openrouter_budget_usage WHERE day=? AND slot=?",
                    (day, slot),
                ).fetchone()
                daily_used = connection.execute(
                    "SELECT COALESCE(SUM(attempts), 0) FROM openrouter_budget_usage WHERE day=?",
                    (day,),
                ).fetchone()[0]
                if daily_used >= self.limit or (used[0] if used else 0) >= quota:
                    raise OpenRouterDailyLimitError(
                        f"OpenRouter free request budget exhausted for UTC slot ({quota})."
                    )
                connection.execute(
                    """
                    INSERT INTO openrouter_budget_usage(day, slot, attempts)
                    VALUES (?, ?, 1)
                    ON CONFLICT(day, slot) DO UPDATE SET attempts=attempts + 1
                    """,
                    (day, slot),
                )
                daily_used += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return {
                "day": day,
                "slot": slot,
                "daily_used": int(daily_used),
                "daily_limit": self.limit,
                "daily_remaining": max(0, self.limit - int(daily_used)),
                "slot_used": int((used[0] if used else 0) + 1),
                "slot_limit": quota,
                "breaker_released": breaker_released,
            }
        except OpenRouterBudgetError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise OpenRouterBudgetStorageError(
                "OpenRouter budget storage unavailable; request was not sent."
            ) from error

    def record_rate_limit(
        self,
        *,
        model_name: str | None,
        status_code=429,
        body="",
        headers=None,
    ) -> dict | bool:
        """Persist a free-model breaker using only safe classification metadata."""
        if not is_free_model(model_name):
            return False
        classification = classify_rate_limit(status_code=status_code, body=body)
        if classification is None:
            return False

        now = _as_utc(self.clock())
        now_unix = _unix(now)
        retry_after = _header_value(headers, ("Retry-After",))
        reset_header = _header_value(
            headers,
            (
                "X-RateLimit-Reset",
                "X-RateLimit-Reset-At",
                "X-RateLimit-Reset-Time",
                "RateLimit-Reset",
            ),
        )
        retry_until = _parse_retry_after(retry_after, now)
        reset_until = _parse_reset_value(reset_header, now)
        if classification == "daily":
            next_day = datetime.combine(
                now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            )
            blocked_until = max(_unix(next_day), retry_until or 0, reset_until or 0)
        else:
            header_until = max(retry_until or 0, reset_until or 0)
            blocked_until = max(
                now_unix + (header_until - now_unix if header_until else 300),
                now_unix + 60,
            )
        reason = "daily" if classification == "daily" else "temporary"
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT blocked_until, reason FROM openrouter_budget_breaker WHERE id=1"
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO openrouter_budget_breaker(id, blocked_until, reason, updated_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        blocked_until=MAX(blocked_until, excluded.blocked_until),
                        reason=excluded.reason,
                        updated_at=excluded.updated_at
                    """,
                    (blocked_until, reason, now_unix),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return {
                "changed": not previous or float(previous[0]) <= now_unix or previous[1] != reason,
                "reason": reason,
                "blocked_until": blocked_until,
            }
        except OpenRouterBudgetError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise OpenRouterBudgetStorageError(
                "OpenRouter breaker state could not be saved."
            ) from error

    def snapshot(self) -> dict:
        """Expose safe usage and breaker metadata for diagnostics."""
        now = _as_utc(self.clock())
        day = now.date().isoformat()
        try:
            connection = self._connect()
            try:
                used = connection.execute(
                    "SELECT COALESCE(SUM(attempts), 0) FROM openrouter_budget_usage WHERE day=?",
                    (day,),
                ).fetchone()[0]
                slot = (now.hour * 3600 + now.minute * 60 + now.second) // SLOT_SECONDS
                slot = min(SLOT_COUNT - 1, slot)
                slot_used_row = connection.execute(
                    "SELECT COALESCE(attempts, 0) FROM openrouter_budget_usage WHERE day=? AND slot=?",
                    (day, slot),
                ).fetchone()
                slot_used = slot_used_row[0] if slot_used_row else 0
                breaker = connection.execute(
                    "SELECT blocked_until, reason FROM openrouter_budget_breaker WHERE id=1"
                ).fetchone()
            finally:
                connection.close()
        except (sqlite3.Error, OSError) as error:
            raise OpenRouterBudgetStorageError(
                "OpenRouter budget storage unavailable."
            ) from error
        return {
            "day": day,
            "limit": self.limit,
            "used": int(used),
            "remaining": max(0, self.limit - int(used)),
            "slot": slot,
            "slot_used": int(slot_used),
            "slot_limit": math.floor((slot + 1) * self.limit / SLOT_COUNT)
            - math.floor(slot * self.limit / SLOT_COUNT),
            "blocked_until": breaker[0] if breaker else None,
            "blocked_reason": breaker[1] if breaker else None,
        }
