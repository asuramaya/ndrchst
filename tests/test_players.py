"""Player module tests using a stub RCON."""
from __future__ import annotations

from ndrchst.domain import players


def test_parse_list_empty():
    assert players.parse_list_response("There are 0 of a max of 20 players online:") == []


def test_parse_list_single():
    assert players.parse_list_response("There are 1 of a max of 20 players online: Alice") == ["Alice"]


def test_parse_list_multiple():
    out = players.parse_list_response("There are 2 of a max of 20 players online: Alice, Bob")
    assert out == ["Alice", "Bob"]


def test_parse_list_unrecognized():
    assert players.parse_list_response("garbage") == []


# Dispatch test — uses a fake RCON to confirm commands are formatted right
class _FakeRCON:
    def __init__(self):
        self.sent: list[str] = []
    async def command(self, cmd: str) -> str:
        self.sent.append(cmd)
        return f"ok:{cmd}"


async def test_dispatchers_format_commands_correctly():
    r = _FakeRCON()
    await players.kick(r, "Alice")
    await players.kick(r, "Alice", "afk too long")
    await players.whitelist_add(r, "Bob")
    await players.whitelist_remove(r, "Bob")
    await players.op(r, "Charlie")
    await players.deop(r, "Charlie")
    await players.ban(r, "Mallory", "griefing")
    await players.unban(r, "Mallory")
    assert r.sent == [
        "kick Alice",
        "kick Alice afk too long",
        "whitelist add Bob",
        "whitelist remove Bob",
        "op Charlie",
        "deop Charlie",
        "ban Mallory griefing",
        "pardon Mallory",
    ]
