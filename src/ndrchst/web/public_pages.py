"""The public website — a minimal, static landing + play page.

Rendered by the admin plane and published to R2 (see runtime/publish.py); the
Cloudflare Worker serves the resulting ``index.html`` / ``play.html`` straight
from the bucket. There is no dynamic public app — these are plain static pages.

`render_play` takes the server catalog (the same dicts publish.py writes to
``servers.json``) so players can see what's running and grab the per-server
client. `downloads_base` is the R2 base that holds the standalone launcher
binaries + ``latest.json`` (env ``NDRCHST_CLIENT_DOWNLOADS_BASE``); when unset
the pages fall back to the per-server ``client.zip``.
"""
from __future__ import annotations

# End-void palette (ender green + void purple).
_CSS = """
:root{--bg:#0a0613;--panel:#161029;--fg:#f4f0ff;--muted:#a99fc7;
--accent:#14f195;--purple:#9945ff;--ink:#04130c;--line:#2a2150}
*{box-sizing:border-box}html{height:100%}
body{margin:0;min-height:100%;background:var(--bg);color:var(--fg);
font-family:'Space Grotesk',system-ui,sans-serif;line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:880px;margin:0 auto;padding:3rem 1.25rem}
header.site{display:flex;align-items:center;gap:.75rem;margin-bottom:2.5rem}
header.site b{font-size:1.25rem;letter-spacing:.04em}
header.site nav{margin-left:auto;display:flex;gap:1.25rem}
h1{font-size:2.6rem;margin:.2em 0;line-height:1.1}
.lede{color:var(--muted);font-size:1.15rem;max-width:46ch}
.cta{display:inline-block;background:var(--accent);color:var(--ink);
font-weight:700;padding:.7rem 1.3rem;border-radius:.6rem;margin:1.5rem .5rem 0 0}
.cta.alt{background:transparent;color:var(--fg);border:1px solid var(--line)}
.muted{color:var(--muted)}
.steps{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin:2.5rem 0}
.step{background:var(--panel);border:1px solid var(--line);border-radius:.7rem;padding:1.1rem}
.step .n{color:var(--purple);font-weight:700;font-size:.8rem;letter-spacing:.1em}
.cards{display:grid;gap:1rem;margin:1.5rem 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:.7rem;
padding:1.1rem 1.25rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.card h3{margin:0;font-size:1.15rem}
.card .meta{color:var(--muted);font-size:.9rem}
.badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;border:1px solid var(--line);color:var(--muted)}
.badge.up{color:var(--accent);border-color:var(--accent)}
.card .grab{margin-left:auto}
footer{color:var(--muted);font-size:.85rem;margin-top:3rem;border-top:1px solid var(--line);padding-top:1.5rem}
code{background:#0006;padding:.1rem .35rem;border-radius:.3rem;font-family:'JetBrains Mono',monospace}
"""

_BINARIES = (
    ("Windows", "ndrchst-client-windows-x86_64.exe"),
    ("macOS", "ndrchst-client-macos-arm64"),
    ("Linux", "ndrchst-client-linux-x86_64"),
)


def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{title}</title>"
        '<link rel=preconnect href="https://fonts.googleapis.com">'
        '<link rel=stylesheet href='
        '"https://fonts.googleapis.com/css2?family=JetBrains+Mono&family=Space+Grotesk:wght@400;600;700&display=swap">'
        f"<style>{_CSS}</style></head><body><div class=wrap>"
        '<header class=site><b>ndrchst</b>'
        '<nav><a href="/">Home</a><a href="/play">Servers</a></nav></header>'
        f"{body}"
        "<footer>ndrchst — modded Minecraft servers + a sync-on-launch desktop "
        "client. The client mirrors each server's mod set and connects you in one "
        "click.</footer>"
        "</div></body></html>"
    )


def _download_buttons(downloads_base: str) -> str:
    base = downloads_base.rstrip("/") if downloads_base else ""
    if not base:
        return ('<p class=muted>Pick a server below and download its client '
                "bundle to play.</p>")
    btns = "".join(
        f'<a class="cta{"" if i == 0 else " alt"}" href="{base}/{f}">{label}</a>'
        for i, (label, f) in enumerate(_BINARIES)
    )
    return btns


def render_landing(*, downloads_base: str = "") -> str:
    body = (
        "<h1>Play modded Minecraft<br>without the setup.</h1>"
        "<p class=lede>Download the ndrchst client once. It installs the right "
        "Minecraft + mods for a server, keeps them in sync, and drops you "
        "straight in.</p>"
        f"<div>{_download_buttons(downloads_base)}</div>"
        '<div class=steps>'
        '<div class=step><div class=n>STEP 01</div><h3>Download the client</h3>'
        "<p class=muted>One small launcher for your OS.</p></div>"
        '<div class=step><div class=n>STEP 02</div><h3>Pick a server</h3>'
        '<p class=muted>Browse the <a href="/play">server list</a> and grab its client.</p></div>'
        '<div class=step><div class=n>STEP 03</div><h3>Press Play</h3>'
        "<p class=muted>Mods sync from the server; you connect automatically.</p></div>"
        "</div>"
        '<p><a class="cta alt" href="/play">See the servers →</a></p>'
    )
    return _shell("ndrchst — modded Minecraft, no setup", body)


def _status_class(status: str) -> str:
    return "up" if (status or "").lower() in ("running", "started") else ""


def render_play(servers: list[dict], *, downloads_base: str = "") -> str:
    """Server catalog. `servers` are the dicts publish.py writes to servers.json:
    {id, name, version, status, cross_play, client_url, …}."""
    if servers:
        cards = []
        for s in servers:
            badge_kind = "modded" if not s.get("cross_play") else "cross-play"
            status = s.get("status") or "stopped"
            client_url = s.get("client_url") or f"/client/{s.get('id', '')}/client.zip"
            cards.append(
                '<div class=card>'
                f"<div><h3>{s.get('name', 'server')}</h3>"
                f"<div class=meta>Minecraft {s.get('version', '?')} · {badge_kind}</div></div>"
                f'<span class="badge {_status_class(status)}">{status}</span>'
                f'<a class="cta grab" href="{client_url}">Download client</a>'
                "</div>"
            )
        server_html = '<div class=cards>' + "".join(cards) + "</div>"
    else:
        server_html = "<p class=muted>No servers are published yet.</p>"

    body = (
        "<h1>Servers</h1>"
        "<p class=lede>Download a server's client bundle, or grab the standalone "
        "launcher and pin a server from in-app.</p>"
        f"<div>{_download_buttons(downloads_base)}</div>"
        f"{server_html}"
        "<p class=muted>Each client is an offline launcher pinned to its server. "
        "It mirrors the server's mods on every launch, so you always match.</p>"
    )
    return _shell("ndrchst — servers", body)
