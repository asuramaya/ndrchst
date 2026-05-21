"""Self-contained HTML for the public surface (marketing landing + play page).

The whole surface now lives on one host (play.ndrchst.com): `/` is the
landing, `/play` is the app, and apex/www just 301 here. So every nav link is
a same-host relative path and the wallet session cookie never crosses origins.

The public app (public.py) deliberately ships no Jinja template dir so it
stays bind-agnostic — these pages are plain Python string builders sharing
one dark shell + embedded CSS that mirrors the admin design tokens.

Two pages:
  - render_landing(): the marketing front page (what ndrchst is).
  - render_play():    the player page (server list + how to run the client).
"""
from __future__ import annotations

import html
import json
import os
from functools import lru_cache
from pathlib import Path


def _play_url() -> str:
    """The player/app page. The whole public surface now lives on the single
    play host (apex + www 301 there), so this is a same-host relative path. That
    is the point of the collapse: the wallet session cookie is host-scoped, and
    with one host it can never strand on the wrong origin — no cross-host hop,
    no `.ndrchst.com` cookie-domain hack."""
    return "/play"


def _home_url() -> str:
    """The marketing landing, served at the root of the one play host. Relative
    for the same single-host reason as _play_url."""
    return "/"

# Per-OS client binary asset names produced by .github/workflows/build-client.yml.
# Joined with NDRCHST_CLIENT_DOWNLOADS_BASE when set so the play page can offer
# direct downloads; falls back to the per-server client.zip otherwise.
CLIENT_ASSETS = [
    ("Windows", "ndrchst-client-windows-x86_64.exe"),
    ("macOS (Apple Silicon)", "ndrchst-client-macos-arm64"),
    ("Linux (x86_64)", "ndrchst-client-linux-x86_64"),
]

