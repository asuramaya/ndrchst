#!/usr/bin/env python3
"""Process raw Minecraft/ATM10 textures into the End-themed web + client asset
set. Output is gitignored (we don't commit game binaries to the OSS repo); it's
staged to the box + R2 at deploy time and bundled into the pilot.

PROVENANCE of the raw inputs (not committed; re-extract to regenerate):
  - Vanilla 1.21.1 client jar (Mojang piston-data CDN): end_sky, end_stone,
    purpur_*, dragon_egg, ancient_debris_side, ender_eye, ender_pearl,
    end_crystal, chorus_fruit + reward icons (diamond, gold_ingot,
    experience_bottle, netherite_scrap/ingot, nether_star).
  - ATM10 mod jars on the box: mysticalagriculture essences (inferium,
    prudentium, tertium, imperium, supremium), allthemodium ingots
    (allthemodium, vibranium, unobtainium).

Usage: python3 scripts/build_game_assets.py [RAW_DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ndrchst-assets/raw")
OUT = ROOT / "src/ndrchst/web/static/game"
# The pilot (Tkinter) bundles these for its themed, offline header.
PILOT_ASSETS = ROOT / "pilot-client/src/ndrchst_pilot/assets"

# Reward item sprites surfaced on /ranks tier cards (filename in RAW == out name).
ITEMS = [
    "diamond", "gold_ingot", "experience_bottle", "netherite_scrap",
    "netherite_ingot", "ancient_debris_side", "nether_star",
    "inferium_essence", "prudentium_essence", "tertium_essence",
    "imperium_essence", "supremium_essence",
    "allthemodium_ingot", "vibranium_ingot", "unobtainium_ingot",
]
# Decorative End textures (crisp upscale).
DECOR = ["ender_eye", "ender_pearl", "end_crystal", "purpur_block",
         "end_stone", "dragon_egg", "chorus_fruit"]


def _load(name: str) -> Image.Image:
    return Image.open(RAW / f"{name}.png").convert("RGBA")


def _crisp(im: Image.Image, size: int) -> Image.Image:
    """Nearest-neighbor upscale so pixel art stays sharp, not blurry."""
    return im.resize((size, size), Image.NEAREST)


def build_banner(dst: Path) -> None:
    """A seamless panning End-starfield strip for the pilot's header. Tkinter's
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
    (OUT / "items").mkdir(parents=True, exist_ok=True)
    (OUT / "decor").mkdir(parents=True, exist_ok=True)
    for name in ITEMS:
        _crisp(_load(name), 64).save(OUT / "items" / f"{name}.png")
    for name in DECOR:
        _crisp(_load(name), 64).save(OUT / "decor" / f"{name}.png")
    # Void background tile (kept native; CSS scales it pixelated).
    _load("end_sky").save(OUT / "decor" / "end_sky.png")
    # Brand glyph + favicon from the ender eye (teal-green = our accent).
    _crisp(_load("ender_eye"), 128).save(OUT / "decor" / "brand.png")
    _crisp(_load("ender_eye"), 32).save(OUT / "favicon.png")
    build_banner(OUT / "decor" / "end_banner.gif")
    # Pilot UI assets (banner GIF + brand glyph) for the Tkinter header.
    PILOT_ASSETS.mkdir(parents=True, exist_ok=True)
    build_banner(PILOT_ASSETS / "end_banner.gif")
    _crisp(_load("ender_eye"), 48).save(PILOT_ASSETS / "brand.png")
    n = sum(1 for _ in OUT.rglob("*.*"))
    print(f"built {n} web assets -> {OUT}\nbuilt 2 pilot assets -> {PILOT_ASSETS}")


if __name__ == "__main__":
    main()
