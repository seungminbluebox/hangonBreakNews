import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
import math

from openrouter_budget import (
    OpenRouterBudget,
    OpenRouterDailyLimitError,
    OpenRouterRequestBlocked,
    is_free_model,
)


class OpenRouterBudgetTests(unittest.TestCase):
    def test_free_model_detection_does_not_classify_paid_models(self):
        self.assertTrue(is_free_model("openrouter/free"))
        self.assertTrue(is_free_model("google/gemma-3-27b-it:free"))
        self.assertFalse(is_free_model("google/gemini-2.5-flash"))

    def test_slot_quota_is_persistent_and_rejects_zero_quota_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "budget.sqlite3")
            now = [datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)]
            budget = OpenRouterBudget(path=path, limit=45, clock=lambda: now[0])

            with self.assertRaises(OpenRouterDailyLimitError):
                budget.reserve("openrouter/free")

            now[0] = datetime(2026, 9, 13, 0, 30, tzinfo=timezone.utc)
            budget.reserve("openrouter/free")
            restarted = OpenRouterBudget(path=path, limit=45, clock=lambda: now[0])
            with self.assertRaises(OpenRouterDailyLimitError):
                restarted.reserve("openrouter/free")

    def test_paid_model_does_not_consume_or_obey_free_breaker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "budget.sqlite3")
            budget = OpenRouterBudget(
                path=path,
                limit=1,
                clock=lambda: datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc),
            )
            budget.record_rate_limit(
                model_name="openrouter/free",
                status_code=429,
                body="free-models-per-day",
            )
            budget.reserve("google/gemini-2.5-flash")
            with self.assertRaises(OpenRouterRequestBlocked):
                budget.reserve("openrouter/free")

    def test_storage_error_is_reported_before_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "missing", "budget.sqlite3")
            budget = OpenRouterBudget(path=path, limit=1)
            with self.assertRaises(RuntimeError):
                budget.reserve("openrouter/free")

    def test_distributed_slots_total_no_more_than_daily_limit(self):
        for limit in (900, 45):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "budget.sqlite3")
                budget = OpenRouterBudget(
                    path=path,
                    limit=limit,
                    clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
                )
                reserved = 0
                for slot in range(288):
                    now = datetime(2026, 9, 13, tzinfo=timezone.utc).replace(
                        hour=(slot * 5) // 60,
                        minute=(slot * 5) % 60,
                    )
                    budget.clock = lambda now=now: now
                    quota = math.floor((slot + 1) * limit / 288) - math.floor(
                        slot * limit / 288
                    )
                    for _ in range(quota):
                        budget.reserve("openrouter/free")
                        reserved += 1
                    with self.assertRaises(OpenRouterDailyLimitError):
                        budget.reserve("openrouter/free")
                self.assertEqual(reserved, limit)

    def test_utc_rollover_clears_daily_usage_and_daily_breaker(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 9, 13, 23, 59, tzinfo=timezone.utc)]
            budget = OpenRouterBudget(
                path=os.path.join(directory, "budget.sqlite3"),
                limit=900,
                clock=lambda: now[0],
            )
            budget.record_rate_limit(
                model_name="openrouter/free",
                status_code=429,
                body="openrouter_free_tier_daily",
            )
            now[0] = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
            budget.reserve("openrouter/free")


if __name__ == "__main__":
    unittest.main()
