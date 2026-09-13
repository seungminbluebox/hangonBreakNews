import logging
import unittest
from unittest.mock import patch

from cycle_logging import cycle_scope, log_event, record_ai_call, record_retry


class CycleLoggingTests(unittest.TestCase):
    def test_cycle_emits_one_summary_with_actual_counters(self):
        with self.assertLogs("hangon.cycle", level="INFO") as captured:
            with cycle_scope(cycle_id="test-cycle", slow_seconds=60) as context:
                context.set_stage("selection")
                context.update(fetched=4, candidates=2, selected=1)
                record_ai_call()
                record_ai_call()
                record_retry()
                context.set_status("ok")

        summaries = [line for line in captured.output if "event=cycle_summary" in line]
        self.assertEqual(len(summaries), 1)
        self.assertIn("cycle=test-cycle", summaries[0])
        self.assertIn("ai_calls=2", summaries[0])
        self.assertIn("retries=1", summaries[0])
        self.assertIn("status=ok", summaries[0])

    def test_debug_events_follow_configured_level(self):
        with self.assertLogs("hangon.cycle", level="DEBUG") as captured:
            with cycle_scope(cycle_id="debug-cycle", slow_seconds=60):
                log_event("stage_detail", level=logging.DEBUG, stage="summary")
        self.assertTrue(any("event=stage_detail" in line for line in captured.output))

    def test_injected_output_receives_only_configured_levels(self):
        outputs = []
        with cycle_scope(cycle_id="sink-cycle", slow_seconds=60, output=outputs.append):
            log_event("hidden_detail", level=logging.DEBUG)
            log_event("visible_warning", level=logging.WARNING)
        self.assertTrue(any("event=visible_warning" in line for line in outputs))
        self.assertTrue(any("event=cycle_summary" in line for line in outputs))
        self.assertFalse(any("event=hidden_detail" in line for line in outputs))

    def test_slow_warning_is_once_and_completion_cancels_timer(self):
        class FakeTimer:
            def __init__(self, interval, callback):
                self.callback = callback
                self.cancelled = False

            def start(self):
                pass

            def cancel(self):
                self.cancelled = True

        timer = FakeTimer(0, lambda: None)
        def timer_factory(_interval, callback):
            timer.callback = callback
            return timer

        with patch("cycle_logging.threading.Timer", side_effect=timer_factory):
            with self.assertLogs("hangon.cycle", level="WARNING") as captured:
                with cycle_scope(cycle_id="slow-cycle", slow_seconds=60):
                    timer.callback()
        self.assertEqual(
            sum("event=cycle_slow" in line for line in captured.output),
            1,
        )

        timer = FakeTimer(0, lambda: None)
        def timer_factory_fast(_interval, callback):
            timer.callback = callback
            return timer

        with patch("cycle_logging.threading.Timer", side_effect=timer_factory_fast):
            with self.assertLogs("hangon.cycle", level="INFO") as captured:
                with cycle_scope(cycle_id="fast-cycle", slow_seconds=60):
                    pass
                timer.callback()
        self.assertTrue(timer.cancelled)
        self.assertFalse(any("event=cycle_slow" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
