"""server.properties read/write.

Same flat `key=value` format on Java and Bedrock with different key sets;
we don't try to validate values (Minecraft does that at boot). Comment
lines (`#`) and blank lines are preserved on write.
"""
from __future__ import annotations

from pathlib import Path


def path_for(data_dir: Path) -> Path:
    return data_dir / "server.properties"


def read(data_dir: Path) -> dict[str, str]:
    p = path_for(data_dir)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write(data_dir: Path, updates: dict[str, str]) -> None:
    """Update only the keys present in `updates`; preserve everything else
    including comments and ordering."""
    p = path_for(data_dir)
    existing = p.read_text().splitlines() if p.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        k, _, _ = stripped.partition("=")
        k = k.strip()
        if k in updates:
            new_lines.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            new_lines.append(line)
    # Append any keys that weren't in the file
    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")
    p.write_text("\n".join(new_lines) + "\n")
