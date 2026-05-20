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
.eyebrow{display:inline-block;font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);border:1px solid rgba(34,197,94,.32);border-radius:999px;
  padding:.32rem .75rem;margin-bottom:1.1rem}
.feature .num{font-family:'JetBrains Mono',ui-monospace,monospace;color:var(--accent);
  font-size:.8rem;letter-spacing:.05em}
.callout{background:linear-gradient(135deg,#101411,#16181b);border:1px solid var(--border);
  border-radius:var(--radius);padding:1.3rem 1.4rem;color:var(--fg2);font-size:.95rem;line-height:1.65}
.callout strong{color:var(--fg)}
.disclaimer{color:var(--muted);font-size:.78rem;line-height:1.65;margin-top:1.4rem}
.disclaimer .status{color:var(--fg2)}
.wbtn{font:inherit;cursor:pointer;border:1px solid rgba(34,197,94,.4);background:rgba(34,197,94,.08);
  color:var(--accent);border-radius:999px;padding:.4rem .9rem;font-size:.85rem;font-weight:600}
.wbtn:hover{background:rgba(34,197,94,.16)}
.wchip{display:none;align-items:center;gap:.5rem;border:1px solid var(--border);background:var(--bg2);
  border-radius:999px;padding:.32rem .4rem .32rem .8rem;font-size:.84rem;color:var(--fg)}
.wchip .tier{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#052e16;
  background:var(--accent);border-radius:999px;padding:.15rem .5rem;font-weight:700}
.wchip .tier.none{background:var(--bg3);color:var(--fg2)}
.wchip button{font:inherit;cursor:pointer;background:none;border:none;color:var(--muted);font-size:.9rem;padding:0 .2rem}
.rankcard{display:none;background:linear-gradient(135deg,#101411,#16181b);border:1px solid var(--border);
  border-radius:var(--radius);padding:1.2rem 1.4rem;margin:0 0 2rem}
.rankcard .row{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.rankcard .big{font-size:1.3rem;font-weight:700}
.rankcard .pct{color:var(--fg2);font-size:.9rem}
footer{padding:2.5rem 0 3rem;color:var(--muted);font-size:.82rem;border-top:1px solid var(--border)}
"""

# Vanilla wallet connect — no npm, no framework. Talks to the injected
# Phantom / Solflare provider, signs the server's challenge, posts to
# /auth/verify. The API base defaults to same-origin; set window.NDRCHST_API
# to point at a separate origin (the edge Worker must proxy /auth/* + /me).
_WALLET_JS = """
<script>
(function(){
  var API = window.NDRCHST_API || '';
  function $(id){return document.getElementById(id);}
  function provider(){
    if(window.phantom&&window.phantom.solana) return window.phantom.solana;
    if(window.solana) return window.solana;
    if(window.solflare) return window.solflare;
    return null;
  }
  function render(me){
    var btn=$('wallet-connect'), chip=$('wallet-chip');
    if(!me){ if(btn)btn.style.display=''; if(chip)chip.style.display='none';
      document.querySelectorAll('.rankcard').forEach(function(c){c.style.display='none';}); return; }
    if(btn)btn.style.display='none';
    if(chip){ chip.style.display='inline-flex';
      $('wallet-addr').textContent=me.display;
      var t=$('wallet-tier'); t.textContent=me.tier_name||'No rank';
      t.className='tier'+(me.tier?'':' none'); }
    var rc=$('rankcard');
    if(rc){ rc.style.display='block';
      $('rc-tier').textContent=me.tier_name||'Not a holder yet';
      $('rc-pct').textContent=(me.holdings_pct||0).toFixed(4)+'% of supply';
      $('rc-name').textContent=me.mc_name; }
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
    var o=$('wallet-logout'); if(o)o.addEventListener('click',logout);
    refresh();
  });
})();
</script>
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
        '<button id="wallet-connect" class="wbtn">Connect Wallet</button>'
        '<span id="wallet-chip" class="wchip">'
        '<span id="wallet-addr" class="mono"></span>'
        '<span id="wallet-tier" class="tier"></span>'
        '<button id="wallet-logout" title="Sign out">&times;</button></span>'
        "</div></nav>"
    )
    return (
        _HEAD.format(title=html.escape(title), css=_CSS)
        + nav + body + _WALLET_JS + "</div></body></html>"
    )


def render_landing(*, play_url: str = "/play") -> str:
    play = html.escape(play_url, quote=True)
    body = f"""
<section class="hero">
  <span class="eyebrow">$NDRCHST · on Solana</span>
  <h1>Connect your wallet. Play. Rank up.</h1>
  <p class="lede">A Minecraft server where your wallet is your login and your holdings
     are your rank.</p>
  <a class="cta" href="{play}">Play now →</a>
</section>

<div id="rankcard" class="rankcard">
  <div class="row"><span class="big" id="rc-tier"></span><span class="pct" id="rc-pct"></span></div>
  <p style="margin:.6rem 0 0;color:var(--fg2);font-size:.9rem">
    In-game name <span class="mono" id="rc-name" style="color:var(--fg)"></span></p>
</div>

<div class="features">
  <div class="feature"><h3>One wallet, everywhere</h3>
    <p>Sign in once. The same identity carries the site, the launcher, and the server.</p></div>
  <div class="feature"><h3>Holdings are rank</h3>
    <p>Your tier is your share of $NDRCHST supply — read from the chain, both ways.</p></div>
  <div class="feature"><h3>A stack you own</h3>
    <p>Custom launcher, private edge, your server. The token sits on real infrastructure.</p></div>
</div>

<p class="disclaimer">$NDRCHST powers identity and in-game rank. It is not an investment,
   a security, or a promise of financial return.</p>

<footer>ndrchst — on-chain identity and rank for Minecraft.</footer>
"""
    return _shell("ndrchst — connect your wallet, play, rank up", body, active="home")


def render_link(*, code: str = "") -> str:
    """Pairing page: the pilot opens this with ?code=…; the user connects a
    wallet and signs to bind it to the launcher session."""
    safe_code = html.escape(code, quote=True)
    body = f"""
<section class="hero" style="padding:3.5rem 0 1rem;text-align:center">
  <span class="eyebrow">Link your launcher</span>
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
    if(!code){{ status('Missing pairing code — reopen the link from your launcher.',false); return; }}
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
        status('Linked as '+d.display+(d.tier_name?(' · '+d.tier_name):'')+'. Return to your launcher.',true);
      }} else {{ status('Could not link this device. The code may have expired.',false); }}
    }}catch(e){{ console.error(e); status('Wallet sign-in was cancelled.',false); }}
  }}
  document.addEventListener('DOMContentLoaded',function(){{
    document.getElementById('link-connect').addEventListener('click',connect);
  }});
}})();
</script>
"""
    return _HEAD.format(title=html.escape("ndrchst — link your launcher"), css=_CSS) \
        + '<nav class="top"><a class="brand" href="/">ndrchst</a></nav>' \
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
