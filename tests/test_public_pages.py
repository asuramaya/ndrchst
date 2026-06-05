"""The static public pages (landing + play). Wallet-free by construction."""
from __future__ import annotations

from ndrchst.web.public_pages import render_landing, render_play

_SERVERS = [
    {"id": "abc123", "name": "Test SMP", "version": "1.21.1", "status": "running",
     "cross_play": False, "client_url": "/client/abc123/client.zip"},
    {"id": "def456", "name": "Crossplay", "version": "1.21.3", "status": "stopped",
     "cross_play": True, "client_url": "/client/def456/client.zip"},
]

_FORBIDDEN = ("wallet", "solana", "siws", "tier", "holdings", "/auth/", "phantom", "$ndrchst")


def _clean(html: str) -> None:
    low = html.lower()
    for tok in _FORBIDDEN:
        assert tok not in low, f"public page must not mention {tok!r}"


def test_landing_renders_and_is_walletfree():
    html = render_landing(downloads_base="https://play.example.com/client")
    assert "<!doctype html>" in html.lower()
    assert "ndrchst-client-windows-x86_64.exe" in html
    assert 'href="/play"' in html
    _clean(html)


def test_landing_without_downloads_base_falls_back():
    html = render_landing()
    # No binary links when there's no downloads base; points users to a server.
    assert ".exe" not in html
    assert "/play" in html
    _clean(html)


def test_play_lists_servers_with_client_links():
    html = render_play(_SERVERS, downloads_base="https://play.example.com/client")
    assert "Test SMP" in html and "Crossplay" in html
    assert "/client/abc123/client.zip" in html
    assert "running" in html and "stopped" in html
    _clean(html)


def test_play_empty_is_graceful():
    html = render_play([], downloads_base="")
    assert "No servers" in html
    _clean(html)
