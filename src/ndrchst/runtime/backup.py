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

# Auto-snapshots taken before destructive operations are name-prefixed so the
# UI can distinguish them and so rotation can trim them without touching
# user-created backups.
SAFETY_PREFIX = "auto-"
SAFETY_KEEP = 5


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

    @property
    def is_safety(self) -> bool:
        return self.name.startswith(SAFETY_PREFIX)

    @property
    def safety_reason(self) -> str | None:
        """For auto-snapshots, returns the reason embedded in the name
        ('pre-install', 'pre-restore', etc.); None for user backups."""
        if not self.is_safety:
            return None
        # Format: auto-<reason>-<stamp>.tar.gz
        stripped = self.name.removeprefix(SAFETY_PREFIX).removesuffix(".tar.gz")
        parts = stripped.rsplit("-", 1)
        return parts[0] if len(parts) == 2 else None


def _server_root(server_id: str, root: Path) -> Path:
    out = root / server_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _root(root: Path | None) -> Path:
    """Resolve the backups root, honoring runtime monkeypatching of the
    module-level default. (A function default would freeze the value at
    definition time and break tests that swap the default in fixtures.)"""
    return root if root is not None else BACKUPS_ROOT_DEFAULT


def create(
    *, server_id: str, data_dir: Path, root: Path | None = None,
) -> Backup:
    root = _root(root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}.tar.gz"
    target = _server_root(server_id, root) / name
    with tarfile.open(target, "w:gz") as tf:
        tf.add(data_dir, arcname=".")
    return Backup(server_id=server_id, name=name, path=target, size=target.stat().st_size)


def create_safety(
    *,
    server_id: str,
    data_dir: Path,
    reason: str,
    root: Path | None = None,
    keep: int = SAFETY_KEEP,
) -> Backup | None:
    root = _root(root)
    """Snapshot the data_dir before a destructive operation.

    Naming convention: ``auto-<reason>-<utc-stamp>.tar.gz`` so the UI can
    label it and rotation only trims auto-snapshots — never user backups.

    Returns None when data_dir does not exist yet (nothing to snapshot, e.g.
    pre-install on a server that hasn't created its data dir). Trims to
    ``keep`` most recent safety snapshots per server.
    """
    if not data_dir.exists():
        return None
    safe_reason = reason.replace("-", "_").replace("/", "_").replace(" ", "_")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{SAFETY_PREFIX}{safe_reason}-{stamp}.tar.gz"
    target = _server_root(server_id, root) / name
    with tarfile.open(target, "w:gz") as tf:
        tf.add(data_dir, arcname=".")
    snap = Backup(
        server_id=server_id, name=name, path=target, size=target.stat().st_size,
    )
    _trim_safety(server_id, root=root, keep=keep)
    return snap


def _trim_safety(server_id: str, *, root: Path | None, keep: int) -> None:
    root = _root(root)
    safeties = [b for b in list_for(server_id, root=root) if b.is_safety]
    # list_for is sorted newest-first; keep the first `keep`, prune the rest.
    for b in safeties[keep:]:
        b.path.unlink(missing_ok=True)


def list_for(server_id: str, *, root: Path | None = None) -> list[Backup]:
    root = _root(root)
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
    root: Path | None = None,
) -> None:
    root = _root(root)
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
    *, server_id: str, name: str, root: Path | None = None,
) -> None:
    root = _root(root)
    target = _server_root(server_id, root) / name
    target.unlink(missing_ok=True)