_CSS = """
:root{
  --bg:#0a0613;--bg2:#161029;--bg3:#241a40;--fg:#f4f0ff;--fg2:#a99fc7;
  --muted:#6f6794;--accent:#14f195;--accent2:#0fbd75;--purple:#9945ff;
  --border:#2a2150;--ink:#04130c;--radius:14px;--radius-sm:9px;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);min-height:100%;overflow-x:hidden;
  font-family:'Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased}
img{max-width:100%}
/* Two-layer End-void parallax from the real end_sky tile. Transform-driven
   (not background-position) so the compositor handles it with no per-frame
   full-screen repaint — animated texture without the weight of a tiled GIF. */
.stars{position:fixed;inset:0;z-index:-2;overflow:hidden;pointer-events:none}
.stars i{position:absolute;inset:-30%;background:url(/game/decor/end_sky.png) repeat;
  image-rendering:pixelated;will-change:transform}
.stars i:nth-child(1){background-size:256px;opacity:.5;animation:drift1 90s linear infinite}
.stars i:nth-child(2){background-size:150px;opacity:.2;animation:drift2 150s linear infinite}
@keyframes drift1{to{transform:translate3d(-256px,-256px,0)}}
@keyframes drift2{to{transform:translate3d(300px,-150px,0)}}
@media(prefers-reduced-motion:reduce){.stars i,.hero-orb,.float{animation:none!important}}
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(120% 80% at 50% -10%, rgba(153,69,255,.22), transparent 60%),
    radial-gradient(90% 60% at 50% 112%, rgba(20,241,149,.12), transparent 55%),
    linear-gradient(180deg, rgba(10,6,19,.4), rgba(10,6,19,.94))}
a{color:var(--accent);text-decoration:none}a:hover{color:#5ffbbd}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace}
.wrap{max-width:960px;margin:0 auto;padding:0 1.25rem;position:relative}
.pixel{image-rendering:pixelated;image-rendering:crisp-edges}
nav.top{display:flex;align-items:center;justify-content:space-between;
  padding:1.1rem 0;border-bottom:1px solid var(--border)}
nav.top .brand{display:inline-flex;align-items:center;gap:.55rem;font-weight:700;
  letter-spacing:.02em;font-size:1.1rem;color:var(--fg)}
nav.top .brand img{width:22px;height:22px;image-rendering:pixelated;
  filter:drop-shadow(0 0 5px rgba(20,241,149,.5))}
nav.top .links{display:flex;align-items:center}
nav.top .links a{color:var(--fg2);margin-left:1.25rem;font-size:.92rem}
nav.top .links a.active,nav.top .links a:hover{color:var(--fg)}
.hero{padding:4.5rem 0 3rem;position:relative}
.hero-orb{position:absolute;top:2.2rem;right:0;width:128px;height:128px;image-rendering:pixelated;
  filter:drop-shadow(0 0 22px rgba(153,69,255,.6));animation:float 6s ease-in-out infinite;
  pointer-events:none;opacity:.95}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-14px)}}
@media(max-width:640px){.hero-orb{display:none}}
.hero h1{font-size:2.7rem;line-height:1.08;letter-spacing:-.02em;margin:0 0 1rem;font-weight:700;
  text-shadow:0 0 30px rgba(153,69,255,.35);overflow-wrap:break-word}
.hero p.lede{font-size:1.15rem;color:var(--fg2);max-width:38rem;margin:0 0 1.8rem;line-height:1.5;overflow-wrap:break-word}
@media(max-width:560px){.hero h1{font-size:2.05rem}.hero p.lede{font-size:1.02rem}}
.cta{display:inline-flex;align-items:center;gap:.5rem;background:var(--accent);
  color:var(--ink);font-weight:600;padding:.7rem 1.3rem;border-radius:var(--radius-sm);
  font-size:1rem;box-shadow:0 0 0 1px rgba(20,241,149,.4),0 8px 28px rgba(20,241,149,.18)}
.cta:hover{background:#2bf7a3;color:var(--ink)}
.cta.ghost{background:transparent;color:var(--fg2);border:1px solid var(--border);
  margin-left:.6rem;box-shadow:none}
.cta.ghost:hover{color:var(--fg);border-color:var(--purple)}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:1rem;padding:1rem 0 3.5rem}
.feature{background:linear-gradient(135deg,rgba(36,26,64,.55),rgba(22,16,41,.55));
  border:1px solid var(--border);border-radius:var(--radius);padding:1.25rem;
  backdrop-filter:blur(3px);transition:border-color .15s,box-shadow .15s}
.feature:hover{border-color:rgba(153,69,255,.55);box-shadow:0 0 24px rgba(153,69,255,.14)}
a.feature{color:var(--fg);text-decoration:none;display:block}
a.feature:hover{color:var(--fg)}
.feature h3{margin:0 0 .5rem;font-size:1.02rem}
.feature p{margin:0;color:var(--fg2);font-size:.9rem;line-height:1.5}
.section{padding:2.5rem 0;border-top:1px solid var(--border)}
.section h2{font-size:1.4rem;margin:0 0 1.2rem;letter-spacing:-.01em}
.server{display:flex;align-items:center;justify-content:space-between;gap:1rem;
  background:linear-gradient(135deg,rgba(36,26,64,.5),rgba(22,16,41,.5));
  border:1px solid var(--border);border-radius:var(--radius);backdrop-filter:blur(3px);
  padding:1rem 1.2rem;margin-bottom:.75rem;flex-wrap:wrap}
.server .name{font-weight:600;font-size:1.05rem}
.server .meta{color:var(--muted);font-size:.82rem;margin-top:.2rem}
.server .right{display:flex;align-items:center;gap:.6rem}
.dot{display:inline-flex;align-items:center;gap:.35rem;font-size:.74rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted)}
.dot::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--muted)}
.dot.running{color:var(--accent)}.dot.running::before{background:var(--accent);box-shadow:0 0 8px rgba(20,241,149,.6)}
.dot.starting,.dot.stopping{color:#eab308}.dot.starting::before,.dot.stopping::before{background:#eab308}
.btn{display:inline-block;padding:.5rem .9rem;border-radius:var(--radius-sm);font-size:.88rem;
  background:var(--accent);color:var(--ink);font-weight:600}
.btn:hover{background:#2bf7a3;color:var(--ink)}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--fg2);font-weight:400}
.btn.ghost:hover{color:var(--fg);border-color:var(--purple)}
.btn:disabled{opacity:.6;cursor:default}
.os-tabs{display:flex;gap:.4rem;margin-bottom:1rem;flex-wrap:wrap}
.os-tab{padding:.45rem .9rem;border:1px solid var(--border);border-radius:999px;
  font-size:.85rem;color:var(--fg2);cursor:pointer;background:rgba(22,16,41,.6)}
.os-tab.active{color:var(--accent);border-color:rgba(20,241,149,.5)}
.os-panel{display:none;background:rgba(22,16,41,.6);border:1px solid var(--border);
  border-radius:var(--radius);padding:1.1rem 1.3rem}
.os-panel.active{display:block}
.os-panel ol{margin:.3rem 0 0;padding-left:1.2rem;color:var(--fg2);line-height:1.7;font-size:.92rem}
.os-panel code{background:var(--bg3);padding:.1rem .35rem;border-radius:4px;font-size:.85em}
.empty{color:var(--muted);text-align:center;padding:2.5rem 1rem;border:1px dashed var(--border);border-radius:var(--radius)}
.eyebrow{display:inline-block;font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);border:1px solid rgba(20,241,149,.4);border-radius:999px;
  padding:.32rem .75rem;margin-bottom:1.1rem}
.feature .num{font-family:'JetBrains Mono',ui-monospace,monospace;color:var(--accent);
  font-size:.8rem;letter-spacing:.05em}
.callout{background:linear-gradient(135deg,rgba(36,26,64,.6),rgba(22,16,41,.6));
  border:1px solid var(--border);border-radius:var(--radius);padding:1.3rem 1.4rem;
  color:var(--fg2);font-size:.95rem;line-height:1.65;backdrop-filter:blur(3px)}
.callout strong{color:var(--fg)}
.disclaimer{color:var(--muted);font-size:.78rem;line-height:1.65;margin-top:1.4rem}
.disclaimer .status{color:var(--fg2)}
.wbtn{font:inherit;cursor:pointer;border:1px solid rgba(20,241,149,.5);background:rgba(20,241,149,.1);
  color:var(--accent);border-radius:999px;padding:.4rem .9rem;font-size:.85rem;font-weight:600}
.wbtn:hover{background:rgba(20,241,149,.2)}
.wchip{display:none;align-items:center;gap:.5rem;border:1px solid var(--border);background:rgba(22,16,41,.7);
  border-radius:999px;padding:.32rem .4rem .32rem .8rem;font-size:.84rem;color:var(--fg)}
.wchip .tier{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--ink);
  background:var(--accent);border-radius:999px;padding:.15rem .5rem;font-weight:700}
.wchip .tier.none{background:var(--bg3);color:var(--fg2)}
.wchip button{font:inherit;cursor:pointer;background:none;border:none;color:var(--muted);font-size:.9rem;padding:0 .2rem}
.rankcard{display:none;background:linear-gradient(135deg,rgba(36,26,64,.6),rgba(22,16,41,.6));
  border:1px solid var(--border);border-radius:var(--radius);padding:1.2rem 1.4rem;margin:0 0 2rem;
  backdrop-filter:blur(3px)}
.rankcard .row{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.rankcard .big{font-size:1.3rem;font-weight:700}
.rankcard .pct{color:var(--fg2);font-size:.9rem}
.pill{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--ink);
  background:var(--accent);border-radius:999px;padding:.18rem .55rem;font-weight:700;white-space:nowrap}
.pill.none{background:var(--bg3);color:var(--fg2)}
.ladder .feature{display:block}
.ladder .feature .lrow{display:flex;align-items:baseline;justify-content:space-between;gap:.6rem}
.ladder .feature h3{margin:0}
.ladder .thr{color:var(--fg2);font-size:.82rem;font-family:'JetBrains Mono',ui-monospace,monospace}
.drops{display:flex;align-items:center;gap:.35rem;margin-top:.7rem;flex-wrap:wrap}
.drops img{width:30px;height:30px;image-rendering:pixelated;
  background:rgba(10,6,19,.5);border:1px solid var(--border);border-radius:6px;padding:2px}
.rankno{font-family:'JetBrains Mono',ui-monospace,monospace;color:var(--muted);font-size:.9rem;
  min-width:2.2rem;text-align:right}
footer{padding:2.5rem 0 3rem;color:var(--muted);font-size:.82rem;border-top:1px solid var(--border)}
footer a{color:var(--fg2)}footer a:hover{color:var(--fg)}
/* Faint End-stone texture on every card so panels read as carved stone, not flat
   divs. Static pseudo-element, ~5% opacity — no measurable cost. */
.feature,.server,.callout,.rankcard,.os-panel{position:relative;overflow:hidden}
.feature::before,.server::before,.callout::before,.rankcard::before,.os-panel::before{
  content:"";position:absolute;inset:0;z-index:0;pointer-events:none;
  background:url(/game/decor/end_stone.png);background-size:128px;
  image-rendering:pixelated;opacity:.05;mix-blend-mode:luminosity}
.feature>*,.server>*,.callout>*,.rankcard>*,.os-panel>*{position:relative;z-index:1}
/* Ambient floating decor — a fixed layer behind the content, populated at load
   from a random subset of the decor pool (see floats JS). GPU transforms only,
   hidden on phones / reduced-motion. */
.floats{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.float{position:absolute;image-rendering:pixelated;pointer-events:none;
  opacity:.5;filter:drop-shadow(0 0 12px rgba(153,69,255,.35))}
.float.spin{animation:float 7s ease-in-out infinite,spin 26s linear infinite}
.float.bob{animation:float 5.5s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:760px){.float{display:none}}
/* Responsive nav — wrap links to a second row before they collide with the
   wallet control, instead of overflowing/overlapping. */
nav.top{flex-wrap:wrap;gap:.6rem 0;row-gap:.6rem}
nav.top .links{flex-wrap:wrap;gap:.4rem .25rem;justify-content:flex-end}
nav.top .links a{margin-left:0;padding:.3rem .7rem;border-radius:999px}
nav.top .links a.active{background:rgba(153,69,255,.16)}
nav.top .links a:hover{background:rgba(255,255,255,.05)}
.wnav{display:flex;align-items:center;gap:.5rem;margin-left:.5rem}
@media(max-width:560px){nav.top{justify-content:center}
  nav.top .links{width:100%;justify-content:center}
  .wnav{margin-left:0;flex-basis:100%;justify-content:center}}
/* Login-state visibility, toggled by body.signed-in / .signed-out from wallet JS. */
.when-in{display:none}body.signed-in .when-in{display:block}
body.signed-in .when-out{display:none}
body.signed-in .wbtn{display:none}
body.signed-in .wchip{display:inline-flex}
.rankcard.when-in{display:none}body.signed-in .rankcard.when-in{display:block}
/* "You" highlight on the ranks ladder. */
.server.me{border-color:rgba(20,241,149,.7);box-shadow:0 0 0 1px rgba(20,241,149,.35),0 0 26px rgba(20,241,149,.12)}
.server.me .youtag{display:inline-flex}
.youtag{display:none;align-items:center;font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--ink);background:var(--accent);border-radius:999px;padding:.1rem .45rem;font-weight:700;margin-left:.5rem}
/* Inline reward-chip strip reused across pages. */
.chips{display:flex;align-items:center;gap:.3rem;flex-wrap:wrap}
.chips img{width:26px;height:26px;image-rendering:pixelated;background:rgba(10,6,19,.5);
  border:1px solid var(--border);border-radius:6px;padding:2px}
/* Small cross-page link row under a section heading. */
.xlink{display:inline-flex;align-items:center;gap:.4rem;font-size:.9rem;color:var(--fg2)}
.xlink:hover{color:var(--fg)}
.hero .deco-row{display:flex;align-items:center;flex-wrap:wrap;gap:.5rem;margin-top:1.6rem;color:var(--muted);font-size:.82rem}
.hero .deco-row img{width:22px;height:22px;image-rendering:pixelated;opacity:.8}
/* CSS hover tooltip — wrap any element in .tip with a data-tip attribute. */
.tip{position:relative;display:inline-flex}
.tip::after{content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + .5rem);
  transform:translateX(-50%) translateY(.25rem);background:var(--bg3);color:var(--fg);
  border:1px solid var(--border);border-radius:7px;padding:.32rem .55rem;font-size:.72rem;
  font-family:'JetBrains Mono',ui-monospace,monospace;white-space:nowrap;opacity:0;
  pointer-events:none;transition:opacity .12s ease,transform .12s ease;z-index:6;
  box-shadow:0 8px 22px rgba(0,0,0,.5)}
.tip:hover::after,.tip:focus-visible::after{opacity:1;transform:translateX(-50%) translateY(0)}
/* Ranks ladder detail. */
.tier-card .thr{white-space:nowrap}
.drop-note{margin-top:.5rem;color:var(--muted);font-size:.74rem;letter-spacing:.01em}
/* Profile / skin. The face is the 8x8 region at (8,8) of a 64px skin, shown at
   72px (scale 9 → 576px sheet, -72px,-72px origin). */
.profile-skin{display:flex;align-items:center;gap:1rem;margin-top:1.1rem;flex-wrap:wrap}
.skin-face{width:72px;height:72px;flex:none;border:1px solid var(--border);border-radius:10px;
  background-color:rgba(10,6,19,.6);background-repeat:no-repeat;
  background-size:576px 576px;background-position:-72px -72px;image-rendering:pixelated;
  position:relative}
.skin-face:not(.has)::after{content:"?";position:absolute;inset:0;display:flex;
  align-items:center;justify-content:center;color:var(--muted);font-size:1.6rem;font-weight:700}
.skin-up{display:flex;flex-direction:column;gap:.4rem}
.skin-up .row{display:flex;gap:.5rem;flex-wrap:wrap}
.skin-up .meta{color:var(--muted);font-size:.78rem}
"""

