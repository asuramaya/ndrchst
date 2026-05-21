#!/usr/bin/env python3
"""Purge orphaned objects from the ndrchst R2 bucket — dry-run by default.

The box hosts almost nothing: the modpack streams from CurseForge's CDN, so R2
should only ever hold the static pages, per-server json/manifest/client.zip, the
game/* assets, and the SMALL set of origin-served substitution jars. Anything
else is an orphan — usually a dead key prefix left by a rename (the old
`pilot/...` tree) or jars stranded by an aborted full-pack upload.

This lists everything under a prefix and, with --apply, deletes it. By default
it only PRINTS what it would delete so you can eyeball the blast radius first.
Use --keep / --keep-index to protect keys a server still serves (so purging
client/<sid>/mods/ won't nuke the live substitution set or its index).

Reuses the SigV4 list/delete in src/ndrchst/runtime/r2.py — no aws-cli / boto3.

Env (the same R2 token used for publishing): NDRCHST_R2_ACCOUNT_ID /
_ACCESS_KEY_ID / _SECRET_ACCESS_KEY / _BUCKET. On the box:
    set -a; . ~/.config/ndrchst/r2.env; set +a

Examples:
    # The dead pre-rename tree — look, then delete:
    python scripts/r2_purge.py --prefix pilot/
    python scripts/r2_purge.py --prefix pilot/ --apply

    # Orphaned client jars, keeping the index + the origin-served set:
    python scripts/r2_purge.py --prefix client/<sid>/mods/ \
        --keep index.json --keep-index ~/.ndrchst/servers/<sid>/mods-index.json
    # ...then add --apply once the dry run looks right.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ndrchst.runtime import r2  # noqa: E402


def _keep_from_index(path: str) -> set[str]:
    """Basenames of jars a server still serves from ORIGIN (from_cdn:false) —
    the only mod keys under client/<sid>/mods/ that must survive a purge."""
    data = json.loads(Path(path).read_text())
    keep: set[str] = set()
    for e in data.get("mods", []):
        if e.get("from_cdn") is False and e.get("filename"):
            keep.add(e["filename"])
    return keep


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Purge orphaned R2 objects under a prefix (dry-run by default).")
    ap.add_argument("--prefix", required=True,
                    help="RAW bucket key prefix to scan, e.g. 'pilot/'")
    ap.add_argument("--keep", action="append", default=[],
                    help="exact key OR basename to preserve (repeatable)")
    ap.add_argument("--keep-index",
                    help="a mods-index.json; preserve jars it serves from origin")
    ap.add_argument("--apply", action="store_true",
                    help="actually DELETE the matched keys (default: just print)")
    args = ap.parse_args()

    cfg_env = r2.config_from_env()
    if cfg_env is None:
        print("error: R2 not configured — need NDRCHST_R2_* in the environment "
              "(on the box: set -a; . ~/.config/ndrchst/r2.env)", file=sys.stderr)
        return 2
    # Operate on RAW bucket keys: publish writes full keys (client/…, pilot/…)
    # with an empty env prefix, so neutralise any configured prefix here too.
    cfg = r2.R2Config(cfg_env.account_id, cfg_env.access_key_id,
                      cfg_env.secret_access_key, cfg_env.bucket, prefix="")

    keep = set(args.keep)
    if args.keep_index:
        keep |= _keep_from_index(args.keep_index)

    print(f"Scanning r2://{cfg.bucket}/{args.prefix} …")
    objs = r2.list_objects(cfg, args.prefix)
    if not objs:
        print("nothing found under that prefix — already clean.")
        return 0

    to_delete: list[tuple[str, int]] = []
    kept: list[tuple[str, int]] = []
    for key, size in sorted(objs):
        base = key.rsplit("/", 1)[-1]
        (kept if (key in keep or base in keep) else to_delete).append((key, size))

    for key, size in kept:
        print(f"  KEEP          {key}  ({_human(size)})")
    verb = "DELETE" if args.apply else "WOULD DELETE"
    for key, size in to_delete:
        print(f"  {verb}  {key}  ({_human(size)})")

    del_bytes = sum(s for _, s in to_delete)
    keep_bytes = sum(s for _, s in kept)
    print(f"\n{len(objs)} object(s), {_human(del_bytes + keep_bytes)} total — "
          f"keep {len(kept)} ({_human(keep_bytes)}), "
          f"{'deleting' if args.apply else 'would delete'} "
          f"{len(to_delete)} ({_human(del_bytes)}).")

    if not args.apply:
        print("\nDry run — re-run with --apply to delete.")
        return 0
    if not to_delete:
        return 0

    print("\nDeleting…")
    import httpx
    with httpx.Client(timeout=60.0) as client:
        for i, (key, _size) in enumerate(to_delete, 1):
            r2.delete_object(cfg, key, client=client)
            if i % 25 == 0 or i == len(to_delete):
                print(f"  {i}/{len(to_delete)} deleted")
    print(f"Done. Deleted {len(to_delete)} object(s), reclaimed {_human(del_bytes)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
