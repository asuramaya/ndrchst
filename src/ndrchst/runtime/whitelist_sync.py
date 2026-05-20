"""Push wallet identities to a Java server over RCON: whitelist each derived
in-game name and, optionally, set its rank tier.

Admin-side only — RCON is full console access, so this never runs on the
internet-facing public surface. The public surface only records wallet links
in the DB (store.wallet_links); the admin reads them and syncs here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..store.wallet_links import WalletLink
from .rcon import RCON, RCONError


def rank_command_template() -> str | None:
    """Server command to set a rank, with {name}/{tier} placeholders, e.g.
    LuckPerms: "lp user {name} parent set {tier}". Unset -> rank is not pushed
    (whitelist only), so a server without a permissions mod still works."""
    return os.environ.get("NDRCHST_RANK_CMD") or None


@dataclass(frozen=True, slots=True)
class SyncResult:
    mc_name: str
    whitelisted: bool
    ranked: bool
    error: str | None = None


async def sync_links_to_server(
    rcon_host: str,
    rcon_port: int,
    rcon_password: str,
    links: list[WalletLink],
    *,
    rank_template: str | None = None,
    rcon_factory: type[RCON] = RCON,
    timeout: float = 8.0,
) -> list[SyncResult]:
    """Whitelist (and optionally rank) each linked wallet's in-game name.

    Does NOT toggle `whitelist on/off` — enabling enforcement is an operator
    decision, since it would gate existing players. mc_name is derived from a
    verified pubkey ([A-Za-z0-9_] only), so it is safe to interpolate."""
    template = rank_template if rank_template is not None else rank_command_template()
    results: list[SyncResult] = []
    async with rcon_factory(rcon_host, rcon_port, rcon_password, timeout=timeout) as r:
        for link in links:
            try:
                await r.command(f"whitelist add {link.mc_name}")
                ranked = False
                if template and link.tier:
                    await r.command(template.format(name=link.mc_name, tier=link.tier))
                    ranked = True
                results.append(SyncResult(link.mc_name, whitelisted=True, ranked=ranked))
            except RCONError as e:
                results.append(SyncResult(link.mc_name, whitelisted=False,
                                          ranked=False, error=str(e)))
    return results
