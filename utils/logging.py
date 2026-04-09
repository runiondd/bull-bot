"""
Structured logging for Bull-Bot.

Every log line is JSON so the performance analyzer and evolver can
ingest logs directly without regex parsing. Uses stdlib logging so
third-party libs (requests, anthropic, pandas) flow through the same
handlers.

Conventions:
- All logs tagged with run_id (defaults to "live" if unset)
- ERROR logs always include exc_info
- Per-module loggers via get_logger(__name__)
- Log dirs: logs/live/, logs/backtest/<run_id>/
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Thread-local context so we can stamp log lines with run_id / agent / ticker
# without threading them through every function signature.
_context = threading.local()


def set_log_context(**kwargs: Any) -> None:
    """Set structured context fields for the current thread.

    Typical use:
        set_log_context(run_id="bt_abc123", agent="decision", ticker="TSLA")

    Pass value=None to clear a field.
    """
    for key, value in kwargs.items():
        if value is None:
            if hasattr(_context, key):
                delattr(_context, key)
        else:
            setattr(_context, key, value)


def clear_log_context() -> None:
    """Drop all thread-local log context fields."""
    for key in list(vars(_context).keys()):
        delattr(_context, key)


def get_log_context() -> dict[str, Any]:
    """Return a shallow dict of current thread-local context."""
    return {k: v for k, v in vars(_context).items() if not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter — one record per line, UTC timestamps."""

    RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # Pull thread-local context
        ctx = get_log_context()
        if ctx:
            payload["ctx"] = ctx

        # Pull any extras that the caller passed via logger.*(..., extra={...})
        for key, value in record.__dict__.items():
            if key in self.RESERVED or key.startswith("_"):
                continue
            # Try to keep JSON-safe values only
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly formatter for stderr."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%H:%M:%SZ"
        )
        ctx = get_log_context()
        ctx_str = ""
        if ctx:
            ctx_str = " [" + " ".join(f"{k}={v}" for k, v in ctx.items()) + "]"
        base = f"{ts} {record.levelname:<5} {record.name}{ctx_str}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_CONFIGURED = False
_LOCK = threading.Lock()


def configure_logging(
    *,
    logs_dir: Path | str | None = None,
    level: str = "INFO",
    console: bool = True,
    json_file: bool = True,
    run_scope: str = "live",
) -> None:
    """
    Configure the root logger. Idempotent — safe to call multiple times.

    Args:
        logs_dir: base directory for log files. Defaults to <project>/logs.
        level: root log level string.
        console: emit human-readable lines to stderr.
        json_file: emit JSON lines to <logs_dir>/<run_scope>/bullbot.log with rotation.
        run_scope: subdirectory name. 'live' for the live process, 'backtest/<run_id>'
            for backtest processes. Creates the directory if missing.
    """
    global _CONFIGURED

    with _LOCK:
        root = logging.getLogger()
        root.setLevel(level)

        # Nuke handlers so reconfiguration doesn't double-log
        for h in list(root.handlers):
            root.removeHandler(h)

        if console:
            ch = logging.StreamHandler(sys.stderr)
            ch.setLevel(level)
            ch.setFormatter(ConsoleFormatter())
            root.addHandler(ch)

        if json_file:
            base_dir = Path(logs_dir) if logs_dir else _default_logs_dir()
            target_dir = base_dir / run_scope
            target_dir.mkdir(parents=True, exist_ok=True)
            log_path = target_dir / "bullbot.log"
            fh = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=25 * 1024 * 1024,  # 25 MB
                backupCount=10,
                encoding="utf-8",
            )
            fh.setLevel(level)
            fh.setFormatter(JsonFormatter())
            root.addHandler(fh)

        # Tame noisy third-party loggers unless we're in DEBUG mode
        if level != "DEBUG":
            for noisy in ("urllib3", "requests", "httpx", "anthropic", "httpcore"):
                logging.getLogger(noisy).setLevel(logging.WARNING)

        _CONFIGURED = True


def _default_logs_dir() -> Path:
    """Fall back to <project>/logs without importing config (no circular dep)."""
    # utils/logging.py -> utils -> project root
    return Path(__file__).resolve().parent.parent / "logs"


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger. Auto-configures on first call with defaults so early
    imports don't blackhole their logs.
    """
    if not _CONFIGURED:
        # Honor env var so tests / scripts can bump verbosity without touching code
        level = os.environ.get("BULLBOT_LOG_LEVEL", "INFO")
        configure_logging(level=level)
    return logging.getLogger(name)
