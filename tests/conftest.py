"""Shared test guards.

The public app starts a price-ticker loop that refreshes immediately on boot
(so the ticker shows soon after a restart). Under TestClient that would fire a
real DexScreener request, making the suite network-dependent and slow. Disable
the loop suite-wide by pinning its interval to 0; tests exercise the fetch/cache
directly and the in-game/page surfaces by monkeypatching ``token_price.get``.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_price_loop(monkeypatch, tmp_path):
    # Read at lifespan time, so this disables the background loop without
    # touching fetch()/refresh()/get(), which the unit tests still exercise.
    monkeypatch.setenv("NDRCHST_PRICE_INTERVAL", "0")
    # Isolate op-config overrides from any real file on the dev machine.
    monkeypatch.setenv("NDRCHST_OP_CONFIG", str(tmp_path / "op-config.json"))
    try:
        from ndrchst.runtime import op_config, token_price
    except Exception:
        yield
        return
    token_price._reset_for_tests()
    op_config._reset_for_tests()
    yield
    token_price._reset_for_tests()
    op_config._reset_for_tests()
