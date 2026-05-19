"""Write Minecraft's servers.dat (the Multiplayer server list) so the
pilot's target server is pre-listed and one click to join.

servers.dat is uncompressed NBT. We only need a tiny subset of the
spec (compound / list / string / byte), so we encode it by hand rather
than pull an NBT dependency into the pilot.

Layout:
  TAG_Compound ""
    TAG_List "servers" <TAG_Compound>
      [ { name: <str>, ip: <str>, hidden: 0 } ]
"""
from __future__ import annotations

import struct
from pathlib import Path

_TAG_END = 0
_TAG_BYTE = 1
_TAG_STRING = 8
_TAG_LIST = 9
_TAG_COMPOUND = 10


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _named(tag: int, name: str, payload: bytes) -> bytes:
    return bytes([tag]) + _str(name) + payload


def _server_compound(name: str, ip: str) -> bytes:
    body = b""
    body += _named(_TAG_STRING, "ip", _str(ip))
    body += _named(_TAG_STRING, "name", _str(name))
    body += _named(_TAG_BYTE, "hidden", bytes([0]))
    body += bytes([_TAG_END])
    return body


def write_servers_dat(path: Path, servers: list[tuple[str, str]]) -> None:
    """Write `servers` (list of (name, ip)) to `path` as servers.dat.

    Overwrites any existing file. `ip` is a host or host:port string,
    e.g. "127.0.0.1:25565".
    """
    # Build the TAG_List payload: element-type byte, count int, then
    # each compound's body (compounds in a list are unnamed).
    list_payload = bytes([_TAG_COMPOUND]) + struct.pack(">i", len(servers))
    for name, ip in servers:
        list_payload += _server_compound(name, ip)

    root_body = _named(_TAG_LIST, "servers", list_payload) + bytes([_TAG_END])
    # Root is a named compound with an empty name.
    data = bytes([_TAG_COMPOUND]) + _str("") + root_body
    path.write_bytes(data)
