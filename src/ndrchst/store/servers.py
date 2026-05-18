"""Server-record CRUD. Plain functions, no repository class."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ..domain.models import Family, Server, ServerStatus


def _row_to_server(r: sqlite3.Row) -> Server:
    return Server(
        id=r["id"],
        name=r["name"],
        platform_id=r["platform_id"],
        family=Family(r["family"]),
        version=r["version"],
        port=r["port"],
        memory_mb=r["memory_mb"],
        status=ServerStatus(r["status"]),
        container_id=r["container_id"],
        cross_play=bool(r["cross_play"]),
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def insert(conn: sqlite3.Connection, s: Server) -> None:
    conn.execute(
        """INSERT INTO servers
            (id, name, platform_id, family, version, port, memory_mb,
             status, container_id, cross_play, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            s.id,
            s.name,
            s.platform_id,
            s.family.value,
            s.version,
            s.port,
            s.memory_mb,
            s.status.value,
            s.container_id,
            int(s.cross_play),
            s.created_at.isoformat(),
        ),
    )


def get(conn: sqlite3.Connection, server_id: str) -> Server | None:
    row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    return _row_to_server(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[Server]:
    rows = conn.execute("SELECT * FROM servers ORDER BY created_at DESC").fetchall()
    return [_row_to_server(r) for r in rows]


def update_status(
    conn: sqlite3.Connection, server_id: str, status: ServerStatus
) -> None:
    conn.execute(
        "UPDATE servers SET status = ? WHERE id = ?",
        (status.value, server_id),
    )


def set_container_id(
    conn: sqlite3.Connection, server_id: str, container_id: str | None
) -> None:
    conn.execute(
        "UPDATE servers SET container_id = ? WHERE id = ?",
        (container_id, server_id),
    )


def delete(conn: sqlite3.Connection, server_id: str) -> None:
    conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))


def port_in_use(conn: sqlite3.Connection, port: int, *, exclude: str | None = None) -> bool:
    if exclude is not None:
        row = conn.execute(
            "SELECT 1 FROM servers WHERE port = ? AND id != ?", (port, exclude)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM servers WHERE port = ?", (port,)
        ).fetchone()
    return row is not None
