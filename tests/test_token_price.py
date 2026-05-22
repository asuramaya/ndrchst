"""The $NDRCHST price ticker is a best-effort DECORATION sourced from
DexScreener (free, keyless) — NOT the metered Solana RPC. These lock down: it
parses the most-liquid pair, it never raises, and the cache keeps the last good
value across a failed refresh (stale-OK), serving None only until the first hit.
"""
from __future__ import annotations

from ndrchst.runtime import token_price


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self._p = payload

    def get(self, _url):
        return _Resp(self._p)

    def close(self):
        return None


class _BoomClient:
    def get(self, _url):
        raise RuntimeError("network down")

    def close(self):
        return None


def test_fetch_picks_most_liquid_pair():
    payload = {"pairs": [
        {"priceUsd": "0.0012", "marketCap": 1_200_000, "liquidity": {"usd": 5_000},
         "baseToken": {"symbol": "NDRCHST"}, "url": "https://dexscreener.com/x"},
        {"priceUsd": "0.0011", "marketCap": 1_100_000, "liquidity": {"usd": 50_000},
         "baseToken": {"symbol": "NDRCHST"}, "url": "https://dexscreener.com/y"},
    ]}
    out = token_price.fetch(client=_Client(payload))
    assert out["price_usd"] == 0.0011          # the deeper-liquidity pair wins
    assert out["market_cap"] == 1_100_000
    assert out["url"].endswith("/y")


def test_fetch_falls_back_to_fdv_when_no_marketcap():
    payload = {"pairs": [
        {"priceUsd": "0.5", "fdv": 999, "liquidity": {"usd": 1},
         "baseToken": {"symbol": "NDRCHST"}},
    ]}
    assert token_price.fetch(client=_Client(payload))["market_cap"] == 999


def test_fetch_empty_or_missing_pairs_is_none():
    assert token_price.fetch(client=_Client({"pairs": []})) is None
    assert token_price.fetch(client=_Client({})) is None


def test_fetch_swallows_network_errors():
    assert token_price.fetch(client=_BoomClient()) is None


def test_refresh_caches_and_keeps_last_good(monkeypatch):
    token_price._reset_for_tests()
    assert token_price.get() is None  # nothing cached yet → ticker hides

    good = {"price_usd": 0.5, "market_cap": 1_000_000, "symbol": "NDRCHST", "url": "u"}
    monkeypatch.setattr(token_price, "fetch", lambda **k: good)
    token_price.refresh()
    cached = token_price.get()
    assert cached["price_usd"] == 0.5
    assert "age_s" in cached

    # A failed refresh must NOT wipe the last good value (stale-OK decoration).
    monkeypatch.setattr(token_price, "fetch", lambda **k: None)
    token_price.refresh()
    assert token_price.get()["price_usd"] == 0.5
    token_price._reset_for_tests()


def test_ranks_renders_ticker_when_present():
    from ndrchst.web.public_pages import render_ranks
    tiers = [{"key": "holder", "name": "Holder", "min_pct": 0.0}]
    html = render_ranks([], tiers, ticker={
        "price_usd": 0.0012, "market_cap": 1_200_000, "symbol": "NDRCHST",
        "url": "https://dexscreener.com/solana/x"})
    assert 'class="ticker"' in html
    assert "$0.0012" in html
    assert "MC $1.2M" in html


def test_ranks_omits_ticker_when_absent():
    from ndrchst.web.public_pages import render_ranks
    tiers = [{"key": "holder", "name": "Holder", "min_pct": 0.0}]
    assert 'class="ticker"' not in render_ranks([], tiers, ticker=None)
