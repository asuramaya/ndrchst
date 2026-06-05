#!/usr/bin/env python3
"""Process raw Minecraft textures into the End-themed asset set still in use:
the web favicon and the client's Tkinter header (banner + brand glyph). Output
is gitignored (we don't commit game binaries to the OSS repo); it's staged to
the box + R2 at deploy time and bundled into the client.

PROVENANCE of the raw inputs (not committed; re-extract to regenerate) — from
the vanilla 1.21.1 client jar (Mojang piston-data CDN):
  - end_sky  — tileable End starfield, source of the panning header banner.
  - ender_eye — source of the favicon + the client brand glyph.

Usage: python3 scripts/build_game_assets.py [RAW_DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ndrchst-assets/raw")
OUT = ROOT / "src/ndrchst/web/static/game"
# The client (Tkinter) bundles these for its themed, offline header.
CLIENT_ASSETS = ROOT / "client/src/ndrchst_client/assets"


def _load(name: str) -> Image.Image:
    return Image.open(RAW / f"{name}.png").convert("RGBA")


def _crisp(im: Image.Image, size: int) -> Image.Image:
    """Nearest-neighbor upscale so pixel art stays sharp, not blurry."""
    return im.resize((size, size), Image.NEAREST)


def build_banner(dst: Path) -> None:
    """A seamless panning End-starfield strip for the client's header. Tkinter's
    PhotoImage cycles GIF frames natively, so the client gets a live backdrop
    with no extra deps."""
    sky = _load("end_sky")  # 128x128 tileable
    tile = sky.width
    w, h, scale = 200, 22, 3  # pan exactly one tile -> seamless; ~600x66 strip
    strip_w = w + tile
    strip = Image.new("RGBA", (strip_w, h))
    for x in range(0, strip_w, tile):
        strip.paste(sky.crop((0, 0, tile, h)), (x, 0))
    step = max(1, tile // 16)
    out = []
    for off in range(0, tile, step):
        frame = strip.crop((off, 0, off + w, h)).resize(
            (w * scale, h * scale), Image.NEAREST).convert("P")
        out.append(frame)
    out[0].save(dst, save_all=True, append_images=out[1:],
                duration=90, loop=0, disposal=1)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Favicon from the ender eye — the Worker serves it at /favicon.ico, which
    # browsers auto-request.
    _crisp(_load("ender_eye"), 32).save(OUT / "favicon.png")
    # Client UI assets (banner GIF + brand glyph) for the Tkinter header.
    CLIENT_ASSETS.mkdir(parents=True, exist_ok=True)
    build_banner(CLIENT_ASSETS / "end_banner.gif")
    _crisp(_load("ender_eye"), 48).save(CLIENT_ASSETS / "brand.png")
    print(f"built favicon -> {OUT}\nbuilt 2 client assets -> {CLIENT_ASSETS}")


if __name__ == "__main__":
    main()
