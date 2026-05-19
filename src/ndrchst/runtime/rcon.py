"""RCON client (Valve / Source protocol).

Java only. Bedrock has no RCON — `runtime.lifecycle` dispatches Bedrock
commands through the BDS stdin pipe instead.

Differences from the v2 port:
  * async-native via asyncio.open_connection (FastAPI is async)
  * AuthError is a real exception, not `RCONResponse(success=False, ...)`
  * fragmentation: large responses (>4096 bytes) arrive split across packets;
    we send a canary follow-up to detect the end
  * no global pool — connections are caller-owned, opened via async-context
"""
from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass

_AUTH = 3
_EXECCOMMAND = 2
_RESPONSE_VALUE = 0
# _AUTH_RESPONSE shares type=2 with EXECCOMMAND; disambiguated by request_id
_CANARY_TYPE = 100  # invalid type used to detect end-of-fragmentation

_MAX_PAYLOAD = 4096


class RCONError(Exception):
    """Generic RCON failure."""


class AuthError(RCONError):
    """Server rejected the password."""


@dataclass(frozen=True, slots=True)
class Packet:
    request_id: int
    type: int
    payload: str


def _encode(request_id: int, type_: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00\x00"
    length = 4 + 4 + len(body)
    return struct.pack("<iii", length, request_id, type_) + body


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    buf = await reader.readexactly(n)
    return buf


async def _read_packet(reader: asyncio.StreamReader) -> Packet:
    length_bytes = await _read_exact(reader, 4)
    length = struct.unpack("<i", length_bytes)[0]
    if length < 10:
        raise RCONError(f"packet length {length} below protocol minimum")
    body = await _read_exact(reader, length)
    request_id, type_ = struct.unpack("<ii", body[:8])
    # strip the two trailing NULs
    payload = body[8:-2].decode("utf-8", errors="replace")
    return Packet(request_id=request_id, type=type_, payload=payload)


class RCON:
    """Async RCON client. Use as `async with RCON(...) as r:`."""

    def __init__(self, host: str, port: int, password: str, *, timeout: float = 5.0):
        self.host = host
        self.port = port
        self._password = password
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._req_id = 0

    async def __aenter__(self) -> RCON:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self._timeout
        )
        req_id = self._next_id()
        self._writer.write(_encode(req_id, _AUTH, self._password))
        await self._writer.drain()

        # Mojang servers send a (usually empty) RESPONSE_VALUE before the
        # AUTH_RESPONSE. Drain until we see type=2.
        while True:
            pkt = await asyncio.wait_for(_read_packet(self._reader), timeout=self._timeout)
            if pkt.type == _EXECCOMMAND:  # == AUTH_RESPONSE
                if pkt.request_id == -1:
                    await self.close()
                    raise AuthError("RCON authentication failed")
                if pkt.request_id != req_id:
                    await self.close()
                    raise RCONError("RCON auth response request_id mismatch")
                return

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await self._writer.wait_closed()
        self._reader = self._writer = None

    async def command(self, cmd: str) -> str:
        if self._writer is None or self._reader is None:
            raise RCONError("not connected")
        if len(cmd.encode("utf-8")) > _MAX_PAYLOAD - 2:
            raise RCONError("command exceeds 4094 bytes")

        cmd_id = self._next_id()
        self._writer.write(_encode(cmd_id, _EXECCOMMAND, cmd))
        await self._writer.drain()

        # Read packets until we get one with our cmd_id. Most responses fit
        # in a single packet (<4096 bytes). For fragmented responses, Paper
        # streams multiple cmd_id packets back-to-back; we collect until the
        # read times out briefly, which means the server is done.
        chunks: list[str] = []
        first_timeout = self._timeout
        # After first chunk, use a tight timeout to detect end-of-stream.
        idle_timeout = 0.3
        timeout = first_timeout
        while True:
            try:
                pkt = await asyncio.wait_for(_read_packet(self._reader), timeout=timeout)
            except (TimeoutError, asyncio.IncompleteReadError):
                break
            if pkt.request_id == cmd_id:
                chunks.append(pkt.payload)
                timeout = idle_timeout
            # ignore stray packets (different request_id) but stay in the loop
        return "".join(chunks)

    def _next_id(self) -> int:
        self._req_id = (self._req_id + 1) & 0x7FFFFFFF
        return self._req_id
