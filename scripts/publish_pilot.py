#!/usr/bin/env python3
"""Build + publish the pilot binary to R2 — no GitHub Actions required.

PyInstaller can't cross-compile, so run this ON each OS you want a binary
for. It builds the pilot for the *current* OS, uploads it to R2, and
merges this OS's entry into ``<prefix>/latest.json`` so the client
auto-updater picks it up. Run it on Linux for the Linux build, on a Mac
for macOS, on Windows for Windows — each updates its own slot.

Reuses the same SigV4 uploader the admin uses (src/ndrchst/runtime/r2.py),
so there's no boto3 / aws-cli dependency.

Env (an R2 token with Object Read & Write on the bucket):
  NDRCHST_R2_ACCOUNT_ID
  NDRCHST_R2_ACCESS_KEY_ID
  NDRCHST_R2_SECRET_ACCESS_KEY
  NDRCHST_R2_BUCKET
  NDRCHST_PILOT_DOWNLOADS_BASE   public base to read the current manifest
                                 (e.g. https://dl.ndrchst.com/pilot)

Usage:
  python scripts/publish_pilot.py --version 0.1.1
  python scripts/publish_pilot.py --version 0.1.1 --no-build   # publish an existing dist/ binary
  python scripts/publish_pilot.py --version 0.1.1 --prefix pilot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PILOT_DIR = REPO / "pilot-client"
sys.path.insert(0, str(REPO / "src"))

from ndrchst.runtime import r2  # noqa: E402

# platform_key → (built binary name in dist/, published asset name)
_TARGETS = {
    "linux-x86_64": ("ndrchst-pilot", "ndrchst-pilot-linux-x86_64"),
    "windows-x86_64": ("ndrchst-pilot.exe", "ndrchst-pilot-windows-x86_64.exe"),
    "macos-arm64": ("ndrchst-pilot", "ndrchst-pilot-macos-arm64"),
    "macos-x86_64": ("ndrchst-pilot", "ndrchst-pilot-macos-x86_64"),
}


def platform_key() -> str:
    sysname = platform.system()
    machine = platform.machine().lower()
    if sysname == "Windows":
        return "windows-x86_64"
    if sysname == "Darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    return f"linux-{arch}"


def merge_manifest(existing: dict | None, *, version: str, key: str,
                   asset: str, sha256: str, notes: str | None = None) -> dict:
    """Set the overall version + this platform's asset entry, preserving
    other platforms' entries from the existing manifest."""
    out = {"version": version, "notes": notes or f"ndrchst pilot {version}", "assets": {}}
    if existing and isinstance(existing.get("assets"), dict):
        out["assets"] = dict(existing["assets"])
    out["assets"][key] = {"file": asset, "sha256": sha256}
    return out


def _fetch_manifest(public_base: str) -> dict | None:
    if not public_base:
        return None
    url = public_base.rstrip("/") + "/latest.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp_version(version: str) -> None:
    init = PILOT_DIR / "src" / "ndrchst_pilot" / "__init__.py"
    import re
    init.write_text(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', init.read_text()))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build + publish the pilot to R2")
    ap.add_argument("--version", required=True, help="release version, e.g. 0.1.1")
    ap.add_argument("--prefix", default="pilot", help="R2 key prefix (default: pilot)")
    ap.add_argument("--no-build", action="store_true", help="publish the existing dist/ binary")
    ap.add_argument("--public-base", default=os.environ.get("NDRCHST_PILOT_DOWNLOADS_BASE", ""),
                    help="public base URL to read the current latest.json")
    args = ap.parse_args()

    cfg_env = r2.config_from_env()
    if cfg_env is None:
        print("error: R2 not configured — set NDRCHST_R2_ACCOUNT_ID / "
              "NDRCHST_R2_ACCESS_KEY_ID / NDRCHST_R2_SECRET_ACCESS_KEY / "
              "NDRCHST_R2_BUCKET", file=sys.stderr)
        return 2
    # Keys carry the prefix explicitly below, so neutralise any env prefix.
    cfg = r2.R2Config(cfg_env.account_id, cfg_env.access_key_id,
                      cfg_env.secret_access_key, cfg_env.bucket, prefix="")

    key = platform_key()
    if key not in _TARGETS:
        print(f"error: unsupported platform {key}", file=sys.stderr)
        return 2
    bin_name, asset = _TARGETS[key]

    if not args.no_build:
        _stamp_version(args.version)
        print(f"Building pilot {args.version} for {key}…")
        subprocess.run(["pyinstaller", "ndrchst-pilot.spec"], cwd=PILOT_DIR, check=True)

    built = PILOT_DIR / "dist" / bin_name
    if not built.exists():
        print(f"error: built binary not found at {built} "
              f"(build first, or drop it there)", file=sys.stderr)
        return 1

    digest = _sha256(built)
    print(f"Uploading {asset} ({built.stat().st_size/1e6:.1f} MB, sha256 {digest[:12]}…)")
    r2.put_object(cfg, f"{args.prefix}/{asset}", built.read_bytes(),
                  content_type="application/octet-stream",
                  cache_control="public, max-age=31536000, immutable")

    manifest = merge_manifest(_fetch_manifest(args.public_base), version=args.version,
                              key=key, asset=asset, sha256=digest)
    r2.put_object(cfg, f"{args.prefix}/latest.json",
                  json.dumps(manifest, indent=2).encode("utf-8"),
                  content_type="application/json", cache_control="no-cache")
    print(f"Published {asset} + latest.json (version {args.version}, "
          f"assets: {', '.join(sorted(manifest['assets']))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
