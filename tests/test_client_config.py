"""The standalone client must autodetect the server it was pinned to instead of
forgetting it between launches and forcing another browser round-trip.

settings.load() consults the remembered pin (~/.ndrchst-client/client-config.json,
written by a ndrchst:// deep link) — but only for a GENERIC build, so a per-server
.zip (baked server_id) can't be hijacked by a stray pin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client" / "src"))

from ndrchst_client import settings


def _write_pin(p: Path, **fields) -> None:
    base = {"app_name": "ndrchst Client — ATM10", "server_host": "mc.ndrchst.com",
            "server_port": 25590, "mc_version": "1.21.1",
            "mods_sync_url": "https://play.ndrchst.com/client/abc"}
    p.write_text(json.dumps({**base, **fields}))


def test_generic_build_loads_remembered_pin(tmp_path, monkeypatch):
    pin = tmp_path / "remembered.json"
    _write_pin(pin, server_id="abc")
    monkeypatch.setattr(settings, "_REMEMBERED_CONFIG", pin)
    monkeypatch.delenv("NDRCHST_CLIENT_CONFIG", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)  # no client-config.json next to exe/cwd → pin wins

    cfg = settings.load()  # baked SERVER_ID is "" → generic → pin consulted
    assert cfg.server_host == "mc.ndrchst.com"
    assert cfg.server_port == 25590
    assert cfg.server_id == "abc"


def test_pinned_zip_ignores_remembered_pin(tmp_path, monkeypatch):
    pin = tmp_path / "remembered.json"
    _write_pin(pin, server_id="other", server_host="someone-elses-box")
    monkeypatch.setattr(settings, "_REMEMBERED_CONFIG", pin)
    monkeypatch.delenv("NDRCHST_CLIENT_CONFIG", raising=False)
    # Simulate a baked, already-pinned .zip build.
    baked = settings._from_baked()
    baked.update(server_id="zip-pinned", server_host="mc.ndrchst.com")
    monkeypatch.setattr(settings, "_from_baked", lambda: dict(baked))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    cfg = settings.load()
    assert cfg.server_id == "zip-pinned"
    assert cfg.server_host == "mc.ndrchst.com"  # the stray pin did not hijack it


def test_env_override_wins_over_pin(tmp_path, monkeypatch):
    pin = tmp_path / "remembered.json"
    _write_pin(pin, server_id="abc", server_host="pinned-host")
    explicit = tmp_path / "explicit.json"
    _write_pin(explicit, server_id="explicit", server_host="env-host")
    monkeypatch.setattr(settings, "_REMEMBERED_CONFIG", pin)
    monkeypatch.setenv("NDRCHST_CLIENT_CONFIG", str(explicit))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    cfg = settings.load()
    assert cfg.server_host == "env-host"
