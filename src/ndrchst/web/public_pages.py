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
import os


def _play_url() -> str:
    """Canonical host for the app + auth flow. The session cookie is set with no
    Domain attribute, so it scopes to whatever host the user is on; the marketing
    landing lives on the apex but "Play" must cross over to the play host or the
    wallet session strands on the wrong origin. Falls back to a relative path
    only when neither the play nor edge URL is configured (dev/tests)."""
    return os.environ.get("NDRCHST_PLAY_URL") or os.environ.get("NDRCHST_EDGE_URL") or "/play"


def _home_url() -> str:
    """Canonical marketing home (the apex). The nav/brand must point here, not a
    relative "/" — on the play host "/" serves the app, so a relative Home link
    would never reach the landing. Derived from the edge URL (play.<zone> ->
    <zone>) when not set explicitly; falls back to "/" in dev/tests."""
    home = os.environ.get("NDRCHST_HOME_URL")
    if home:
        return home
    edge = os.environ.get("NDRCHST_EDGE_URL", "")
    if edge:
        return edge.replace("//play.", "//", 1)
    return "/"

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
/* Floating decor sprites (existing PNGs), GPU transforms only, hidden on phones. */
.float{position:absolute;image-rendering:pixelated;pointer-events:none;z-index:0;
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
"""

# Per-tier daily reward icons (mirrors deploy/datapacks loot tables) — surfaced
# on /ranks so holders see exactly what each tier drops. Files under /game/items.
TIER_DROPS = {
    "holder": ["diamond", "gold_ingot", "experience_bottle"],
    "bronze": ["diamond", "inferium_essence", "netherite_scrap", "experience_bottle"],
    "silver": ["prudentium_essence", "diamond", "allthemodium_ingot", "netherite_scrap"],
    "gold": ["tertium_essence", "netherite_ingot", "vibranium_ingot", "allthemodium_ingot"],
    "diamond": ["imperium_essence", "ancient_debris_side", "unobtainium_ingot", "vibranium_ingot"],
    "whale": ["supremium_essence", "nether_star", "unobtainium_ingot", "vibranium_ingot"],
}

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
  }
  async function refresh(){
    try{ var r=await fetch(API+'/me',{credentials:'include'});
      render(r.ok?await r.json():null);}catch(e){ render(null);} }
  async function connect(){
    var p=provider();
    if(!p){ alert('No Solana wallet found. Install Phantom to sign in.'); return; }
    try{
      var res=await p.connect(); var pk=(res&&res.publicKey?res.publicKey:p.publicKey).toString();
      var ch=await fetch(API+'/auth/challenge',{method:'POST',
        headers:{'content-type':'application/json'},body:JSON.stringify({pubkey:pk})});
      if(!ch.ok){ alert('Could not start sign-in'); return; }
      var msg=(await ch.json()).message;
      var enc=new TextEncoder().encode(msg);
      var signed=await p.signMessage(enc,'utf8');
      var sig=signed.signature||signed;
      var b64=btoa(String.fromCharCode.apply(null,new Uint8Array(sig)));
      var v=await fetch(API+'/auth/verify',{method:'POST',credentials:'include',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({pubkey:pk,message:msg,signature:b64})});
      render(v.ok?await v.json():null);
      if(!v.ok) alert('Sign-in failed');
    }catch(e){ console.error(e); }
  }
  async function logout(){ try{await fetch(API+'/auth/logout',{method:'POST',credentials:'include'});}catch(e){} render(null); }
  document.addEventListener('DOMContentLoaded',function(){
    var b=$('wallet-connect'); if(b)b.addEventListener('click',connect);
    document.querySelectorAll('.connect-trigger').forEach(function(c){c.addEventListener('click',connect);});
    var o=$('wallet-logout'); if(o)o.addEventListener('click',logout);
    document.querySelectorAll('.client-dl').forEach(function(d){
      d.addEventListener('click',function(){
        if(signedIn){ window.location.href = API + '/me/pilot/' + d.dataset.sid; }
        else { connect(); }
      });
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
        + _STARS + _nav(active) + body + _WALLET_JS + "</div></body></html>"
    )


# Tiers teaser for the landing — one representative reward icon per tier.
_TIER_TEASER = [
    ("Holder", "diamond", "any holdings"),
    ("Bronze", "inferium_essence", "≥ 0.1%"),
    ("Silver", "allthemodium_ingot", "≥ 0.5%"),
    ("Gold", "vibranium_ingot", "≥ 1%"),
    ("Diamond", "unobtainium_ingot", "≥ 2.5%"),
    ("Whale", "nether_star", "≥ 5%"),
]


def render_landing(*, play_url: str | None = None) -> str:
    play = html.escape(play_url or _play_url(), quote=True)
    teaser = "".join(
        '<a class="feature" href="/ranks">'
        '<div class="lrow">'
        f'<h3>{name}</h3><span class="thr">{thr}</span></div>'
        f'<div class="drops"><img class="pixel" src="/game/items/{icon}.png" alt=""></div>'
        "</a>"
        for name, icon, thr in _TIER_TEASER
    )
    chips = "".join(
        f'<img class="pixel" src="/game/items/{i}.png" alt="">'
        for i in ("diamond", "inferium_essence", "vibranium_ingot", "nether_star")
    )
    body = f"""
<section class="hero">
  <img class="hero-orb" src="/game/decor/end_crystal.png" alt="">
  <img class="float bob" src="/game/decor/chorus_fruit.png" alt="" style="width:46px;top:7rem;left:-1.5rem">
  <img class="float spin" src="/game/decor/ender_pearl.png" alt="" style="width:38px;bottom:1rem;left:34%">
  <img class="float bob" src="/game/decor/purpur_block.png" alt="" style="width:40px;top:1.5rem;right:31%">
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
  <div class="row" style="display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap">
    <h2 style="margin:0">Six tiers, read from the chain</h2>
    <a class="xlink" href="/ranks">See the full ladder →</a>
  </div>
  <div class="features ladder" style="margin-top:1.2rem">{teaser}</div>
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
    wallet and signs to bind it to the client session."""
    safe_code = html.escape(code, quote=True)
    body = f"""
<section class="hero" style="padding:3.5rem 0 1rem;text-align:center">
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
  var code = {('"' + safe_code + '"') if code else '""'};
  function provider(){{
    if(window.phantom&&window.phantom.solana) return window.phantom.solana;
    if(window.solana) return window.solana;
    if(window.solflare) return window.solflare;
    return null;
  }}
  function status(msg, ok){{
    var el=document.getElementById('link-status');
    el.style.display='block'; el.textContent=msg;
    el.style.borderColor = ok?'rgba(34,197,94,.4)':'var(--border)';
  }}
  async function connect(){{
    var p=provider();
    if(!p){{ status('No Solana wallet found. Install Phantom to continue.',false); return; }}
    if(!code){{ status('Missing pairing code — reopen the link from your client.',false); return; }}
    try{{
      var res=await p.connect(); var pk=(res&&res.publicKey?res.publicKey:p.publicKey).toString();
      var ch=await fetch(API+'/auth/challenge',{{method:'POST',
        headers:{{'content-type':'application/json'}},body:JSON.stringify({{pubkey:pk}})}});
      var msg=(await ch.json()).message;
      var signed=await p.signMessage(new TextEncoder().encode(msg),'utf8');
      var sig=signed.signature||signed;
      var b64=btoa(String.fromCharCode.apply(null,new Uint8Array(sig)));
      var v=await fetch(API+'/pilot/auth/approve',{{method:'POST',
        headers:{{'content-type':'application/json'}},
        body:JSON.stringify({{code:code,pubkey:pk,message:msg,signature:b64}})}});
      if(v.ok){{ var d=await v.json();
        document.getElementById('link-connect').style.display='none';
        status('Linked as '+d.display+(d.tier_name?(' · '+d.tier_name):'')+'. Return to your client.',true);
      }} else {{ status('Could not link this device. The code may have expired.',false); }}
    }}catch(e){{ console.error(e); status('Wallet sign-in was cancelled.',false); }}
  }}
  document.addEventListener('DOMContentLoaded',function(){{
    document.getElementById('link-connect').addEventListener('click',connect);
  }});
}})();
</script>
"""
    return _HEAD.format(title=html.escape("ndrchst — link your client"), css=_CSS) \
        + _STARS + '<nav class="top">' + _brand() + '</nav>' \
        + '<div class="wrap-inner">' + body + "</div></div></body></html>"


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
            for label, fname in PILOT_ASSETS
        )
        binaries_html = (
            '<p style="color:var(--fg2);font-size:.92rem">Prefer a standalone client? '
            "Download the binary for your OS and drop a server's "
            "<code>config.json</code> next to it:</p>"
            f'<ul class="mono" style="color:var(--fg2);line-height:1.9;font-size:.88rem">{links}</ul>'
        )

    body = f"""
