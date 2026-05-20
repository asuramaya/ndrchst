"""Self-contained HTML for the public surface (www landing + play page).

The public app (public.py) deliberately ships no Jinja template dir so it
stays bind-agnostic — these pages are plain Python string builders sharing
one dark shell + embedded CSS that mirrors the admin design tokens.

Two pages:
  - render_landing(): the marketing front page (what ndrchst is).
  - render_play():    the player page (server list + how to run the pilot).
"""
from __future__ import annotations

import html

# Per-OS pilot binary asset names produced by .github/workflows/build-pilot.yml.
# Joined with NDRCHST_PILOT_DOWNLOADS_BASE when set so the play page can offer
# direct downloads; falls back to the per-server pilot.zip otherwise.
PILOT_ASSETS = [
    ("Windows", "ndrchst-pilot-windows-x86_64.exe"),
    ("macOS (Apple Silicon)", "ndrchst-pilot-macos-arm64"),
    ("Linux (x86_64)", "ndrchst-pilot-linux-x86_64"),
]

_CSS = """
:root{
  --bg:#09090b;--bg2:#18181b;--bg3:#27272a;--fg:#fafafa;--fg2:#a1a1aa;
  --muted:#71717a;--accent:#22c55e;--accent2:#16a34a;--border:#27272a;
  --radius:14px;--radius-sm:9px;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);
  font-family:'Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{color:var(--accent2)}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace}
.wrap{max-width:960px;margin:0 auto;padding:0 1.25rem}
nav.top{display:flex;align-items:center;justify-content:space-between;
  padding:1.1rem 0;border-bottom:1px solid var(--border)}
nav.top .brand{font-weight:700;letter-spacing:.02em;font-size:1.1rem;color:var(--fg)}
nav.top .links a{color:var(--fg2);margin-left:1.25rem;font-size:.92rem}
nav.top .links a.active,nav.top .links a:hover{color:var(--fg)}
.hero{padding:4.5rem 0 3rem}
.hero h1{font-size:2.7rem;line-height:1.08;letter-spacing:-.02em;margin:0 0 1rem;font-weight:700}
.hero p.lede{font-size:1.15rem;color:var(--fg2);max-width:38rem;margin:0 0 1.8rem;line-height:1.5}
.cta{display:inline-flex;align-items:center;gap:.5rem;background:var(--accent);
  color:#052e16;font-weight:600;padding:.7rem 1.3rem;border-radius:var(--radius-sm);
  font-size:1rem}
.cta:hover{background:var(--accent2);color:#052e16}
.cta.ghost{background:transparent;color:var(--fg2);border:1px solid var(--border);margin-left:.6rem}
.cta.ghost:hover{color:var(--fg);border-color:var(--bg3)}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:1rem;padding:1rem 0 3.5rem}
.feature{background:linear-gradient(135deg,#18181b,#1f1f23);border:1px solid var(--border);
  border-radius:var(--radius);padding:1.25rem}
.feature h3{margin:0 0 .5rem;font-size:1.02rem}
.feature p{margin:0;color:var(--fg2);font-size:.9rem;line-height:1.5}
.section{padding:2.5rem 0;border-top:1px solid var(--border)}
.section h2{font-size:1.4rem;margin:0 0 1.2rem;letter-spacing:-.01em}
.server{display:flex;align-items:center;justify-content:space-between;gap:1rem;
  background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);
  padding:1rem 1.2rem;margin-bottom:.75rem;flex-wrap:wrap}
.server .name{font-weight:600;font-size:1.05rem}
.server .meta{color:var(--muted);font-size:.82rem;margin-top:.2rem}
.server .right{display:flex;align-items:center;gap:.6rem}
.dot{display:inline-flex;align-items:center;gap:.35rem;font-size:.74rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted)}
.dot::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--muted)}
.dot.running{color:var(--accent)}.dot.running::before{background:var(--accent);box-shadow:0 0 6px rgba(34,197,94,.4)}
.dot.starting,.dot.stopping{color:#eab308}.dot.starting::before,.dot.stopping::before{background:#eab308}
.btn{display:inline-block;padding:.5rem .9rem;border-radius:var(--radius-sm);font-size:.88rem;
  background:var(--accent);color:#052e16;font-weight:600}
.btn:hover{background:var(--accent2);color:#052e16}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--fg2);font-weight:400}
.btn.ghost:hover{color:var(--fg);border-color:var(--bg3)}
.os-tabs{display:flex;gap:.4rem;margin-bottom:1rem;flex-wrap:wrap}
.os-tab{padding:.45rem .9rem;border:1px solid var(--border);border-radius:999px;
  font-size:.85rem;color:var(--fg2);cursor:pointer;background:var(--bg2)}
.os-tab.active{color:var(--accent);border-color:rgba(34,197,94,.4)}
.os-panel{display:none;background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--radius);padding:1.1rem 1.3rem}
.os-panel.active{display:block}
.os-panel ol{margin:.3rem 0 0;padding-left:1.2rem;color:var(--fg2);line-height:1.7;font-size:.92rem}
.os-panel code{background:var(--bg3);padding:.1rem .35rem;border-radius:4px;font-size:.85em}
.empty{color:var(--muted);text-align:center;padding:2.5rem 1rem;border:1px dashed var(--border);border-radius:var(--radius)}
footer{padding:2.5rem 0 3rem;color:var(--muted);font-size:.82rem;border-top:1px solid var(--border)}
"""

