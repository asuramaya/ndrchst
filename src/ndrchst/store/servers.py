"""Server-record CRUD. Plain functions, no repository class."""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime

from ..domain.models import Family, Server, ServerStatus


def _row_to_server(r: sqlite3.Row) -> Server:
    bedrock_bridge = None
    rcon_port = None
    rcon_password = None
    extra_jvm_flags = None
    env_vars = None
    cf_project_id = None
    cf_file_id = None
    neoforge_version = None
    with contextlib.suppress(IndexError, KeyError):
        bedrock_bridge = r["bedrock_bridge_port"]
    with contextlib.suppress(IndexError, KeyError):
        rcon_port = r["rcon_port"]
    with contextlib.suppress(IndexError, KeyError):
        rcon_password = r["rcon_password"]
    with contextlib.suppress(IndexError, KeyError):
        extra_jvm_flags = r["extra_jvm_flags"]
    with contextlib.suppress(IndexError, KeyError):
        env_vars = r["env_vars"]
    with contextlib.suppress(IndexError, KeyError):
        cf_project_id = r["cf_project_id"]
    with contextlib.suppress(IndexError, KeyError):
        cf_file_id = r["cf_file_id"]
    with contextlib.suppress(IndexError, KeyError):
        neoforge_version = r["neoforge_version"]
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
        bedrock_bridge_port=bedrock_bridge,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
        extra_jvm_flags=extra_jvm_flags,
        env_vars=env_vars,
        cf_project_id=cf_project_id,
        cf_file_id=cf_file_id,
        neoforge_version=neoforge_version,
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def insert(conn: sqlite3.Connection, s: Server) -> None:
    conn.execute(
        """INSERT INTO servers
            (id, name, platform_id, family, version, port, memory_mb,
             status, container_id, cross_play, bedrock_bridge_port,
             rcon_port, rcon_password, extra_jvm_flags, env_vars,
             cf_project_id, cf_file_id, neoforge_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            s.bedrock_bridge_port,
            s.rcon_port,
            s.rcon_password,
            s.extra_jvm_flags,
            s.env_vars,
            s.cf_project_id,
            s.cf_file_id,
            s.neoforge_version,
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


def update_config(
    conn: sqlite3.Connection,
    server_id: str,
    *,
    memory_mb: int,
    extra_jvm_flags: str | None,
    env_vars: str | None,
) -> None:
    conn.execute(
        "UPDATE servers SET memory_mb = ?, extra_jvm_flags = ?, env_vars = ? "
        "WHERE id = ?",
        (memory_mb, extra_jvm_flags, env_vars, server_id),
    )


def set_cf_pack(
    conn: sqlite3.Connection,
    server_id: str,
    project_id: int | None,
    file_id: int | None,
) -> None:
    """Pin (or clear) the CurseForge client-pack coordinates used to build
    the client's modpack CDN URL."""
    conn.execute(
        "UPDATE servers SET cf_project_id = ?, cf_file_id = ? WHERE id = ?",
        (project_id, file_id, server_id),
    )


def set_neoforge_version(
    conn: sqlite3.Connection, server_id: str, version: str | None
) -> None:
    """Pin (or clear) the NeoForge version the client installs for this server."""
    conn.execute(
        "UPDATE servers SET neoforge_version = ? WHERE id = ?",
        (version, server_id),
    )


def delete(conn: sqlite3.Connection, server_id: str) -> None:
    conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))


def port_in_use(conn: sqlite3.Connection, port: int, *, exclude: str | None = None) -> bool:
    """True iff any server has reserved this port. Checks the main port,
    the Geyser UDP bridge port, and the RCON port — all of which need to be
    host-unique because they're all docker-proxy bind targets."""
    if exclude is not None:
        row = conn.execute(
            "SELECT 1 FROM servers "
            "WHERE (port = ? OR bedrock_bridge_port = ? OR rcon_port = ?) AND id != ?",
            (port, port, port, exclude),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM servers WHERE port = ? OR bedrock_bridge_port = ? OR rcon_port = ?",
            (port, port, port),
        ).fetchone()
    return row is not None