<section class="hero" style="padding:3rem 0 1.5rem">
  <img class="float bob" src="/game/decor/purpur_block.png" alt="" style="width:48px;top:2rem;right:4%">
  <img class="float spin" src="/game/decor/ender_eye.png" alt="" style="width:34px;bottom:.5rem;right:22%">
  <h1 style="font-size:2.1rem">Play on ndrchst</h1>
  <p class="lede">Connect your wallet to download the client — it arrives already linked to
     your wallet, so you just press Play. It installs the modpack and joins for you.</p>
  <div class="when-out"><button class="wbtn connect-trigger"
     style="font-size:.95rem;padding:.55rem 1.1rem">Connect Wallet to begin</button></div>
  <section class="rankcard when-in" style="margin:1.4rem 0 0;max-width:32rem">
    <div class="row"><span class="big rc-tier"></span><span class="pct rc-pct"></span></div>
    <p style="margin:.55rem 0 .9rem;color:var(--fg2);font-size:.9rem">
      Signed in as <span class="mono rc-name" style="color:var(--fg)"></span> — you're cleared to join.</p>
    <a class="xlink" href="/ranks">See where you stand on the ladder →</a>
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
      <li>Run <code>ndrchst-pilot.exe</code> (or <code>launch.bat</code>).</li>
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
      <li>Download a server's client above and unzip it: <code>unzip pilot.zip &amp;&amp; cd pilot</code></li>
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