# Single source of truth for the data the public pages display — DON'T hardcode
# parallel copies, so editing the source syncs the site:
#   - per-tier daily drops come from the datapack loot tables that the server
#     actually rolls (deploy/datapacks/.../loot_table/daily/<tier>.json)
#   - the floating-decor pool comes from whatever sprites live in the assets dir
# Both are read at render time; the box re-renders on each R2 publish.

_STATIC = Path(__file__).resolve().parent / "static" / "game"
_ITEMS_DIR = _STATIC / "items"
_DECOR_DIR = _STATIC / "decor"

# Decor sprites that are textures/brand, not free-floating items — excluded from
# the float pool. Everything else under decor/ is fair game (add a sprite → it
# joins the rotation automatically).
_DECOR_NON_FLOAT = {"brand", "end_sky", "end_stone", "end_banner"}


def _loot_dir() -> Path:
    """Daily-reward loot tables — the server's own source of truth for drops.
    Overridable for tests/alt deployments via NDRCHST_LOOT_TABLES_DIR."""
    env = os.environ.get("NDRCHST_LOOT_TABLES_DIR")
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "deploy" / "datapacks" / "ndrchst" / "data" / "ndrchst" / "loot_table" / "daily"


def _pretty_item(item_id: str) -> str:
    """`allthemodium:vibranium_ingot` / `..._side` → 'Vibranium Ingot'."""
    base = item_id.split(":")[-1].removesuffix("_side")
    return base.replace("_", " ").title()


