"""Small, process-local logging context shared by one GNews cycle."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
import os
import threading
import time
import uuid


LOGGER_NAME = "hangon.cycle"
_CURRENT: ContextVar["CycleLogContext | None"] = ContextVar(
    "hangon_cycle_context", default=None
)


def _logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    owned_handler = getattr(logger, "_hangon_handler", None)
    if owned_handler in logger.handlers:
        level_name = os.getenv("GNEWS_LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level_name, logging.INFO))
        return logger
    if owned_handler not in logger.handlers:
        if not logger.handlers:
            level_name = os.getenv("GNEWS_LOG_LEVEL", "INFO").upper()
            logger.setLevel(getattr(logging, level_name, logging.INFO))
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            logger._hangon_handler = handler
            logger.propagate = False
    return logger


def current_context() -> "CycleLogContext | None":
    return _CURRENT.get()


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "_".join(value.split())[:80] or "unknown"
    return type(value).__name__


def log_event(event: str, *, level=logging.INFO, stage=None, status=None, cycle_id=None, once_key=None, context_override=None, **fields):
    """Emit one safe key=value record; arbitrary exception text is never logged."""
    context = context_override or current_context()
    if context is not None and once_key:
        with context._lock:
            if once_key in context._once_events:
                return
            context._once_events.add(once_key)
    values = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "level": "WARN" if level >= logging.WARNING and level < logging.ERROR else logging.getLevelName(level),
        "event": event,
    }
    if context is not None:
        cycle_id = cycle_id or context.cycle_id
        stage = stage or context.stage
    if cycle_id:
        values["cycle"] = cycle_id
    if stage:
        values["stage"] = stage
    if status:
        values["status"] = status
    values.update({key: _value(value) for key, value in fields.items() if value is not None})
    message = " ".join(f"{key}={_value(value)}" for key, value in values.items())
    if context is not None and context.output is not None:
        if level >= context.level_threshold:
            context.output(message)
    else:
        _logger().log(level, message)


@dataclass
class CycleLogContext:
    cycle_id: str
    slow_seconds: float = 60.0
    started: float = field(default_factory=time.monotonic)
    stage: str = "fetch"
    status: str = "running"
    stats: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    output: object = field(default=None, repr=False)
    level_threshold: int = logging.INFO
    failure_stage: str | None = field(default=None, init=False)
    failure_reason: str | None = field(default=None, init=False)
    _slow_emitted: bool = field(default=False, init=False)
    _summary_emitted: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _once_events: set[str] = field(default_factory=set, init=False, repr=False)

    def start(self):
        if self.slow_seconds > 0:
            self._timer = threading.Timer(self.slow_seconds, self._slow_warning)
            self._timer.daemon = True
            self._timer.start()

    def set_stage(self, stage: str):
        self.stage = stage

    def set_status(self, status: str):
        self.status = status

    def update(self, **values):
        self.stats.update(values)

    def increment(self, key: str, amount: int = 1):
        self.stats[key] = self.stats.get(key, 0) + amount

    def set_budget(self, *, used=None, limit=None):
        if used is not None:
            self.budget["budget_used"] = used
        if limit is not None:
            self.budget["budget_limit"] = limit

    def _slow_warning(self):
        with self._lock:
            if self._finished or self._slow_emitted:
                return
            self._slow_emitted = True
            log_event(
                "cycle_slow",
                level=logging.WARNING,
                cycle_id=self.cycle_id,
                stage=self.stage,
                context_override=self,
                elapsed=f"{time.monotonic() - self.started:.1f}",
            )

    def emit_summary(self):
        with self._lock:
            if self._summary_emitted:
                return
            self._summary_emitted = True
        values = {
            "fetched": self.stats.get("fetched", 0),
            "candidates": self.stats.get("candidates", 0),
            "selected": self.stats.get("selected", 0),
            "saved": self.stats.get("saved", 0),
            "duplicates": self.stats.get("duplicates", 0),
            "rejected": self.stats.get("rejected", 0),
            "cut": self.stats.get("cut", 0),
            "quality_failed": self.stats.get("quality_failed", 0),
            "ai_calls": self.stats.get("ai_calls", 0),
            "retries": self.stats.get("retries", 0),
            "ai_failures": self.stats.get("ai_failures", 0),
            "ai_blocked": self.stats.get("ai_blocked", 0),
            "fetch_failures": self.stats.get("fetch_failures", 0),
            "db_failures": self.stats.get("db_failures", 0),
            "notify_failures": self.stats.get("notify_failures", 0),
            "elapsed": f"{time.monotonic() - self.started:.1f}",
            **self.budget,
        }
        if self.failure_stage:
            values["failure_stage"] = self.failure_stage
        if self.failure_reason:
            values["failure_reason"] = self.failure_reason
        log_event(
            "cycle_summary",
            level=logging.INFO,
            stage=self.stage,
            status=self.status,
            **values,
        )

    def finish(self):
        with self._lock:
            self._finished = True
            timer = self._timer
        if timer is not None:
            timer.cancel()


@contextmanager
def cycle_scope(*, cycle_id=None, slow_seconds=None, output=None):
    try:
        threshold = float(
            slow_seconds if slow_seconds is not None else os.getenv("GNEWS_SLOW_CYCLE_SECONDS", "60")
        )
    except (TypeError, ValueError):
        threshold = 60.0
    if not math.isfinite(threshold) or threshold <= 0:
        threshold = 60.0
    context = CycleLogContext(
        cycle_id=cycle_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        slow_seconds=threshold,
        output=output,
        level_threshold=getattr(logging, os.getenv("GNEWS_LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    token = _CURRENT.set(context)
    context.start()
    try:
        yield context
    except Exception:
        context.set_status("error")
        raise
    finally:
        context.finish()
        context.emit_summary()
        _CURRENT.reset(token)


def record_ai_call():
    context = current_context()
    if context is not None:
        context.increment("ai_calls")


def record_retry():
    context = current_context()
    if context is not None:
        context.increment("retries")


def record_failure(kind: str, amount: int = 1, *, stage=None, reason=None):
    context = current_context()
    if context is not None:
        context.increment(kind, amount)
        if stage:
            context.failure_stage = stage
        if reason:
            context.failure_reason = reason
