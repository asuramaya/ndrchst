"""The client's self-updater contract: it must detect a newer build for THIS
platform from the published manifest and point at the right asset URL. (The
GUI prompt that consumes this lives in app.py and needs a display, so it's not
exercised here — but check() is the gate that decides whether an update happens
at all, so it's the part worth locking down.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_CLIENT_SRC = Path(__file__).resolve().parent.parent / "client" / "src"
sys.path.insert(0, str(_CLIENT_SRC))

from ndrchst_client import updater  # noqa: E402


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._d = data

    def read(self) -> bytes:
        return self._d

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a) -> bool:
        return False


def _manifest(version: str, plat: str | None = None) -> bytes:
    plat = plat or updater.platform_key()
    return json.dumps({
        "version": version,
        "notes": "what changed",
        "assets": {plat: {"file": "ndrchst-client-bin", "sha256": "deadbeef"}},
    }).encode()


def _patch_manifest(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _Resp(data))


def test_version_ordering():
    assert updater._is_newer("0.2.3", "0.2.2")
    assert updater._is_newer("0.2.10", "0.2.9")   # numeric, not lexical
    assert not updater._is_newer("0.2.2", "0.2.2")
    assert not updater._is_newer("0.2.1", "0.2.2")


def test_check_detects_newer_with_asset(monkeypatch):
    _patch_manifest(monkeypatch, _manifest("0.2.3"))
    info = updater.check("https://play.ndrchst.com/client", current="0.2.0")
    assert info is not None
    assert info.version == "0.2.3"
    assert info.url == "https://play.ndrchst.com/client/ndrchst-client-bin"
    assert info.sha256 == "deadbeef"


def test_check_same_version_is_none(monkeypatch):
    _patch_manifest(monkeypatch, _manifest("0.2.3"))
    assert updater.check("https://e/client", current="0.2.3") is None


def test_check_no_asset_for_platform_is_none(monkeypatch):
    _patch_manifest(monkeypatch, _manifest("0.2.3", plat="some-other-os"))
    assert updater.check("https://e/client", current="0.2.0") is None


def test_check_no_base_url_is_none():
    assert updater.check("", current="0.2.0") is None


def test_show_update_does_not_shadow_header_frame():
    """Regression: show_update's arg must NOT be named `info` — that shadowed the
    header frame the update bar packs `after=`, raising in pack() so the prompt
    never appeared (the whole auto-update silently dead). Read as text so we
    don't import tkinter/portablemc here."""
    src = (_CLIENT_SRC / "ndrchst_client" / "app.py").read_text()
    assert "def show_update(info)" not in src, "show_update arg shadows the `info` frame again"