def _drops_from_loot(path: Path) -> list[dict]:
    """Parse one tier's loot table into [{icon, name, min, max}], keeping only
    items we actually have an icon for (so a missing sprite degrades to nothing
    rather than a broken image). Dedupes by icon, first occurrence wins."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for pool in data.get("pools", []):
        for e in pool.get("entries", []):
            name = e.get("name", "")
            if e.get("type") != "minecraft:item" or not name:
                continue
            base = name.split(":")[-1]
            if base in seen:
                continue
            # Block items often only ship a `_side` texture as their icon.
            if (_ITEMS_DIR / f"{base}.png").exists():
                icon = base
            elif (_ITEMS_DIR / f"{base}_side.png").exists():
                icon = f"{base}_side"
            else:
                continue
            seen.add(base)
            lo = hi = None
            for fn in e.get("functions", []):
                if fn.get("function") == "minecraft:set_count":
                    c = fn.get("count", {})
                    lo, hi = c.get("min"), c.get("max")
            out.append({"icon": icon, "name": _pretty_item(name), "min": lo, "max": hi})
    return out


@lru_cache(maxsize=1)
def _tier_drops() -> dict[str, list[dict]]:
    """{tier_key: [drop, ...]} read from the loot tables. Cached: drops only
    change on a datapack edit, which ships with a service restart."""
    d = _loot_dir()
    if not d.is_dir():
        return {}
    return {p.stem: _drops_from_loot(p) for p in d.glob("*.json")}


@lru_cache(maxsize=1)
def _float_pool() -> list[str]:
    """Sprite basenames eligible to float in the background — every PNG under
    decor/ that isn't a texture/brand. Add a sprite, it joins the rotation."""
    if not _DECOR_DIR.is_dir():
        return []
    return sorted(
        p.stem for p in _DECOR_DIR.glob("*.png") if p.stem not in _DECOR_NON_FLOAT
    )

