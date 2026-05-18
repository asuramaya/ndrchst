"""SQLite connection + schema bootstrap. One file, no ORM, no migrations
framework — schema lives in schema.sql and is applied with IF NOT EXISTS.

When the schema needs to grow past additive ALTERs, swap this for alembic.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".ndrchst" / "ndrchst.db"
_SCHEMA = Path(__file__).with_name("schema.sql").read_text()


def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatches sync routes to a thread pool,
    # so the connection created at lifespan-time must be usable from any worker
    # thread. SQLite itself is thread-safe at the C level (SERIALIZED mode);
    # the Python wrapper just gates it by default.
    conn = sqlite3.connect(
        path,
        isolation_level=None,  # autocommit; explicit BEGIN/COMMIT where we need txns
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