_HEAD = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="wrap">"""


def _shell(title: str, body: str, *, active: str) -> str:
    def cls(name: str) -> str:
        return ' class="active"' if name == active else ""
    nav = (
        '<nav class="top"><a class="brand" href="/">ndrchst</a>'
        '<div class="links">'
        f'<a href="/"{cls("home")}>Home</a>'
        f'<a href="/play"{cls("play")}>Play</a>'
        "</div></nav>"
    )
    return _HEAD.format(title=html.escape(title), css=_CSS) + nav + body + "</div></body></html>"


def render_landing(*, play_url: str = "/play") -> str:
    play = html.escape(play_url, quote=True)
    body = f"""
<section class="hero">
  <h1>Your own Minecraft, top to bottom.</h1>
  <p class="lede">ndrchst is a vertically-integrated Minecraft stack — a custom launcher,
     your own private edge, and a server you control. One click puts players in your world
     with the exact modpack you run, no manual setup.</p>
  <a class="cta" href="{play}">Play now →</a>
  <a class="cta ghost" href="#how">How it works</a>
</section>

<div class="features">
  <div class="feature">
    <h3>One pack, always in sync</h3>
    <p>The server is the source of truth. Clients mirror its mod set on launch — update
       a mod once and every player gets it next time they play.</p>
  </div>
  <div class="feature">
    <h3>Your edge, not your address</h3>
    <p>Players connect through your domain at the network edge. The server's real address
       never leaves your machine — no port-forwarding, no exposed IP.</p>
  </div>
  <div class="feature">
    <h3>Bring your own machine</h3>
    <p>A small cross-platform launcher for Windows, macOS, and Linux installs the modpack,
       tunes the JVM, and joins — so players don't fight with mod folders.</p>
  </div>
</div>

<section class="section" id="how">
  <h2>How it works</h2>
  <div class="features" style="padding-bottom:0">
    <div class="feature"><h3>1 · Download the launcher</h3>
      <p>Grab the pilot for your OS from the <a href="/play">Play</a> page.</p></div>
    <div class="feature"><h3>2 · Press Play</h3>
      <p>It pulls the modpack from a global CDN, mirrors the server's mod set, and tunes memory.</p></div>
    <div class="feature"><h3>3 · You're in</h3>
      <p>It connects through your edge straight into the world. No accounts to wire up.</p></div>
  </div>
</section>