# Vanilla wallet connect — no npm, no framework. Talks to the injected
# Phantom / Solflare provider, signs the server's challenge, posts to
# /auth/verify. The API base defaults to same-origin; set window.NDRCHST_API
# to point at a separate origin (the edge Worker must proxy /auth/* + /me).
_WALLET_JS = """
<script>
(function(){
  var API = window.NDRCHST_API || '';
  var signedIn = false;
  function $(id){return document.getElementById(id);}
  function provider(){
    if(window.phantom&&window.phantom.solana) return window.phantom.solana;
    if(window.solana) return window.solana;
    if(window.solflare) return window.solflare;
    return null;
  }
  function render(me){
    signedIn = !!me;
    document.body.classList.toggle('signed-in', signedIn);
    document.body.classList.toggle('signed-out', !signedIn);
    document.querySelectorAll('.client-dl').forEach(function(d){
      d.disabled=false; d.textContent = me ? 'Download the client' : 'Sign in to download'; });
    // Highlight the signed-in player's row on the ranks ladder.
    document.querySelectorAll('.server.me').forEach(function(r){r.classList.remove('me');});
    if(!me) return;
    var t=$('wallet-tier');
    if(t){ $('wallet-addr').textContent=me.display;
      t.textContent=me.tier_name||'No rank'; t.className='tier'+(me.tier?'':' none'); }
    document.querySelectorAll('.rankcard').forEach(function(c){
      var a=c.querySelector('.rc-tier'); if(a) a.textContent=me.tier_name||'Not a holder yet';
      var b=c.querySelector('.rc-pct'); if(b) b.textContent=(me.holdings_pct||0).toFixed(4)+'% of supply';
      var n=c.querySelector('.rc-name'); if(n) n.textContent=me.mc_name; });
    if(me.mc_name){ var mine=document.querySelector('.server[data-mc="'+
      (window.CSS&&CSS.escape?CSS.escape(me.mc_name):me.mc_name)+'"]');
      if(mine) mine.classList.add('me'); }
    var face=$('skin-face'), clr=$('skin-clear');
    if(face){
      if(me.skin_url){ face.style.backgroundImage='url('+API+me.skin_url+'?t='+Date.now()+')';
        face.classList.add('has'); if(clr) clr.style.display=''; }
      else { face.style.backgroundImage=''; face.classList.remove('has'); if(clr) clr.style.display='none'; }
    }
  }
  async function refresh(){
    try{ var r=await fetch(API+'/me',{credentials:'include'});
      render(r.ok?await r.json():null);}catch(e){ render(null);} }
  // Shared sign step: connect the wallet, fetch a challenge, sign it. Returns
  // {pubkey, message, signature} so every flow (session sign-in here, device
  // pairing on /link) drives ONE code path instead of duplicating provider
  // detection + signing. Throws a tagged Error the caller can surface.
  async function requestSignature(){
    var p=provider();
    if(!p){ var e=new Error('no wallet'); e.code='no-wallet'; throw e; }
    var res=await p.connect(); var pk=(res&&res.publicKey?res.publicKey:p.publicKey).toString();
    var ch=await fetch(API+'/auth/challenge',{method:'POST',
      headers:{'content-type':'application/json'},body:JSON.stringify({pubkey:pk})});
    if(!ch.ok){ var e2=new Error('challenge'); e2.code='challenge'; throw e2; }
    var msg=(await ch.json()).message;
    var signed=await p.signMessage(new TextEncoder().encode(msg),'utf8');
    var sig=signed.signature||signed;
    var b64=btoa(String.fromCharCode.apply(null,new Uint8Array(sig)));
    return {pubkey:pk, message:msg, signature:b64};
  }
  async function connect(){
    try{
      var s=await requestSignature();
      var v=await fetch(API+'/auth/verify',{method:'POST',credentials:'include',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({pubkey:s.pubkey,message:s.message,signature:s.signature})});
      render(v.ok?await v.json():null);
      if(!v.ok) alert('Sign-in failed');
    }catch(e){
      if(e&&e.code==='no-wallet') alert('No Solana wallet found. Install Phantom to sign in.');
      else console.error(e);
    }
  }
  async function logout(){ try{await fetch(API+'/auth/logout',{method:'POST',credentials:'include'});}catch(e){} render(null); }
  // Exposed so other pages (e.g. /link) reuse the exact same wallet plumbing.
  window.ndrchstWallet = {provider:provider, requestSignature:requestSignature, refresh:refresh};
  document.addEventListener('DOMContentLoaded',function(){
    var b=$('wallet-connect'); if(b)b.addEventListener('click',connect);
    document.querySelectorAll('.connect-trigger').forEach(function(c){c.addEventListener('click',connect);});
    var o=$('wallet-logout'); if(o)o.addEventListener('click',logout);
    document.querySelectorAll('.client-dl').forEach(function(d){
      d.addEventListener('click',function(){
        if(signedIn){ window.location.href = API + '/me/client/' + d.dataset.sid; }
        else { connect(); }
      });
    });
    // Profile: skin upload / remove (only present on the play page).
    var sf=$('skin-file'), st=$('skin-status');
    if(sf) sf.addEventListener('change',async function(){
      var f=sf.files[0]; sf.value=''; if(!f) return;
      if(f.size>256*1024){ if(st)st.textContent='Too large — skins are tiny (max 256 KB).'; return; }
      try{
        var r=await fetch(API+'/me/skin',{method:'POST',credentials:'include',
          headers:{'content-type':'image/png'},body:await f.arrayBuffer()});
        if(r.ok){ if(st)st.textContent='Skin updated.'; refresh(); }
        else if(st) st.textContent = r.status===400 ? 'That must be a 64x64 PNG skin.' : 'Upload failed.';
      }catch(e){ if(st)st.textContent='Upload failed.'; }
    });
    var sc=$('skin-clear');
    if(sc) sc.addEventListener('click',async function(){
      try{ await fetch(API+'/me/skin',{method:'DELETE',credentials:'include'});
        if(st)st.textContent='Skin removed.'; refresh(); }catch(e){}
    });
    refresh();
  });
})();
</script>
"""

_HEAD = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/png" href="/game/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="wrap">"""

# Drifting End-void backdrop (two parallax layers), shared by every page.
_STARS = '<div class="stars" aria-hidden="true"><i></i><i></i></div>'


def _floats_html() -> str:
    """The ambient floating-decor layer, shared by every page. The sprites
    aren't hardcoded into the markup — JS scatters a random subset of the live
    decor pool ([[_float_pool]]) at random size/position/timing on load, so each
    visit differs and adding a sprite to decor/ joins the rotation for free."""
    pool = json.dumps(_float_pool())
    return (
        '<div class="floats" aria-hidden="true"></div>\n'
        "<script>\n(function(){\n"
        "  try{\n"
        "    if(matchMedia('(prefers-reduced-motion: reduce)').matches) return;\n"
        "    if(innerWidth < 760) return;\n"
        "    var host=document.querySelector('.floats'); if(!host) return;\n"
        f"    var pool={pool};\n"
        "    if(!pool.length) return;\n"
        "    var bag=pool.slice();\n"
        "    for(var i=bag.length-1;i>0;i--){var j=(Math.random()*(i+1))|0;var t=bag[i];bag[i]=bag[j];bag[j]=t;}\n"
        "    var n=Math.min(bag.length, 3+((Math.random()*3)|0));\n"
        "    var R=function(a,b){return a+Math.random()*(b-a);};\n"
        "    for(var k=0;k<n;k++){\n"
        "      var img=document.createElement('img');\n"
        "      img.className='float '+(Math.random()<0.5?'bob':'spin');\n"
        "      img.src='/game/decor/'+bag[k]+'.png'; img.alt='';\n"
        "      img.style.width=R(30,54).toFixed(0)+'px';\n"
        "      img.style.top=R(6,82).toFixed(1)+'vh';\n"
        "      img.style[Math.random()<0.5?'left':'right']=R(1,10).toFixed(1)+'%';\n"
        "      img.style.animationDuration=R(4.5,8).toFixed(2)+'s, '+R(20,40).toFixed(0)+'s';\n"
        "      img.style.animationDelay='-'+R(0,7).toFixed(2)+'s';\n"
        "      host.appendChild(img);\n"
        "    }\n"
        "  }catch(e){}\n"
        "})();\n</script>"
    )


# Brand mark: the ender-eye glyph + wordmark, links to the marketing home.
def _brand() -> str:
    return (f'<a class="brand" href="{html.escape(_home_url(), quote=True)}">'
            '<img class="pixel" src="/game/decor/brand.png" alt="">ndrchst</a>')


def _wallet_ctl() -> str:
    """The shared wallet control: connect button (signed-out) + identity chip
    (signed-in). Wrapped so it stays a unit on the right and never overlaps the
    nav links when they wrap."""
    return (
        '<div class="wnav">'
        '<button id="wallet-connect" class="wbtn">Connect Wallet</button>'
        '<span id="wallet-chip" class="wchip">'
        '<span id="wallet-addr" class="mono"></span>'
        '<span id="wallet-tier" class="tier"></span>'
        '<button id="wallet-logout" title="Sign out">&times;</button></span>'
        "</div>"
    )


def _nav(active: str) -> str:
    def cls(name: str) -> str:
        return ' class="active"' if name == active else ""
    return (
        '<nav class="top">' + _brand() +
        '<div class="links">'
        f'<a href="{html.escape(_home_url(), quote=True)}"{cls("home")}>Home</a>'
        f'<a href="{html.escape(_play_url(), quote=True)}"{cls("play")}>Play</a>'
        f'<a href="/ranks"{cls("ranks")}>Ranks</a>'
        + _wallet_ctl() +
        "</div></nav>"
    )


def _shell(title: str, body: str, *, active: str) -> str:
    return (
        _HEAD.format(title=html.escape(title), css=_CSS)
        + _STARS + _floats_html() + _nav(active) + body + _WALLET_JS
        + "</div></body></html>"
    )


def _teaser_chip_icons() -> list[str]:
    """Four representative reward icons for the landing's deco strip, sampled
    from the real per-tier drops (so it tracks the loot tables, not a hardcoded
    list). One icon each from a low→high spread of tiers, deduped."""
    drops = _tier_drops()
    picks: list[str] = []
    for key in ("holder", "bronze", "gold", "whale"):
        for d in drops.get(key, []):
            if d["icon"] not in picks:
                picks.append(d["icon"])
                break
    return picks[:4]


def render_landing() -> str:
    play = html.escape(_play_url(), quote=True)
    chips = "".join(
        f'<img class="pixel" src="/game/items/{i}.png" alt="">'
        for i in _teaser_chip_icons()
    )
    body = f"""
