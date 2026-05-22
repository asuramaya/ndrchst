"""Operator-tunable runtime knobs, settable live from the in-game op surface.

A tiny persisted overrides store so the single operator (admin plane is
single-operator by design) can retune the economy — the daily cooldown and the
refresh cadences — without a redeploy, and have it survive a restart. A read
falls back to an env var then a built-in default; ``set`` records an override
and best-effort-persists it to JSON. The in-game ``/ndrchst config`` command
reaches these through a bridge-gated box endpoint.

Knob values are plain integers (seconds), so they're trivial to validate and
display. Reading happens on hot-ish paths (every /daily claim, each loop pass),
so it's lock-light and never touches the network.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

_log = logging.getLogger("ndrchst.opcfg")

# key -> (env var fallback, built-in default). Seconds.
KNOBS: dict[str, tuple[str, int]] = {
    "daily_cooldown_s":    ("NDRCHST_DAILY_COOLDOWN_S", 24 * 3600),
    "snapshot_interval_s": ("NDRCHST_SNAPSHOT_INTERVAL", 3600),
    "price_interval_s":    ("NDRCHST_PRICE_INTERVAL", 600),
}

# A floor per knob so an operator can't wedge the box (e.g. a 1s snapshot loop
# hammering the metered RPC). 0 is allowed for the loop intervals = "disable".
_MIN: dict[str, int] = {
    "daily_cooldown_s": 0,
    "snapshot_interval_s": 0,
    "price_interval_s": 0,
}

_lock = threading.Lock()
_overrides: dict[str, int] = {}


def _path() -> Path:
    return Path(os.environ.get(
        "NDRCHST_OP_CONFIG", str(Path.home() / ".ndrchst" / "op-config.json")))


def _load() -> None:
    global _overrides
    try:
        raw = json.loads(_path().read_text())
        _overrides = {k: int(v) for k, v in raw.items() if k in KNOBS}
    except FileNotFoundError:
        _overrides = {}
    except Exception:
        _log.warning("op-config unreadable; ignoring overrides")
        _overrides = {}


_load()


def get(key: str) -> int:
    """Override > env > default. Raises KeyError for an unknown knob."""
    if key not in KNOBS:
        raise KeyError(key)
    with _lock:
        if key in _overrides:
            return _overrides[key]
    env, default = KNOBS[key]
    raw = os.environ.get(env)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def set(key: str, value: int) -> int:
    """Record + persist an override. Clamps to the knob's floor. Returns the
    value actually applied. KeyError for an unknown knob."""
    if key not in KNOBS:
        raise KeyError(key)
    value = max(int(value), _MIN[key])
    with _lock:
        _overrides[key] = value
        try:
            p = _path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_overrides))
        except Exception:
            # In-memory value still applies this run; just won't survive restart.
            _log.warning("op-config not persisted (%s)", _path())
    return value


def all_values() -> dict[str, int]:
    return {k: get(k) for k in KNOBS}


def _reset_for_tests() -> None:
    with _lock:
        _overrides.clear()
