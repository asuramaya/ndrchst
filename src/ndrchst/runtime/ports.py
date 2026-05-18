"""Host-level port-availability probe.

Even when our SQLite says a port is free, the host can have it bound by an
unrelated process or container. We try a real bind+release and report.

Java is TCP, Bedrock is UDP — the bind has to use the matching socket type.
"""
from __future__ import annotations

import socket

from ..domain.models import Family


def is_port_free(port: int, family: Family, host: str = "0.0.0.0") -> bool:
    sock_type = socket.SOCK_DGRAM if family is Family.BEDROCK else socket.SOCK_STREAM
    s = socket.socket(socket.AF_INET, sock_type)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError:
        return False
    finally:
        s.close()
    return True
