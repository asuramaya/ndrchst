"""Structured JSON logging + optional file rotation.

Idempotent: ``configure()`` may be called multiple times (e.g. CLI + lifespan)
without duplicating handlers. Config can come from kwargs or env vars:

    NDRCHST_LOG_LEVEL      DEBUG|INFO|WARNING|ERROR (default INFO)
    NDRCHST_LOG_JSON       1|0 — emit JSON lines vs human format (default 1)
    NDRCHST_LOG_FILE       absolute path; enables RotatingFileHandler
    NDRCHST_LOG_ROTATE_MB  rotate threshold for the file handler (default 10)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MARKER = "ndrchst.configured"
_LOGGER_NAME = "ndrchst"
_STD_RECORD_FIELDS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extras attached via ``logger.info(..., extra={...})``
    are merged into the record so callers can attach context without string-formatting."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _STD_RECORD_FIELDS or k.startswith("_"):
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def configure(
    *,
    level: str | None = None,
    as_json: bool | None = None,
    file: Path | str | None = None,
    rotate_mb: int | None = None,
    force: bool = False,
) -> logging.Logger:
    """Wire up the ndrchst logger. Safe to call repeatedly.

    Returns the configured ``ndrchst`` logger so callers can chain.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, _MARKER, False) and not force:
        return logger

    level = (level or os.environ.get("NDRCHST_LOG_LEVEL") or "INFO").upper()
    if as_json is None:
        as_json = _env_bool("NDRCHST_LOG_JSON", True)
    if file is None:
        env_file = os.environ.get("NDRCHST_LOG_FILE")
        file = Path(env_file) if env_file else None
    if rotate_mb is None:
        rotate_mb = int(os.environ.get("NDRCHST_LOG_ROTATE_MB", "10"))

    # Clear any prior handlers so a reconfigure is clean.
    for h in list(logger.handlers):
        logger.removeHandler(h)

    formatter: logging.Formatter
    if as_json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if file is not None:
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            path, maxBytes=rotate_mb * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        logger.addHandler(rotating)

    logger.setLevel(level)
    # Don't double-emit through the root logger
    logger.propagate = False
    setattr(logger, _MARKER, True)
    return logger