<section class="hero">
  <img class="hero-orb" src="/game/decor/end_crystal.png" alt="">
  <span class="eyebrow">$NDRCHST · modded Minecraft on Solana</span>
  <h1>Your wallet is your login.<br>Your holdings are your rank.</h1>
  <p class="lede">A modded Minecraft server gated by $NDRCHST. Sign in with your Solana
     wallet, grab a client that's already linked, and press Play — your tier, ranks
     and daily rewards follow you straight in-game.</p>
  <div class="when-out">
    <a class="cta" href="{play}">Play now →</a>
    <a class="cta ghost" href="/ranks">Explore the ranks</a>
  </div>
  <section class="rankcard when-in" style="margin:1.4rem 0 0;max-width:30rem">
    <div class="row"><span class="big rc-tier"></span><span class="pct rc-pct"></span></div>
    <p style="margin:.55rem 0 1rem;color:var(--fg2);font-size:.9rem">
      Signed in as <span class="mono rc-name" style="color:var(--fg)"></span></p>
    <a class="cta" href="{play}">Open the client →</a>
    <a class="cta ghost" href="/ranks">Where you rank</a>
  </section>
  <div class="deco-row"><span class="chips">{chips}</span> daily drops scale with your tier</div>
</section>

<section class="section" style="border-top:none;padding-top:1rem">
  <h2>How it works</h2>
  <div class="features">
    <div class="feature"><div class="num">STEP 01</div><h3>Connect your wallet</h3>
      <p>Sign one message in Phantom or Solflare. No seed phrase leaves your wallet,
         no deposit, no account to make.</p></div>
    <div class="feature"><div class="num">STEP 02</div><h3>Download the client</h3>
      <p>The client arrives already linked to your wallet. It installs the modpack
         and keeps it in sync — you never touch a config file.</p></div>
    <div class="feature"><div class="num">STEP 03</div><h3>Press Play</h3>
      <p>A cryptographic gate verifies your wallet at connect time and drops you in at
         your rank, with your perks and <code>/daily</code> ready.</p></div>
  </div>
</section>

<section class="section">
  <h2>A stack you actually own</h2>
  <div class="features">
    <div class="feature"><h3>One wallet, everywhere</h3>
      <p>The same identity carries the site, the client and the server. Sign in once.</p></div>
    <div class="feature"><h3>Holdings are rank — both ways</h3>
      <p>Your tier is your share of $NDRCHST supply, read live from Solana and pushed
         into the game. Buy in, rank up; the chain is the source of truth.</p></div>
    <div class="feature"><h3>Open and self-hosted</h3>
      <p>Custom client, private edge, our own server — all open source. No third-party
         account stands between you and the world.</p></div>
  </div>
</section>

<p class="disclaimer">$NDRCHST powers identity and in-game rank. It is not an investment,
   a security, or a promise of financial return.</p>

<footer>ndrchst — on-chain identity and rank for Minecraft ·
  <a href="{play}">Play</a> · <a href="/ranks">Ranks</a></footer>
"""
    return _shell("ndrchst — your wallet is your rank in Minecraft", body, active="home")


def render_link(*, code: str = "") -> str:
    """Pairing page: the client opens this with ?code=…; the user connects a
    wallet and signs to bind it to the client session. Shares the same shell +
    wallet plumbing (window.ndrchstWallet) as every other page — the only
    difference is it posts the signature to /client/auth/approve with the code."""
    safe_code = html.escape(code, quote=True)
    code_js = ('"' + safe_code + '"') if code else '""'
    body = f"""
<section class="hero" style="padding:3rem 0 1rem;text-align:center">
  <span class="eyebrow">Link your client</span>
  <h1 style="font-size:1.9rem">Sign in to play</h1>
  <p class="lede" style="margin-left:auto;margin-right:auto">Connect your Solana wallet to
     link this device. Your wallet is your identity and your rank in-game.</p>
