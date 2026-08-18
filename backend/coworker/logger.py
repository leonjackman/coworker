"""Unified logger controller for Coworker.

Replaces the ad-hoc ``logging.getLogger(__name__)`` calls scattered across
modules with a single entry point that configures level, formatters, handlers,
file rotation, and retention — all from environment variables or the Settings
page API.

Key features
------------
* Structured JSON output (console + file) with a custom ``JsonFormatter``.
* Log file rotation by size (10 MB default) with N backup files.
* Environment-variable driven level control: ``COWORKER_LOG_LEVEL``.
* Hierarchical logger names under the ``coworker`` package.
* API endpoint: ``POST /settings/log-level`` to change level at runtime.

Usage
-----
    # In any module:
    from coworker.logger import get_logger

    logger = get_logger(__name__)
    logger.info("some message")
"""
from __future__ import annotations

import json
import logging
import logging.config  # Required for dictConfig
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults (readable from env / Settings)
# ---------------------------------------------------------------------------
_DEFAULT_LEVEL = os.environ.get("COWORKER_LOG_LEVEL", "INFO")
_DEFAULT_MAX_BYTES = int(os.environ.get("COWORKER_LOG_MAX_BYTES", "10485760"))  # 10 MB
_DEFAULT_BACKUP_COUNT = int(os.environ.get("COWORKER_LOG_BACKUP_COUNT", "5"))
_DEFAULT_JSON_LOG = os.environ.get("COWORKER_JSON_LOG", "1").strip().lower() not in {"0", "false", "no", "off"}
_DEFAULT_LOG_FILE_PREFIX = "app"


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter.

    Produces one JSON object per log line suitable for ingestion by external
    logging systems or easy parsing by the frontend.

    Fields added to every record:
        timestamp, level, logger, message, exc_info
    Extra keys from ``record.__dict__`` (e.g. ``request_id``) are also included.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        *,
        validate: bool = True,
    ) -> None:
        # Consume parent init args but ignore them — we only use getMessage()
        # which the base class provides.
        logging.Formatter.__init__(self, "", style="%", validate=False)

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_info"] = self.formatException(record.exc_info)
        if hasattr(record, "message_id"):
            entry["message_id"] = record.message_id
        if hasattr(record, "session_id"):
            entry["session_id"] = record.session_id
        if hasattr(record, "tool_name"):
            entry["tool_name"] = record.tool_name
        if hasattr(record, "context"):
            entry["context"] = record.context

        # Include any extra keys
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "pathname", "filename", "module", "levelno", "levelname",
                "message", "getMessage",
            ):
                try:
                    entry[key] = value
                except Exception:  # noqa: BLE001
                    entry[key] = repr(value)

        return json.dumps(entry, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """Human-readable plain-text formatter for console output in non-JSON mode."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        *,
        validate: bool = True,
    ) -> None:
        logging.Formatter.__init__(
            self,
            "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
            "%",
            False,
        )


def _resolve_data_dir(data_dir: Path) -> Path:
    """Return the app data directory (create if needed)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _build_logger_config(
    data_dir: Path,
    log_level: str,
    log_max_bytes: int,
    log_backup_count: int,
    json_log: bool,
    log_file_prefix: str = _DEFAULT_LOG_FILE_PREFIX,
) -> dict[str, Any]:
    """Return a ``logging.config.dictConfig`` compatible config dict."""
    log_file = str(_resolve_data_dir(data_dir) / f"{log_file_prefix}.log")

    formatters: dict[str, Any] = {}
    if json_log:
        formatters["default"] = {
            "class": f"{JsonFormatter.__module__}.{JsonFormatter.__name__}",
        }
    else:
        formatters["default"] = {
            "class": f"{PlainFormatter.__module__}.{PlainFormatter.__name__}",
        }

    handlers: dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": log_level,
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "level": log_level,
            "filename": log_file,
            "maxBytes": log_max_bytes,
            "backupCount": log_backup_count,
            "encoding": "utf-8",
        },
    }

    # Also configure sub-packages so langgraph/uvicorn/etc logs surface too
    loggers: dict[str, Any] = {
        "": {  # root logger
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "coworker": {
            "handlers": ["console", "file"],
            "level": log_level,
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "langgraph": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "langchain": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
    }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "loggers": loggers,
    }


def _drain_handlers() -> None:
    """Remove all handlers from the root logger and all ``coworker.*`` loggers.

    This ensures ``dictConfig`` gets a clean slate so it can apply the
    configured formatters and handler types without inheriting stale
    ``StreamHandler``/``Formatter`` pairs from previous ``basicConfig`` or
    framework initialisation.
    """
    for h in logging.root.handlers[:]:
        handler = h
        logging.root.removeHandler(handler)
        handler.close()
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name == "coworker" or name.startswith("coworker."):
            lg = logging.getLogger(name)
            for h in lg.handlers[:]:
                lg.removeHandler(h)
                h.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_initialized = False
_log_file: Path | None = None
_current_level: str = "INFO"


def init_logger(data_dir: Path, log_level: str | None = None) -> Path:
    """Configure the root logger and all submodule loggers.

    Returns the path to the log file so callers can surface it in the UI.
    """
    global _initialized
    global _log_file  # must be at top of function body
    global _current_level

    if _initialized:
        # Already initialized; just update the level if changed.
        # NOTE: do NOT drain handlers here — a second call would tear down the
        # configured console/file handlers and leave the app with no output.
        if log_level:
            _set_level(log_level)
            _current_level = (log_level or _DEFAULT_LEVEL).upper()
        return _log_file or _resolve_data_dir(data_dir) / f"{_DEFAULT_LOG_FILE_PREFIX}.log"

    # Remove pre-existing handlers FIRST so dictConfig gets a clean slate
    _drain_handlers()

    level = log_level or _DEFAULT_LEVEL
    _current_level = level
    config = _build_logger_config(
        data_dir=data_dir,
        log_level=level,
        log_max_bytes=_DEFAULT_MAX_BYTES,
        log_backup_count=_DEFAULT_BACKUP_COUNT,
        json_log=_DEFAULT_JSON_LOG,
        log_file_prefix=_DEFAULT_LOG_FILE_PREFIX,
    )
    try:
        logging.config.dictConfig(config)
    except (ValueError, AttributeError) as exc:
        # Fallback: minimal basicConfig so the app never crashes on init failure
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            stream=sys.stdout,
            format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            force=True,
        )

    _initialized = True
    log_file_path = _resolve_data_dir(data_dir) / f"{_DEFAULT_LOG_FILE_PREFIX}.log"
    _log_file = log_file_path
    return log_file_path


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``coworker`` hierarchy.

    If ``name`` does not already live under ``coworker``, it is prefixed so
    that ``get_logger(__name__)`` in ``coworker/agents.py`` resolves to
    ``coworker.agents``. Calling without a name returns the top-level
    ``coworker`` logger.
    """
    if not name:
        return logging.getLogger("coworker")

    name = name.strip()
    if name == "coworker" or name.startswith("coworker."):
        return logging.getLogger(name)

    return logging.getLogger(f"coworker.{name.lstrip('.')}")


def _set_level(level: str) -> None:
    """Change the effective log level for ``coworker``, sub-modules, and all handlers."""
    numeric = getattr(logging, level.upper(), logging.INFO)

    # Update the "coworker" logger and all its children
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name == "coworker" or name.startswith("coworker."):
            lg = logging.getLogger(name)
            lg.setLevel(numeric)

    # Update handlers on root and coworker loggers
    for lg in (logging.getLogger(""), logging.getLogger("coworker")):
        for h in lg.handlers:
            h.setLevel(numeric)

    # Also set uvicorn to WARNING so request logs don't overwhelm console
    for name in ("uvicorn", "langgraph", "langchain"):
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET:
            lg.setLevel(logging.WARNING)


def set_log_level(level: str) -> str:
    """Set log level at runtime. Returns the new level string on success."""
    global _current_level
    numeric = getattr(logging, level.upper(), None)
    if numeric is None:
        return f"Invalid log level: {level}. Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL."

    _set_level(level)
    _current_level = level.upper()
    return "ok"


def get_log_level() -> str:
    """Return the current effective runtime log level."""
    return _current_level


def truncate_log(max_bytes: int = 0) -> dict[str, Any]:
    """Truncate the app log file (keep last ``max_bytes`` bytes).

    Returns a dict with ``status``, ``lines_before``, ``lines_after``.
    """
    log_file = _log_file
    if log_file is None:
        log_file = _resolve_data_dir(Path.home() / "Library/Application Support/Coworker") / f"{_DEFAULT_LOG_FILE_PREFIX}.log"
    if not log_file.exists():
        return {"status": "no_log_file"}

    total = log_file.stat().st_size
    lines_before = log_file.read_text(encoding="utf-8", errors="replace").count("\n")

    if max_bytes <= 0:
        log_file.write_text("", encoding="utf-8")
        return {"status": "cleared", "lines_before": lines_before, "lines_after": 0}

    # Keep last ``max_bytes`` bytes
    try:
        log_content = log_file.read_bytes()
        if len(log_content) > max_bytes:
            log_content = log_content[-max_bytes:]
        log_file.write_bytes(log_content)
    except OSError:
        return {"status": "io_error"}

    lines_after = log_content.decode("utf-8", errors="replace").count("\n")
    return {
        "status": "truncated",
        "lines_before": lines_before,
        "lines_after": lines_after,
        "size": len(log_content),
    }
