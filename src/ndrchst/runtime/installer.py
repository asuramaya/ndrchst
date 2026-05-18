"""Mod / plugin / pack installer.

Downloads a Modrinth Version into the correct subdir of a server's data_dir,
verifies SHA1, and records the install in the installed_assets table.

Per-family destination map:

  Java + AssetKind.PLUGIN              -> data_dir/plugins/
  Java + AssetKind.MOD                 -> data_dir/mods/
  Java + AssetKind.DATAPACK            -> data_dir/world/datapacks/
  Java + AssetKind.RESOURCEPACK        -> data_dir/resourcepacks/   (server resource pack staging)
  Bedrock + AssetKind.RESOURCEPACK     -> data_dir/resource_packs/
  Bedrock + AssetKind.BEHAVIORPACK     -> data_dir/behavior_packs/

The Modrinth artifact is whatever the version metadata calls the "primary
file" — typically a .jar (Java) or .mcpack/.zip (Bedrock).
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..domain.models import Family
from ..mods.base import AssetKind
from ..mods.modrinth import Version


class InstallerError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class InstallResult:
    asset_id: str
    version: str
    file_path: Path
    sha1: str


_JAVA_DIRS: dict[AssetKind, str] = {
    AssetKind.PLUGIN: "plugins",
    AssetKind.MOD: "mods",
    AssetKind.DATAPACK: "world/datapacks",
    AssetKind.RESOURCEPACK: "resourcepacks",
}
_BEDROCK_DIRS: dict[AssetKind, str] = {
    AssetKind.RESOURCEPACK: "resource_packs",
    AssetKind.BEHAVIORPACK: "behavior_packs",
}


def _dest_dir(family: Family, kind: AssetKind) -> str:
    mapping = _JAVA_DIRS if family is Family.JAVA else _BEDROCK_DIRS
    if kind not in mapping:
        raise InstallerError(f"asset kind {kind.value!r} not installable on {family.value} servers")
    return mapping[kind]


async def install(
    *,
    data_dir: Path,
    family: Family,
    kind: AssetKind,
    version: Version,
    client: httpx.AsyncClient | None = None,
) -> InstallResult:
    """Download + verify + place. Pure function — caller records to DB."""
    if not version.download_url:
        raise InstallerError("version has no download_url")

    subdir = data_dir / _dest_dir(family, kind)
    subdir.mkdir(parents=True, exist_ok=True)
    target = subdir / version.file_name

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=120.0)
    try:
        sha = hashlib.sha1()
        async with client.stream("GET", version.download_url, follow_redirects=True) as resp:
            resp.raise_for_status()
            with target.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    sha.update(chunk)

        got = sha.hexdigest()
        if version.sha1 and got != version.sha1:
            target.unlink(missing_ok=True)
            raise InstallerError(
                f"sha1 mismatch for {version.file_name}: "
                f"expected {version.sha1}, got {got}"
            )
        return InstallResult(
            asset_id=version.project_id,
            version=version.version_number,
            file_path=target,
            sha1=got,
        )
    finally:
        if own_client:
            await client.aclose()


# ─── DB record helpers ─────────────────────────────────────────────────────


def record_install(
    conn: sqlite3.Connection,
    *,
    server_id: str,
    source_id: str,
    kind: AssetKind,
    result: InstallResult,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO installed_assets
            (server_id, source_id, asset_id, kind, version, installed_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (server_id, source_id, result.asset_id, kind.value, result.version),
    )


def list_installed(
    conn: sqlite3.Connection, server_id: str
) -> list[dict]:
    rows = conn.execute(
        """SELECT source_id, asset_id, kind, version, installed_at
             FROM installed_assets WHERE server_id = ?
             ORDER BY installed_at DESC""",
        (server_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_installed(
    conn: sqlite3.Connection, server_id: str, source_id: str, asset_id: str
) -> None:
    conn.execute(
        """DELETE FROM installed_assets
           WHERE server_id = ? AND source_id = ? AND asset_id = ?""",
        (server_id, source_id, asset_id),
    )