<footer>ndrchst — a self-hosted, vertically-integrated Minecraft stack.</footer>
"""
    return _shell("ndrchst — your own Minecraft stack", body, active="home")


def _status_class(status: str) -> str:
    return status if status in ("running", "starting", "stopping") else ""


def render_play(servers: list[dict], *, downloads_base: str = "") -> str:
    """servers: list of {name, version, port, status, cross_play,
    bedrock_bridge_port, pilot_url, config_url}."""
    rows = []
    if not servers:
        rows.append('<div class="empty">No servers are online right now.</div>')
    for s in servers:
        cross = (
            f' · bedrock {s["bedrock_bridge_port"]}/udp'
            if s.get("cross_play") and s.get("bedrock_bridge_port") else ""
        )
        st = _status_class(s.get("status", ""))
        rows.append(
            '<div class="server">'
            '<div>'
            f'<div class="name">{html.escape(s["name"])}</div>'
            f'<div class="meta mono">Minecraft {html.escape(str(s["version"]))} · port {s["port"]}{cross}</div>'
            '</div>'
            '<div class="right">'
            f'<span class="dot {st}">{html.escape(s.get("status",""))}</span>'
            f'<a class="btn" href="{s["pilot_url"]}">Download pilot</a>'
            f'<a class="btn ghost" href="{s["config_url"]}">config</a>'
            '</div></div>'
        )

    # Standalone per-OS binaries (only when the operator has published them).
    binaries_html = ""
    if downloads_base:
        base = downloads_base.rstrip("/")
        links = "".join(
            f'<li>{html.escape(label)}: <a href="{base}/{html.escape(fname)}">{html.escape(fname)}</a></li>'
            for label, fname in PILOT_ASSETS
        )
        binaries_html = (
            '<p style="color:var(--fg2);font-size:.92rem">Prefer a standalone launcher? '
            "Download the binary for your OS and drop a server's "
            "<code>config.json</code> next to it:</p>"
            f'<ul class="mono" style="color:var(--fg2);line-height:1.9;font-size:.88rem">{links}</ul>'
        )

    body = f"""
<section class="hero" style="padding:3rem 0 1.5rem">
  <h1 style="font-size:2.1rem">Play on ndrchst</h1>
  <p class="lede">Pick a server, download its pilot, and press Play. The pilot installs the
     modpack and joins for you.</p>
</section>

<section class="section" style="border-top:none;padding-top:0">
  <h2>Servers</h2>
  {''.join(rows)}
</section>

<section class="section">
  <h2>How to run the pilot</h2>
  <div class="os-tabs">
    <div class="os-tab" data-os="win">Windows</div>
    <div class="os-tab" data-os="mac">macOS</div>
    <div class="os-tab" data-os="linux">Linux</div>
  </div>
  <div class="os-panel" data-os="win">
    <ol>
      <li>Download a server's pilot above and unzip it.</li>
      <li>Run <code>ndrchst-pilot.exe</code> (or <code>launch.bat</code>).</li>
      <li>Enter your name, pick your RAM, and press <strong>Play</strong>.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="mac">
    <ol>
      <li>Download a server's pilot above and unzip it.</li>
      <li>Run the launcher (you may need to allow it in System Settings → Privacy &amp; Security).</li>
      <li>Enter your name, pick your RAM, and press <strong>Play</strong>.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="linux">
    <ol>
      <li>Download a server's pilot above and unzip it: <code>unzip pilot.zip &amp;&amp; cd pilot</code></li>
      <li>Run <code>./launch.sh</code>.</li>
      <li>Enter your name, pick your RAM (and Graphics, on hybrid laptops), and press <strong>Play</strong>.</li>
    </ol>
  </div>
  {binaries_html}
</section>

<footer>The pilot is an offline launcher pinned to each server. It mirrors the server's
  mod set from a CDN, so first launch downloads the pack once.</footer>

<script>
(function(){{
  var def = navigator.platform.indexOf('Win')>=0?'win':(navigator.platform.indexOf('Mac')>=0?'mac':'linux');
  function sel(os){{
    document.querySelectorAll('.os-tab').forEach(function(t){{t.classList.toggle('active',t.dataset.os===os);}});
    document.querySelectorAll('.os-panel').forEach(function(p){{p.classList.toggle('active',p.dataset.os===os);}});
  }}
  document.querySelectorAll('.os-tab').forEach(function(t){{t.addEventListener('click',function(){{sel(t.dataset.os);}});}});
  sel(def);
}})();
</script>
"""
    return _shell("ndrchst — play", body, active="play")
