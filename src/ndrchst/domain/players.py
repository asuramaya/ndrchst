"""Player management via RCON (Java only).

Parses `/list` output, dispatches /kick, /whitelist, /op, /ban. Bedrock has
no RCON; the players UI hides this tab on Bedrock and points users at the
console.
"""
from __future__ import annotations

import re

from ..runtime.rcon import RCON

# `/list` output examples:
#   "There are 0 of a max of 20 players online:"
#   "There are 2 of a max of 20 players online: Alice, Bob"
_LIST_RE = re.compile(r"online:\s*(.*)$", re.IGNORECASE)


def parse_list_response(payload: str) -> list[str]:
    m = _LIST_RE.search(payload.strip())
    if not m:
        return []
    rest = m.group(1).strip()
    if not rest:
        return []
    return [name.strip() for name in rest.split(",") if name.strip()]


async def online(rcon: RCON) -> list[str]:
    return parse_list_response(await rcon.command("list"))


async def kick(rcon: RCON, player: str, reason: str = "") -> str:
    suffix = f" {reason}" if reason else ""
    return await rcon.command(f"kick {player}{suffix}")


async def whitelist_add(rcon: RCON, player: str) -> str:
    return await rcon.command(f"whitelist add {player}")


async def whitelist_remove(rcon: RCON, player: str) -> str:
    return await rcon.command(f"whitelist remove {player}")


async def op(rcon: RCON, player: str) -> str:
    return await rcon.command(f"op {player}")


async def deop(rcon: RCON, player: str) -> str:
    return await rcon.command(f"deop {player}")


async def ban(rcon: RCON, player: str, reason: str = "") -> str:
    suffix = f" {reason}" if reason else ""
    return await rcon.command(f"ban {player}{suffix}")


async def unban(rcon: RCON, player: str) -> str:
    return await rcon.command(f"pardon {player}")
