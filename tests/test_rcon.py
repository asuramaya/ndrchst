"""RCON tests using an in-process fake server.

Stresses:
  - happy path: auth + small command
  - auth failure: server returns request_id=-1
  - fragmentation: response split across two packets, terminated by canary
"""
from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable

import pytest

from ndrchst.runtime.rcon import RCON, AuthError

PORT_BASE = 25700


def _decode(buf: bytes) -> tuple[int, int, str]:
    length = struct.unpack("<i", buf[:4])[0]
    body = buf[4 : 4 + length]
    rid, type_ = struct.unpack("<ii", body[:8])
    payload = body[8:-2].decode("utf-8", errors="replace")
    return rid, type_, payload


def _encode(rid: int, type_: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00\x00"
    length = 4 + 4 + len(body)
    return struct.pack("<iii", length, rid, type_) + body


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    head = await reader.readexactly(4)
    length = struct.unpack("<i", head)[0]
    body = await reader.readexactly(length)
    rid, type_ = struct.unpack("<ii", body[:8])
    return rid, type_, body[8:-2].decode("utf-8", errors="replace")


async def _serve(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
    port: int,
) -> asyncio.Server:
    return await asyncio.start_server(handler, "127.0.0.1", port)


async def test_happy_path():
    port = PORT_BASE + 1

    async def handler(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        # auth
        rid, type_, payload = await _read_packet(r)
        assert type_ == 3 and payload == "secret"
        w.write(_encode(rid, 2, ""))  # auth ok
        await w.drain()

        # command + canary
        cmd_rid, _, cmd = await _read_packet(r)
        canary_rid, _, _ = await _read_packet(r)
        w.write(_encode(cmd_rid, 0, f"echo:{cmd}"))
        w.write(_encode(canary_rid, 0, "Unknown request"))
        await w.drain()

        w.close()
        await w.wait_closed()

    server = await _serve(handler, port)
    try:
        async with RCON("127.0.0.1", port, "secret") as r:
            out = await r.command("list")
            assert out == "echo:list"
    finally:
        server.close()
        await server.wait_closed()


async def test_auth_failure_raises():
    port = PORT_BASE + 2

    async def handler(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _read_packet(r)
        w.write(_encode(-1, 2, ""))  # auth rejected
        await w.drain()
        w.close()
        await w.wait_closed()

    server = await _serve(handler, port)
    try:
        with pytest.raises(AuthError):
            async with RCON("127.0.0.1", port, "wrong"):
                pass
    finally:
        server.close()
        await server.wait_closed()


async def test_fragmented_response_assembles():
    port = PORT_BASE + 3
    big_part_1 = "A" * 4000
    big_part_2 = "B" * 1500

    async def handler(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        rid, _, _ = await _read_packet(r)
        w.write(_encode(rid, 2, ""))  # auth ok
        await w.drain()

        cmd_rid, _, _ = await _read_packet(r)
        canary_rid, _, _ = await _read_packet(r)
        # Two fragments under same cmd_rid, then canary completion
        w.write(_encode(cmd_rid, 0, big_part_1))
        w.write(_encode(cmd_rid, 0, big_part_2))
        w.write(_encode(canary_rid, 0, "Unknown request"))
        await w.drain()
        w.close()
        await w.wait_closed()

    server = await _serve(handler, port)
    try:
        async with RCON("127.0.0.1", port, "x") as rcon:
            out = await rcon.command("seed")
            assert out == big_part_1 + big_part_2
            assert len(out) == 5500
    finally:
        server.close()
        await server.wait_closed()