def _fmt_threshold(min_pct: float) -> str:
    if min_pct <= 0:
        return "any holdings"
    # Trim trailing zeros: 0.10 -> 0.1, 2.50 -> 2.5
    s = f"{min_pct:g}"
    return f"≥ {s}% of supply"


def render_ranks(holders: list[dict], tiers: list[dict]) -> str:
    """tiers: list of {key, name, min_pct} ascending. holders: list of
    {display, mc_name, tier, tier_name, holdings_pct} sorted by holdings desc."""
    def _drops(key: str) -> str:
        items = TIER_DROPS.get(key, [])
        if not items:
            return ""
        icons = "".join(
            f'<img class="pixel" src="/game/items/{i}.png" alt="" '
            f'title="{html.escape(i.replace("_side", "").replace("_", " "))}">'
            for i in items
        )
        return f'<div class="drops">{icons}</div>'

    ladder = "".join(
        '<div class="feature">'
        '<div class="lrow">'
        f'<h3>{html.escape(t["name"])}</h3>'
        f'<span class="thr">{_fmt_threshold(t["min_pct"])}</span>'
        "</div>"
        f'{_drops(t["key"])}'
        "</div>"
        for t in reversed(tiers)  # show the top tier first
    )

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
  <img class="float spin" src="/game/decor/end_crystal.png" alt="" style="width:42px;top:1.5rem;right:6%">
  <img class="float bob" src="/game/decor/ender_pearl.png" alt="" style="width:32px;bottom:.5rem;right:24%">
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
