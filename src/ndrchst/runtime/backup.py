"""Tar.gz backup of a server's data_dir.

Layout on disk:
    ~/.ndrchst/backups/<server_id>/<timestamp>.tar.gz

Backups are not container-aware: caller is responsible for stopping the
server first if a quiesced backup is required.
"""
from __future__ import annotations

import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BACKUPS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "backups"


@dataclass(frozen=True, slots=True)
class Backup:
    server_id: str
    name: str       # filename, e.g. "20260518T120000Z.tar.gz"
    path: Path
    size: int

    @property
    def size_human(self) -> str:
        n = self.size
        for unit in ("B", "K", "M", "G", "T"):
            if n < 1024:
                return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}P"


def _server_root(server_id: str, root: Path) -> Path:
    out = root / server_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def create(
    *, server_id: str, data_dir: Path, root: Path = BACKUPS_ROOT_DEFAULT
) -> Backup:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}.tar.gz"
    target = _server_root(server_id, root) / name
    with tarfile.open(target, "w:gz") as tf:
        tf.add(data_dir, arcname=".")
    return Backup(server_id=server_id, name=name, path=target, size=target.stat().st_size)


def list_for(server_id: str, *, root: Path = BACKUPS_ROOT_DEFAULT) -> list[Backup]:
    server_dir = root / server_id
    if not server_dir.exists():
        return []
    out = [
        Backup(
            server_id=server_id, name=p.name, path=p, size=p.stat().st_size,
        )
        for p in sorted(server_dir.iterdir(), reverse=True)
        if p.is_file() and p.name.endswith(".tar.gz")
    ]
    return out


def restore(
    *,
    server_id: str,
    name: str,
    data_dir: Path,
    root: Path = BACKUPS_ROOT_DEFAULT,
) -> None:
    src = _server_root(server_id, root) / name
    if not src.exists():
        raise FileNotFoundError(f"backup not found: {name}")
    # Wipe-and-restore. Caller is expected to have stopped the server.
    if data_dir.exists():
        import shutil
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(src, "r:gz") as tf:
        # Slip guard: refuse any member that escapes data_dir
        base = data_dir.resolve()
        for member in tf.getmembers():
            target = (data_dir / member.name).resolve()
            if base != target and base not in target.parents:
                raise RuntimeError(f"refusing to extract outside data_dir: {member.name}")
        # `data` filter: deny absolute paths, traversal, special files (Py 3.12+)
        tf.extractall(data_dir, filter="data")


def delete(
    *, server_id: str, name: str, root: Path = BACKUPS_ROOT_DEFAULT
) -> None:
    target = _server_root(server_id, root) / name
    target.unlink(missing_ok=True)