</section>
<div style="max-width:30rem;margin:0 auto">
  <div class="callout" style="text-align:center">
    Pairing code <strong class="mono" id="code">{safe_code or '—'}</strong>
  </div>
  <div style="text-align:center;margin:1.4rem 0">
    <button id="link-connect" class="cta">Connect Wallet</button>
  </div>
  <div id="link-status" class="callout" style="display:none;text-align:center"></div>
</div>
<script>
(function(){{
  var API = window.NDRCHST_API || '';
  var code = {code_js};
  function status(msg, ok){{
    var el=document.getElementById('link-status');
    el.style.display='block'; el.textContent=msg;
    el.style.borderColor = ok?'rgba(34,197,94,.4)':'var(--border)';
  }}
  async function link(){{
    if(!code){{ status('Missing pairing code — reopen the link from your client.',false); return; }}
    try{{
      var s=await window.ndrchstWallet.requestSignature();
      var v=await fetch(API+'/client/auth/approve',{{method:'POST',
        headers:{{'content-type':'application/json'}},
        body:JSON.stringify({{code:code,pubkey:s.pubkey,message:s.message,signature:s.signature}})}});
      if(v.ok){{ var d=await v.json();
        document.getElementById('link-connect').style.display='none';
        status('Linked as '+d.display+(d.tier_name?(' · '+d.tier_name):'')+'. Return to your client.',true);
      }} else {{ status('Could not link this device. The code may have expired.',false); }}
    }}catch(e){{
      if(e&&e.code==='no-wallet') status('No Solana wallet found. Install Phantom to continue.',false);
      else status('Wallet sign-in was cancelled.',false);
    }}
  }}
  document.addEventListener('DOMContentLoaded',function(){{
    document.getElementById('link-connect').addEventListener('click',link);
  }});
}})();
</script>
"""
    return _shell("ndrchst — link your client", body, active="")


def render_maintenance(*, message: str = "") -> str:
    """Friendly stand-in served when the box origin is unreachable — the edge
    Worker falls back to this static page so a downtime still looks like ndrchst
    (same shell, nav and wallet control) instead of a raw error."""
    msg = html.escape(message) or (
        "We're doing a quick bit of maintenance. The site, your wallet session "
        "and your rank are all fine — back in a moment.")
    body = f"""
<section class="hero" style="padding:5rem 0 2rem;text-align:center">
  <img class="hero-orb" src="/game/decor/end_crystal.png" alt=""
       style="position:static;display:block;margin:0 auto 1.4rem;right:auto;top:auto">
  <span class="eyebrow">Status</span>
  <h1 style="font-size:2.1rem">Be right back</h1>
  <p class="lede" style="margin-left:auto;margin-right:auto">{msg}</p>
  <div style="margin-top:1.5rem">
    <a class="cta" href="/">Reload ndrchst →</a>
    <a class="cta ghost" href="/ranks">View ranks</a>
  </div>
</section>
<footer>The server, client and edge are open source. · <a href="/">Home</a> ·
  <a href="/ranks">Ranks</a></footer>
"""
    return _shell("ndrchst — maintenance", body, active="")


def _status_class(status: str) -> str:
    return status if status in ("running", "starting", "stopping") else ""


def render_play(servers: list[dict], *, downloads_base: str = "") -> str:
    """servers: list of {name, version, port, status, cross_play,
    bedrock_bridge_port, client_url, config_url}."""
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
            f'<button class="btn client-dl" data-sid="{html.escape(str(s.get("id","")), quote=True)}" '
            'disabled>Sign in to download</button>'
            f'<a class="btn ghost" href="{s["config_url"]}">config</a>'
            '</div></div>'
        )

    # Standalone per-OS binaries (only when the operator has published them).
    binaries_html = ""
    if downloads_base:
        base = downloads_base.rstrip("/")
        links = "".join(
            f'<li>{html.escape(label)}: <a href="{base}/{html.escape(fname)}">{html.escape(fname)}</a></li>'
            for label, fname in CLIENT_ASSETS
        )
        binaries_html = (
            '<p style="color:var(--fg2);font-size:.92rem">Prefer a standalone client? '
            "Download the binary for your OS and drop a server's "
            "<code>config.json</code> next to it:</p>"
            f'<ul class="mono" style="color:var(--fg2);line-height:1.9;font-size:.88rem">{links}</ul>'
        )

    body = f"""
<section class="hero" style="padding:3rem 0 1.5rem">
  <h1 style="font-size:2.1rem">Play on ndrchst</h1>
  <p class="lede">Connect your wallet to download the client — it arrives already linked to
     your wallet, so you just press Play. It installs the modpack and joins for you.</p>
  <div class="when-out">
    <button class="wbtn connect-trigger"
       style="font-size:.95rem;padding:.55rem 1.1rem">Connect Wallet to begin</button>
    <a class="cta ghost" href="/ranks">See the ranks</a>
  </div>
  <section class="rankcard when-in" style="margin:1.4rem 0 0;max-width:32rem">
    <div class="row"><span class="big rc-tier"></span><span class="pct rc-pct"></span></div>
    <p style="margin:.55rem 0 .2rem;color:var(--fg2);font-size:.9rem">
      Signed in as <span class="mono rc-name" style="color:var(--fg)"></span> — you're cleared to join.</p>
    <div class="profile-skin">
      <div class="skin-face" id="skin-face" title="Your skin"></div>
      <div class="skin-up">
        <div class="row">
          <label class="btn ghost" style="cursor:pointer">Upload skin
            <input type="file" id="skin-file" accept="image/png" hidden></label>
          <button class="btn ghost" id="skin-clear" style="display:none">Remove</button>
        </div>
        <div class="meta" id="skin-status">A 64x64 PNG — your face on your profile and in-game.</div>
      </div>
    </div>
    <a class="xlink" href="/ranks" style="margin-top:1rem;display:inline-flex">See where you stand on the ladder →</a>
  </section>
</section>

<section class="section" style="border-top:none;padding-top:0">
  <h2>Servers</h2>
  {''.join(rows)}
</section>

