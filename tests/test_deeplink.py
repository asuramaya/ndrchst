"""Tests for the client's ndrchst:// deep-link parsing + single-instance forward.

The launcher is a separate package (client/src/ndrchst_client); deeplink.py is
pure stdlib, so we add it to the path and exercise parse() + the localhost
forwarder with temp paths — no display, no frozen binary needed.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client" / "src"))

from ndrchst_client import deeplink


def test_parse_launch_with_params():
    dl = deeplink.parse("ndrchst://launch?sid=abc123&code=XYZ")
    assert dl is not None
    assert dl.action == "launch"
    assert dl.params["sid"] == "abc123"
    assert dl.params["code"] == "XYZ"


def test_parse_tolerates_quotes_and_triple_slash():
    # Windows hands the handler a quoted "%1"; some shells produce ndrchst:///…
    assert deeplink.parse('"ndrchst://launch?sid=s"').action == "launch"
    assert deeplink.parse("ndrchst:///launch?sid=s").action == "launch"


def test_parse_rejects_other_schemes_and_junk():
    assert deeplink.parse("https://play.ndrchst.com/play") is None
    assert deeplink.parse("ndrchstx://launch") is None
    assert deeplink.parse("ndrchst://") is None  # no action
    assert deeplink.parse("") is None
    assert deeplink.parse(None) is None


def test_url_from_argv():
    assert deeplink.url_from_argv(
        ["--flag", "ndrchst://launch?sid=1", "tail"]) == "ndrchst://launch?sid=1"
    assert deeplink.url_from_argv(["--flag", "tail"]) is None


def test_single_instance_forward_roundtrip(tmp_path, monkeypatch):
    # Keep the instance file + data dir out of the real ~/.ndrchst-client.
    monkeypatch.setattr(deeplink, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(deeplink, "_INSTANCE_FILE", str(tmp_path / "instance.json"))
    received: list[str] = []
    got = threading.Event()
    assert deeplink.start_listener(lambda u: (received.append(u), got.set()))
    assert deeplink.try_forward("ndrchst://launch?sid=forwarded")
    assert got.wait(2.0)
    assert received == ["ndrchst://launch?sid=forwarded"]


def test_try_forward_with_no_primary_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(deeplink, "_INSTANCE_FILE", str(tmp_path / "absent.json"))
    assert deeplink.try_forward("ndrchst://launch?sid=x") is False
