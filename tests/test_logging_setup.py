"""Logging setup: JSON formatter, env config, file rotation, idempotency."""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from ndrchst import logging_setup


@pytest.fixture(autouse=True)
def reset_logger():
    """Each test starts from a fresh, unconfigured ndrchst logger."""
    logger = logging.getLogger("ndrchst")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    if hasattr(logger, logging_setup._MARKER):
        delattr(logger, logging_setup._MARKER)
    logger.setLevel(logging.NOTSET)
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
    if hasattr(logger, logging_setup._MARKER):
        delattr(logger, logging_setup._MARKER)


def _capture(logger: logging.Logger) -> io.StringIO:
    """Replace whatever stream the configured handler has with an in-memory buffer."""
    buf = io.StringIO()
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.stream = buf
    return buf


def test_json_formatter_emits_one_object_per_line():
    logger = logging_setup.configure(as_json=True, level="INFO")
    buf = _capture(logger)
    logger.info("hello world")
    line = buf.getvalue().strip()
    record = json.loads(line)
    assert record["msg"] == "hello world"
    assert record["level"] == "INFO"
    assert record["logger"] == "ndrchst"
    assert "ts" in record


def test_extras_are_merged_into_json_payload():
    logger = logging_setup.configure(as_json=True, level="INFO")
    buf = _capture(logger)
    logger.info("created", extra={"server_id": "abc123", "port": 25565})
    record = json.loads(buf.getvalue().strip())
    assert record["server_id"] == "abc123"
    assert record["port"] == 25565


def test_exception_info_serialized():
    logger = logging_setup.configure(as_json=True, level="INFO")
    buf = _capture(logger)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("oh no")
    record = json.loads(buf.getvalue().strip())
    assert "RuntimeError" in record["exc"]
    assert "boom" in record["exc"]


def test_human_format_when_json_disabled():
    logger = logging_setup.configure(as_json=False, level="INFO")
    buf = _capture(logger)
    logger.info("plain text")
    line = buf.getvalue().strip()
    # Not JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert "plain text" in line
    assert "INFO" in line


def test_level_filtering_drops_messages_below_threshold():
    logger = logging_setup.configure(as_json=True, level="WARNING")
    buf = _capture(logger)
    logger.debug("debug")
    logger.info("info")
    logger.warning("warning")
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["msg"] == "warning"


def test_idempotent_does_not_add_duplicate_handlers():
    logger = logging_setup.configure(as_json=True, level="INFO")
    n = len(logger.handlers)
    logging_setup.configure(as_json=True, level="INFO")
    logging_setup.configure(as_json=True, level="INFO")
    assert len(logger.handlers) == n


def test_force_reconfigures_and_replaces_handlers():
    logger = logging_setup.configure(as_json=True, level="INFO")
    # Reconfigure to plain text
    logger = logging_setup.configure(as_json=False, level="DEBUG", force=True)
    buf = _capture(logger)
    logger.debug("now visible")
    assert "now visible" in buf.getvalue()


def test_rotating_file_handler_writes_and_rotates(tmp_path: Path):
    log_file = tmp_path / "ndrchst.log"
    # rotate at ~1 KB so we don't need megabytes of data; rotate_mb takes
    # integers, so we patch the threshold by passing 0 then manually adjusting.
    logger = logging_setup.configure(
        as_json=True, level="INFO", file=log_file, rotate_mb=0,
    )
    # Force a small rotation threshold so we can trigger it deterministically
    from logging.handlers import RotatingFileHandler
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            h.maxBytes = 512

    for i in range(50):
        logger.info("event %d", i, extra={"idx": i, "filler": "x" * 50})

    # The base file exists and at least one rotation (.log.1) should be present
    assert log_file.exists()
    rotated = list(tmp_path.glob("ndrchst.log.*"))
    assert rotated, "expected at least one rotated file"


def test_env_var_drives_level_when_no_kwarg(monkeypatch):
    monkeypatch.setenv("NDRCHST_LOG_LEVEL", "ERROR")
    logger = logging_setup.configure()
    assert logger.level == logging.ERROR


def test_env_var_disables_json(monkeypatch):
    monkeypatch.setenv("NDRCHST_LOG_JSON", "0")
    logger = logging_setup.configure()
    buf = _capture(logger)
    logger.info("plain")
    with pytest.raises(json.JSONDecodeError):
        json.loads(buf.getvalue().strip())


def test_logger_does_not_propagate_to_root():
    """We have our own handlers; bubbling to root would double-emit."""
    logger = logging_setup.configure(as_json=True)
    assert logger.propagate is False