<section class="section">
  <h2>How to run the client</h2>
  <div class="os-tabs">
    <div class="os-tab" data-os="win">Windows</div>
    <div class="os-tab" data-os="mac">macOS</div>
    <div class="os-tab" data-os="linux">Linux</div>
  </div>
  <div class="os-panel" data-os="win">
    <ol>
      <li>Download a server's client above and unzip it.</li>
      <li>Run <code>ndrchst-client.exe</code> (or <code>launch.bat</code>).</li>
      <li>Enter your name, pick your RAM, and press <strong>Play</strong>.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="mac">
    <ol>
      <li>Download a server's client above and unzip it.</li>
      <li>Run it (you may need to allow it in System Settings → Privacy &amp; Security).</li>
      <li>Enter your name, pick your RAM, and press <strong>Play</strong>.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="linux">
    <ol>
      <li>Download a server's client above and unzip it: <code>unzip client.zip &amp;&amp; cd client</code></li>
      <li>Run <code>./launch.sh</code>.</li>
      <li>Enter your name, pick your RAM (and Graphics, on hybrid laptops), and press <strong>Play</strong>.</li>
    </ol>
  </div>
  {binaries_html}
</section>

<footer>The client is an offline launcher pinned to each server. It mirrors the server's
  mod set from a CDN, so first launch downloads the pack once. ·
  <a href="{_home_url()}">Home</a> · <a href="/ranks">Ranks</a></footer>

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


def _tier_band(tiers: list[dict], idx: int) -> str:
    """Exact supply band for the tier at ascending index `idx`: the base tier is
    open at the bottom, the top tier open at the top, the rest are [lo, hi)."""
    lo = tiers[idx]["min_pct"]
    if lo <= 0:
        return "any holdings · base tier"
    hi = tiers[idx + 1]["min_pct"] if idx + 1 < len(tiers) else None
    return f"≥ {lo:g}% of supply" if hi is None else f"{lo:g}% – {hi:g}% of supply"  # noqa: RUF001


def _drop_amount(d: dict) -> str:
    """Count detail (e.g. ' x1-3' / ' x2') appended to a drop's tooltip text."""
    lo, hi = d.get("min"), d.get("max")
    if lo is None:
        return ""
    return f" ·×{lo}" if lo == hi else f" ·×{lo}–{hi}"  # noqa: RUF001


def render_ranks(holders: list[dict], tiers: list[dict]) -> str:
    """tiers: list of {key, name, min_pct} ascending. holders: list of
    {display, mc_name, tier, tier_name, holdings_pct} sorted by holdings desc."""
    def _drops(key: str) -> str:
        items = _tier_drops().get(key, [])
        if not items:
            return ""
        chips = "".join(
            f'<span class="tip" data-tip="{html.escape(d["name"] + _drop_amount(d), quote=True)}">'
            f'<img class="pixel" src="/game/items/{html.escape(d["icon"], quote=True)}.png" alt=""></span>'
            for d in items
        )
        n = len(items)
        return (f'<div class="drops">{chips}</div>'
                f'<div class="drop-note">{n} daily reward{"" if n == 1 else "s"} '
                "· hover an item for the amount</div>")

    # Build ascending (so bands can see the next tier), display top-first.
    cards = [
        '<div class="feature tier-card">'
        '<div class="lrow">'
        f'<h3>{html.escape(t["name"])}</h3>'
        f'<span class="thr">{_tier_band(tiers, i)}</span>'
        "</div>"
        f'{_drops(t["key"])}'
        "</div>"
        for i, t in enumerate(tiers)
    ]
    ladder = "".join(reversed(cards))

    rows = []
    if not holders:
        rows.append(
            '<div class="empty">No ranked holders yet — connect a wallet to be the first.</div>')
    for i, h in enumerate(holders, start=1):
        tier_name = h.get("tier_name") or "No rank"
        pill_cls = "pill" if h.get("tier") else "pill none"
        rows.append(
            f'<div class="server" data-mc="{html.escape(h["mc_name"], quote=True)}">'
            '<div class="right" style="gap:.9rem">'
            f'<span class="rankno">#{i}</span>'
            "<div>"
            f'<div class="name mono">{html.escape(h["display"])}'
            '<span class="youtag">you</span></div>'
            f'<div class="meta mono">in-game {html.escape(h["mc_name"])}</div>'
            "</div></div>"
            '<div class="right">'
            f'<span class="{pill_cls}">{html.escape(tier_name)}</span>'
            f'<span class="meta mono">{h.get("holdings_pct", 0.0):.4f}%</span>'
            "</div></div>"
        )

    play = html.escape(_play_url(), quote=True)
    body = f"""
<section class="hero" style="padding:3rem 0 1rem">
  <span class="eyebrow">$NDRCHST · ranks</span>
  <h1 style="font-size:2.1rem">Holdings are rank</h1>
  <p class="lede">Your tier is your share of $NDRCHST supply, read straight from the chain.
     Hold more, rank up — both here and in-game.</p>
  <p class="when-in" style="color:var(--accent);font-size:.92rem;margin:.2rem 0 0">
    You're <span class="mono rc-tier" style="color:var(--fg)"></span> ·
    <span class="mono rc-pct"></span> — your row is highlighted below.</p>
  <div class="when-out" style="margin-top:.4rem">
    <a class="cta" href="{play}">Get the client →</a>
    <button class="wbtn connect-trigger" style="margin-left:.5rem">Connect to see your rank</button>
  </div>
</section>

<section class="section" style="border-top:none;padding-top:0">
  <div class="row" style="display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap">
    <h2 style="margin:0">Tiers</h2>
    <a class="xlink" href="{play}">Claim yours — get the client →</a>
  </div>
  <div class="features ladder" style="margin-top:1.2rem">{ladder}</div>
</section>

<section class="section">
  <h2>Holders</h2>
  {''.join(rows)}
</section>

<footer>Ranks track the chain. Buys and sells are reflected the next time holdings are
  refreshed. · <a href="{_home_url()}">Home</a> · <a href="{play}">Play</a></footer>
"""
    return _shell("ndrchst — ranks", body, active="ranks")
