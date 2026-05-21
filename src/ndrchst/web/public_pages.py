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
/* Login-state visibility, toggled by html.signed-in / .signed-out from wallet JS. */
.when-in{display:none}html.signed-in .when-in{display:block}
html.signed-in .when-out{display:none}
html.signed-in .wbtn{display:none}
html.signed-in .wchip{display:inline-flex}
.rankcard.when-in{display:none}html.signed-in .rankcard.when-in{display:block}
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
/* Skin search (find a skin by Minecraft username, apply with one click). */
.skin-search{display:flex;gap:.5rem;margin-top:.9rem;flex-wrap:wrap}
.skin-search input{flex:1;min-width:12rem;background:rgba(10,6,19,.6);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--fg);padding:.5rem .75rem;font:inherit}
.skin-search input::placeholder{color:var(--muted)}
.skin-search input:focus{outline:none;border-color:rgba(20,241,149,.5)}
.skin-results{display:flex;gap:.7rem;flex-wrap:wrap;margin-top:.7rem}
.skin-results .meta{color:var(--muted);font-size:.8rem}
.skin-pick{display:flex;flex-direction:column;align-items:center;gap:.4rem;padding:.6rem;
  border:1px solid var(--border);border-radius:10px;background:rgba(22,16,41,.6)}
.skin-pick .face{width:56px;height:56px;image-rendering:pixelated;border-radius:6px;
  background-color:rgba(10,6,19,.6);background-repeat:no-repeat;
  background-size:448px 448px;background-position:-56px -56px}
.skin-pick .nm{font-size:.76rem;color:var(--fg2);max-width:6rem;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.skin-pick .btn{padding:.3rem .8rem;font-size:.8rem}
.skindex-link{display:inline-block;margin-top:.7rem;font-size:.82rem;color:var(--fg2)}
.skindex-link:hover{color:var(--fg)}
/* Ranks transparency: per-tier rolls with item, amount and exact drop odds. */
.ladder.detailed{grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
.callout code{background:var(--bg3);padding:.08rem .35rem;border-radius:4px;font-size:.85em;
  font-family:'JetBrains Mono',ui-monospace,monospace;color:var(--fg)}
.rolls{margin-top:.7rem;display:flex;flex-direction:column;gap:.55rem}
.roll{display:flex;gap:.6rem;align-items:flex-start}
.roll-no{flex:none;min-width:3.1rem;padding-top:.4rem;font-family:'JetBrains Mono',ui-monospace,monospace;
  font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.roll-items{display:flex;flex-direction:column;gap:.28rem;flex:1;min-width:0}
.ritem{display:grid;grid-template-columns:22px 1fr auto auto;align-items:center;gap:.5rem;
  background:rgba(10,6,19,.45);border:1px solid var(--border);border-radius:7px;padding:.26rem .55rem}
.ritem img{width:22px;height:22px;image-rendering:pixelated}
.ritem-noicon{width:22px;height:22px;display:flex;align-items:center;justify-content:center;color:var(--muted)}
.ritem .rname{font-size:.82rem;color:var(--fg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ritem .ramt{font-size:.8rem;color:var(--fg)}
.ritem .rpct{font-size:.74rem;color:var(--accent);text-align:right;min-width:2.7rem}
.inherit{margin-top:.55rem;font-size:.8rem;color:var(--fg2);border-left:2px solid rgba(20,241,149,.5);
  padding:.35rem .6rem;background:rgba(20,241,149,.06);border-radius:0 7px 7px 0}
.inherit strong{color:var(--accent)}
/* Nested tier card (ranks): identity + holder count header, demonstration
   (icons + odds), compact chips, and a highlighted "your rank" state. */
.tc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:.6rem}
.tc-id{display:flex;flex-direction:column;gap:.15rem}
.tc-id h3{margin:0}
.tc-count{flex:none;font-size:.72rem;color:var(--fg2);background:var(--bg3);
  padding:.2rem .55rem;border-radius:999px;white-space:nowrap}
.tc-you{display:none;margin:.5rem 0 0;font-size:.72rem;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;color:var(--accent)}
.tier-card.me{outline:2px solid var(--accent);outline-offset:2px;
  box-shadow:0 0 0 1px rgba(20,241,149,.35),0 10px 34px rgba(20,241,149,.14)}
.tier-card.me .tc-you{display:block}
.tier-card.me .tc-count{background:rgba(20,241,149,.16);color:var(--accent)}
.tc-chips{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.7rem}
.tc-chip{font-size:.74rem;color:var(--fg2);background:var(--bg3);border:1px solid var(--border);
  padding:.18rem .55rem;border-radius:999px}
.soon{border:1px dashed var(--border);border-radius:var(--radius-sm);padding:.9rem 1.1rem;
  color:var(--fg2);font-size:.9rem;text-align:center;margin-top:.2rem}
/* Profile popover anchored to the wallet chip (skin + identity, every page). */
.wprofile{position:relative}
.wchip{cursor:pointer;gap:.45rem;padding-left:.4rem}
.wchip-face{width:20px;height:20px;flex:none;border-radius:5px;image-rendering:pixelated;
  background-color:var(--bg3);background-repeat:no-repeat;background-size:160px 160px;
  background-position:-20px -20px;border:1px solid var(--border)}
.profile-pop{position:absolute;top:calc(100% + .55rem);right:0;width:312px;max-width:88vw;z-index:40;
  background:linear-gradient(135deg,rgba(36,26,64,.98),rgba(14,10,26,.98));
  border:1px solid var(--border);border-radius:var(--radius);padding:1rem;text-align:left;
  box-shadow:0 22px 54px rgba(0,0,0,.6);backdrop-filter:blur(10px)}
.profile-pop[hidden]{display:none}
.pp-head{display:flex;gap:.85rem;align-items:center;margin-bottom:.85rem}
.pp-id{min-width:0}
.pp-name{font-size:.95rem;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pp-sub{display:flex;gap:.45rem;align-items:baseline;margin-top:.15rem;font-size:.82rem}
.pp-sub .rc-tier{color:var(--accent);font-weight:600}
.pp-foot{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.9rem;padding-top:.8rem;border-top:1px solid var(--border)}
.pp-foot .btn{font-size:.82rem;padding:.42rem .75rem}
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


def _icon_for(base: str) -> str | None:
    """Sprite basename for an item, or None if we have no icon (block items
    often only ship a `_side` texture)."""
    if (_ITEMS_DIR / f"{base}.png").exists():
        return base
    if (_ITEMS_DIR / f"{base}_side.png").exists():
        return f"{base}_side"
    return None


def _loot_pools(path: Path) -> list[list[dict]]:
    """Parse one tier's loot table into ROLLS: a list of pools, each a list of
    {icon, name, min, max, weight, pct}. /daily rolls once per pool, picking one
    entry by weight — so pct = this entry's share of its pool's total weight.
    This is the real source the transparency table renders; nothing hardcoded."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    rolls: list[list[dict]] = []
    for pool in data.get("pools", []):
        items = [e for e in pool.get("entries", [])
                 if e.get("type") == "minecraft:item" and e.get("name")]
        entries: list[dict] = []
        for e in items:
            base = e["name"].split(":")[-1]
            lo = hi = None
            for fn in e.get("functions", []):
                if fn.get("function") == "minecraft:set_count":
                    c = fn.get("count", {})
                    lo, hi = c.get("min"), c.get("max")
            entries.append({
                "icon": _icon_for(base), "name": _pretty_item(e["name"]),
                "min": lo, "max": hi, "weight": e.get("weight", 1),
            })
        if entries:
            _assign_odds(entries)
            rolls.append(entries)
    return rolls


def _assign_odds(entries: list[dict]) -> None:
    """Set each entry's integer "pct" from its weight, using largest-remainder
    rounding so a roll's odds sum to EXACTLY 100% (independent round() can drift
    to 99/101, which reads as sloppy on a page that promises transparency)."""
    total = sum(e["weight"] for e in entries) or 1
    exact = [100 * e["weight"] / total for e in entries]
    floors = [int(x) for x in exact]
    leftover = 100 - sum(floors)
    # Hand the remaining points to the largest fractional remainders.
    order = sorted(range(len(entries)), key=lambda k: exact[k] - floors[k], reverse=True)
    for k in order[:leftover]:
        floors[k] += 1
    for e, p in zip(entries, floors, strict=True):
        e["pct"] = p


@lru_cache(maxsize=1)
def _tier_loot() -> dict[str, list[list[dict]]]:
    """{tier_key: [roll, ...]} — the full weighted breakdown per tier. Cached
    like _tier_drops (changes only on a datapack edit + restart)."""
    d = _loot_dir()
    if not d.is_dir():
        return {}
    return {p.stem: _loot_pools(p) for p in d.glob("*.json")}


# Vanilla treasure tables a tier's daily crate also pulls from — built-in random
# loot, read straight from the loot table's minecraft: refs (single source).
_TREASURE_NAMES = {
    "minecraft:chests/simple_dungeon": "Dungeon",
    "minecraft:chests/abandoned_mineshaft": "Mineshaft",
    "minecraft:chests/nether_bridge": "Nether Fortress",
    "minecraft:chests/bastion_treasure": "Bastion",
    "minecraft:chests/end_city": "End City",
    "minecraft:chests/ancient_city": "Ancient City",
}


def _tier_treasure(key: str) -> list[str]:
    """Human names of the vanilla treasure tables a tier's daily crate pulls a
    random bonus from (the built-in random-loot 'system'). From the loot table's
    own minecraft: refs — additive tiers chain lower tiers' treasure too."""
    try:
        data = json.loads((_loot_dir() / f"{key}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for pool in data.get("pools", []):
        for e in pool.get("entries", []):
            v = e.get("value", "") if e.get("type") == "minecraft:loot_table" else ""
            if isinstance(v, str) and v.startswith("minecraft:"):
                out.append(_TREASURE_NAMES.get(
                    v, v.rsplit("/", 1)[-1].replace("_", " ").title()))
    return out


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
  var skinVer = 0;     // bumped on a real skin change to force the <img> to reload
  var lastSkin = null; // last background-image URL set, so we don't re-paint it
  function $(id){return document.getElementById(id);}
  function provider(){
    if(window.phantom&&window.phantom.solana) return window.phantom.solana;
    if(window.solana) return window.solana;
    if(window.solflare) return window.solflare;
    return null;
  }
  function render(me){
    signedIn = !!me;
    var root = document.documentElement;
    root.classList.toggle('signed-in', signedIn);
    root.classList.toggle('signed-out', !signedIn);
    // Persist identity so the next navigation can paint the signed-in state
    // synchronously (head bootstrap + cache paint below) instead of flashing
    // the signed-out layout while /me round-trips.
    try{ if(me) localStorage.setItem('ndrchst_me', JSON.stringify(me));
         else localStorage.removeItem('ndrchst_me'); }catch(e){}
    document.querySelectorAll('.client-dl').forEach(function(d){
      d.disabled=false;
      d.textContent = me ? (d.dataset.labelIn||'Download the client')
                         : (d.dataset.labelOut||'Sign in to download'); });
    // Highlight the signed-in player's row + their tier card on the ranks page.
    document.querySelectorAll('.server.me,.tier-card.me').forEach(function(r){r.classList.remove('me');});
    if(!me) return;
    var t=$('wallet-tier');
    if(t){ $('wallet-addr').textContent=me.display;
      t.textContent=me.tier_name||'No rank'; t.className='tier'+(me.tier?'':' none'); }
    // Fill identity placeholders wherever they appear (rankcards on home/play
    // AND the bare line on /ranks) — not only inside .rankcard.
    document.querySelectorAll('.rc-tier').forEach(function(e){e.textContent=me.tier_name||'Not a holder yet';});
    document.querySelectorAll('.rc-pct').forEach(function(e){e.textContent=(me.holdings_pct||0).toFixed(4)+'% of supply';});
    document.querySelectorAll('.rc-name').forEach(function(e){e.textContent=me.mc_name;});
    if(me.mc_name){ var mine=document.querySelector('.server[data-mc="'+
      (window.CSS&&CSS.escape?CSS.escape(me.mc_name):me.mc_name)+'"]');
      if(mine) mine.classList.add('me'); }
    if(me.tier){ var tc=document.querySelector('.tier-card[data-tier="'+
      (window.CSS&&CSS.escape?CSS.escape(me.tier):me.tier)+'"]');
      if(tc) tc.classList.add('me'); }
    // Paint every skin face (the chip avatar + the profile-pop face). Only
    // touch background-image when the URL actually changes — re-setting it each
    // render (the old ?t=Date.now() pattern) reloaded the image and flickered.
    // skinVer busts the cache only after a real change.
    var su = me.skin_url ? (API+me.skin_url+(skinVer?('?v='+skinVer):'')) : '';
    if(su !== lastSkin){
      lastSkin = su;
      document.querySelectorAll('.js-skinface').forEach(function(f){
        f.style.backgroundImage = su ? ('url('+su+')') : '';
        f.classList.toggle('has', !!su);
      });
    }
    var clr=$('skin-clear'); if(clr) clr.style.display = me.skin_url ? '' : 'none';
  }
  function bustSkin(){ skinVer++; refresh(); }  // reload the face after a change
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
  window.ndrchstWallet = {provider:provider, requestSignature:requestSignature,
                          refresh:refresh, bustSkin:bustSkin};
  // Paint the last-known identity from cache right now (this script runs at the
  // end of <body>, so the DOM above exists). The /me fetch below then reconciles
  // — usually a no-op, so the signed-in player sees no flicker on navigation.
  try{ var _cached=JSON.parse(localStorage.getItem('ndrchst_me')||'null'); if(_cached) render(_cached); }catch(e){}
  document.addEventListener('DOMContentLoaded',function(){
    var b=$('wallet-connect'); if(b)b.addEventListener('click',connect);
    document.querySelectorAll('.connect-trigger').forEach(function(c){c.addEventListener('click',connect);});
    // Profile popover: the wallet chip toggles a card with skin + identity.
    var chip=$('wallet-chip'), pop=$('profile-pop');
    function closePop(){ if(pop&&!pop.hidden){ pop.hidden=true; if(chip)chip.setAttribute('aria-expanded','false'); } }
    if(chip) chip.addEventListener('click',function(e){
      e.stopPropagation(); if(!pop) return;
      pop.hidden=!pop.hidden; chip.setAttribute('aria-expanded',String(!pop.hidden)); });
    if(pop) pop.addEventListener('click',function(e){ e.stopPropagation(); });
    document.addEventListener('click',closePop);
    document.addEventListener('keydown',function(e){ if(e.key==='Escape') closePop(); });
    var o=$('wallet-logout'); if(o)o.addEventListener('click',function(){ logout(); closePop(); });
    document.querySelectorAll('.client-dl').forEach(function(d){
      d.addEventListener('click',function(){
        if(!signedIn){ connect(); return; }
        // The play page installs window.ndrchstPlay to hand off to the installed
        // app via a ndrchst:// deep link (with a download fallback). Elsewhere,
        // fall back to the per-server zip download.
        if(window.ndrchstPlay){ window.ndrchstPlay(d.dataset.sid); }
        else { window.location.href = API + '/me/client/' + d.dataset.sid; }
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
        if(r.ok){ if(st)st.textContent='Skin updated.'; bustSkin(); }
        else if(st) st.textContent = r.status===400 ? 'That must be a 64x64 PNG skin.' : 'Upload failed.';
      }catch(e){ if(st)st.textContent='Upload failed.'; }
    });
    var sc=$('skin-clear');
    if(sc) sc.addEventListener('click',async function(){
      try{ await fetch(API+'/me/skin',{method:'DELETE',credentials:'include'});
        if(st)st.textContent='Skin removed.'; bustSkin(); }catch(e){}
    });
    // Find a skin by Minecraft username (Mojang) and apply it with one click.
    var sq=$('skin-q'), sgo=$('skin-q-go'), sres=$('skin-results');
    function meta(t){ sres.innerHTML='<span class="meta">'+t+'</span>'; }
    async function runSearch(){
      var term=sq.value.trim(); if(!term) return;
      meta('Searching…');
      try{
        var r=await fetch(API+'/me/skin/search?q='+encodeURIComponent(term),{credentials:'include'});
        if(!r.ok){ meta('Sign in to search for a skin.'); return; }
        var res=(await r.json()).results||[];
        if(!res.length){ meta('No skin for that username — try The Skindex below.'); return; }
        sres.innerHTML='';
        res.forEach(function(s){
          var card=document.createElement('div'); card.className='skin-pick';
          var face=document.createElement('div'); face.className='face';
          face.style.backgroundImage='url('+API+s.preview_url+')'; card.appendChild(face);
          var nm=document.createElement('div'); nm.className='nm'; nm.textContent=s.name; card.appendChild(nm);
          var use=document.createElement('button'); use.className='btn'; use.textContent='Use';
          use.addEventListener('click',async function(){
            use.disabled=true; use.textContent='…';
            try{
              var im=await fetch(API+'/me/skin/import',{method:'POST',credentials:'include',
                headers:{'content-type':'application/json'},body:JSON.stringify({texture:s.texture})});
              if(im.ok){ if(st)st.textContent='Skin applied from '+s.name+'.'; sres.innerHTML=''; bustSkin(); }
              else { use.disabled=false; use.textContent='Use'; if(st)st.textContent='Could not apply that skin.'; }
            }catch(e){ use.disabled=false; use.textContent='Use'; }
          });
          card.appendChild(use); sres.appendChild(card);
        });
      }catch(e){ meta('Search is unavailable right now.'); }
    }
    if(sq){
      if(sgo) sgo.addEventListener('click',runSearch);
      sq.addEventListener('keydown',function(e){ if(e.key==='Enter'){ e.preventDefault(); runSearch(); }});
    }
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
<style>{css}</style>
<script>try{{document.documentElement.classList.add(localStorage.getItem('ndrchst_me')?'signed-in':'signed-out');}}catch(e){{}}</script>
</head><body><div class="wrap">"""

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


def _profile_pop() -> str:
    """The profile card that drops from the wallet chip — identity + skin
    management (upload / username search / Skindex) + quick links + sign-out.
    Lives in the shared nav so it's reachable from every page; the play page
    and landing no longer carry their own copy of this. All the skin-control
    ids live ONLY here (ids stay unique across the page)."""
    return (
        '<div class="profile-pop" id="profile-pop" hidden>'
        '<div class="pp-head">'
        '<div class="skin-face js-skinface" id="skin-face" title="Your skin"></div>'
        '<div class="pp-id">'
        '<div class="pp-name mono rc-name"></div>'
        '<div class="pp-sub"><span class="rc-tier"></span>'
        '<span class="rc-pct meta"></span></div>'
        "</div></div>"
        '<div class="pp-skin">'
        '<div class="row">'
        '<label class="btn ghost" style="cursor:pointer">Upload skin'
        '<input type="file" id="skin-file" accept="image/png" hidden></label>'
        '<button class="btn ghost" id="skin-clear" style="display:none">Remove</button>'
        "</div>"
        '<div class="meta" id="skin-status">A 64x64 PNG — your face on your profile and in-game.</div>'
        '<div class="skin-search">'
        '<input id="skin-q" type="text" autocomplete="off" spellcheck="false" '
        'placeholder="Find a skin by Minecraft username…">'
        '<button class="btn ghost" id="skin-q-go">Search</button>'
        "</div>"
        '<div class="skin-results" id="skin-results"></div>'
        '<a class="skindex-link" href="https://www.minecraftskins.com/" target="_blank" '
        'rel="noopener">Or browse The Skindex ↗ (download a PNG, then Upload skin)</a>'
        "</div>"
        '<div class="pp-foot">'
        f'<a class="btn" href="{html.escape(_play_url(), quote=True)}">Open client →</a>'
        '<a class="btn ghost" href="/ranks">Where you rank</a>'
        '<button class="btn ghost" id="wallet-logout">Sign out</button>'
        "</div>"
        "</div>"
    )


def _wallet_ctl() -> str:
    """The shared wallet control: connect button (signed-out) + identity chip
    (signed-in) that opens the profile popover. Wrapped so it stays a unit on
    the right and never overlaps the nav links when they wrap."""
    return (
        '<div class="wnav">'
        '<button id="wallet-connect" class="wbtn">Connect Wallet</button>'
        '<div class="wprofile">'
        '<button id="wallet-chip" class="wchip" aria-haspopup="true" aria-expanded="false">'
        '<span class="wchip-face js-skinface" id="chip-face"></span>'
        '<span id="wallet-addr" class="mono"></span>'
        '<span id="wallet-tier" class="tier"></span>'
        "</button>"
        + _profile_pop() +
        "</div>"
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
        '<a href="https://t.me/ndrchst" target="_blank" rel="noopener">Telegram</a>'
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
  <div>
    <a class="cta" href="{play}">Play now →</a>
    <a class="cta ghost" href="/ranks">Explore the ranks</a>
  </div>
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
    bedrock_bridge_port, client_url, config_url}.

    The standalone per-OS executable is the primary path: download it once
    (it self-updates), sign in here, and pressing Play on a server hands off to
    the installed app via a ``ndrchst://`` deep link — with a download fallback
    when the app isn't installed yet."""
    rows = []
    if not servers:
        rows.append('<div class="empty">No servers are online right now.</div>')
    for s in servers:
        cross = (
            f' · bedrock {s["bedrock_bridge_port"]}/udp'
            if s.get("cross_play") and s.get("bedrock_bridge_port") else ""
        )
        st = _status_class(s.get("status", ""))
        sid = html.escape(str(s.get("id", "")), quote=True)
        rows.append(
            '<div class="server">'
            '<div>'
            f'<div class="name">{html.escape(s["name"])}</div>'
            f'<div class="meta mono">Minecraft {html.escape(str(s["version"]))} · port {s["port"]}{cross}</div>'
            '</div>'
            '<div class="right">'
            f'<span class="dot {st}">{html.escape(s.get("status",""))}</span>'
            f'<button class="btn client-dl" data-sid="{sid}" '
            'data-label-in="Play" data-label-out="Sign in to play" '
            'disabled>Sign in to play</button>'
            f'<a class="btn ghost when-in" href="/me/client/{sid}" '
            'title="Download a self-contained client folder for this server">.zip</a>'
            f'<a class="btn ghost" href="{s["config_url"]}">config</a>'
            '</div></div>'
        )

    dl_base = downloads_base.rstrip("/") if downloads_base else ""
    dl_cfg = json.dumps({
        "base": dl_base,
        "assets": {
            "windows": f"{dl_base}/ndrchst-client-windows-x86_64.exe" if dl_base else "",
            "macos": f"{dl_base}/ndrchst-client-macos-arm64" if dl_base else "",
            "linux": f"{dl_base}/ndrchst-client-linux-x86_64" if dl_base else "",
        },
    })

    script = """
(function(){
  var API = window.NDRCHST_API || '';
  window.NDRCHST_DL = __DL__;
  function osKey(){
    var d=(navigator.userAgentData&&navigator.userAgentData.platform)||navigator.platform||'';
    d=String(d).toLowerCase();
    if(d.indexOf('win')>=0) return 'windows';
    if(d.indexOf('mac')>=0||d.indexOf('iphone')>=0||d.indexOf('ipad')>=0) return 'macos';
    return 'linux';
  }
  var OS_LABEL={windows:'Windows',macos:'macOS',linux:'Linux'};
  function exeUrl(){ var a=(window.NDRCHST_DL||{}).assets||{}; return window.NDRCHST_DL.base?(a[osKey()]||''):''; }
  // Primary download button → the visitor's OS binary (a self-updating exe).
  var dlBtn=document.getElementById('client-download');
  if(dlBtn){
    var u=exeUrl();
    if(u){ dlBtn.href=u; dlBtn.textContent='Download for '+OS_LABEL[osKey()]; }
    else { dlBtn.href='#servers'; }   // no published binaries yet → grab a server's .zip
  }
  // Open the installed app via a ndrchst:// deep link; if nothing handles it
  // (app not installed) fall back to the download after a short grace period.
  function openApp(deeplink, fallbackUrl){
    var done=false;
    function cancel(){ done=true; }
    document.addEventListener('visibilitychange',function h(){ if(document.hidden){cancel();document.removeEventListener('visibilitychange',h);} });
    window.addEventListener('blur',function b(){ cancel(); window.removeEventListener('blur',b); });
    setTimeout(function(){ if(!done && !document.hidden && fallbackUrl){ window.location.href=fallbackUrl; } },1500);
    window.location.href=deeplink;
  }
  // Per-server Play: mint a one-time handoff code so the app links instantly,
  // build the deep link, and hand off (download fallback if not installed).
  window.ndrchstPlay=async function(sid){
    var code='';
    try{
      var r=await fetch(API+'/me/handoff',{method:'POST',credentials:'include'});
      if(r.ok){ code=(await r.json()).code||''; }
    }catch(e){}
    var dl='ndrchst://launch?sid='+encodeURIComponent(sid)+(code?('&code='+encodeURIComponent(code)):'');
    openApp(dl, exeUrl()||(API+'/me/client/'+sid));   // exe, else the authed .zip
  };
  // OS tabs for the instructions.
  var def=osKey()==='windows'?'win':(osKey()==='macos'?'mac':'linux');
  function sel(os){
    document.querySelectorAll('.os-tab').forEach(function(t){t.classList.toggle('active',t.dataset.os===os);});
    document.querySelectorAll('.os-panel').forEach(function(p){p.classList.toggle('active',p.dataset.os===os);});
  }
  document.querySelectorAll('.os-tab').forEach(function(t){t.addEventListener('click',function(){sel(t.dataset.os);});});
  sel(def);
})();
""".replace("__DL__", dl_cfg)

    body = f"""
<section class="hero" style="padding:3rem 0 1.5rem">
  <span class="eyebrow">$NDRCHST · play</span>
  <h1 style="font-size:2.1rem">Play on ndrchst</h1>
  <p class="lede">Download the client once — it links to your wallet, installs the modpack,
     joins the server for you, and keeps itself up to date. Sign in here, then press Play.</p>
  <div style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:.5rem 0 0">
    <a id="client-download" class="cta" href="#servers">Download the client</a>
    <span class="when-out"><button class="wbtn connect-trigger"
       style="font-size:.95rem;padding:.55rem 1.1rem">Connect Wallet</button></span>
    <a class="cta ghost" href="/ranks">See the ranks</a>
  </div>
  <p class="when-in" style="color:var(--accent);font-size:.96rem;margin:.6rem 0 0;max-width:38rem">
    Signed in as <span class="mono rc-name" style="color:var(--fg)"></span> ·
    <span class="rc-tier" style="color:var(--fg)"></span> — pick a server below and press
    <strong>Play</strong>. If the client's installed it opens straight away; otherwise it downloads.
    <span class="meta" style="display:block;margin-top:.45rem">Manage your skin &amp; profile from
      your wallet chip, top-right.</span>
  </p>
</section>

<section class="section" id="servers" style="border-top:none;padding-top:.4rem">
  <h2>Servers</h2>
  {''.join(rows)}
  <div class="soon">More servers coming soon</div>
</section>

<section class="section">
  <h2 style="font-size:1.2rem">Run the client</h2>
  <div class="os-tabs">
    <div class="os-tab" data-os="win">Windows</div>
    <div class="os-tab" data-os="mac">macOS</div>
    <div class="os-tab" data-os="linux">Linux</div>
  </div>
  <div class="os-panel" data-os="win">
    <ol>
      <li>Download the client (button above) and run <code>ndrchst-client-windows-x86_64.exe</code>.</li>
      <li>SmartScreen may warn on an unsigned app — choose <em>More info → Run anyway</em>.</li>
      <li>Sign in here and press <strong>Play</strong> on a server — it opens the app.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="mac">
    <ol>
      <li>Download the client and move it to Applications; first run, right-click → <em>Open</em>.</li>
      <li>Sign in with your wallet in the app itself (the website Play hand-off is Windows/Linux for now).</li>
      <li>Press <strong>Play</strong>.</li>
    </ol>
  </div>
  <div class="os-panel" data-os="linux">
    <ol>
      <li>Download the client, then <code>chmod +x ndrchst-client-linux-x86_64 &amp;&amp; ./ndrchst-client-linux-x86_64</code>.</li>
      <li>First launch installs the app + registers the <code>ndrchst://</code> handler.</li>
      <li>Sign in here and press <strong>Play</strong> on a server — it opens the app.</li>
    </ol>
  </div>
  <p class="meta when-in" style="margin-top:1rem">Prefer a self-contained folder? Each server's
    <span class="mono">.zip</span> above bundles the client pinned to that server (Python required).</p>
</section>

<footer>The client is an offline launcher pinned to each server. It mirrors the server's
  mod set from a CDN, so first launch downloads the pack once, and self-updates after. ·
  <a href="{_home_url()}">Home</a> · <a href="/ranks">Ranks</a></footer>

<script>{script}</script>
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


def _amt(d: dict) -> str:
    """Inline count for the transparency table: a single value or a min-max range."""
    lo, hi = d.get("min"), d.get("max")
    if lo is None:
        return "×1"  # noqa: RUF001
    return f"×{lo}" if lo == hi else f"×{lo}–{hi}"  # noqa: RUF001


def _ritem_html(e: dict) -> str:
    """One reward row: icon (or a placeholder), name, amount, exact drop odds.
    Shared by the ranks ladder and the crates page."""
    icon = (
        f'<img class="pixel" src="/game/items/{html.escape(e["icon"], quote=True)}.png" alt="">'
        if e["icon"] else '<span class="ritem-noicon">▩</span>'
    )
    return (
        '<span class="ritem">' + icon
        + f'<span class="rname">{html.escape(e["name"])}</span>'
        + f'<span class="ramt mono">{_amt(e)}</span>'
        + f'<span class="rpct mono">{e["pct"]}%</span></span>'
    )


def _rolls_html(rolls: list[list[dict]]) -> str:
    """The `<div class="rolls">` for a set of weighted rolls (one item per roll,
    odds shown). Used by both the ranks tiers and the crates."""
    blocks = "".join(
        f'<div class="roll"><span class="roll-no">Roll {n}</span>'
        f'<div class="roll-items">{"".join(_ritem_html(e) for e in entries)}</div></div>'
        for n, entries in enumerate(rolls, start=1)
    )
    return f'<div class="rolls">{blocks}</div>'


def render_ranks(holders: list[dict], tiers: list[dict]) -> str:
    """tiers: list of {key, name, min_pct} ascending. holders: list of
    {display, mc_name, tier, tier_name, holdings_pct}.

    The ladder IS the page: each tier is one self-explanatory nested card —
    name + supply band, how many holders sit in it, and a demonstration of its
    real daily crate (item icons + exact odds). The signed-in wallet's own card
    is highlighted client-side. No separate leaderboard: a tier's name already
    says what its holders are, so we show a per-tier count, not a roster."""
    counts: dict[str, int] = {}
    for h in holders:
        if h.get("tier"):
            counts[h["tier"]] = counts.get(h["tier"], 0) + 1

    def _count_label(key: str) -> str:
        n = counts.get(key, 0)
        return f"{n} holder{'' if n == 1 else 's'}" if n else "no holders yet"

    def _demo(key: str, has_lower: bool) -> str:
        """Show, don't tell: the tier's own weighted rolls (icons + odds), then
        compact chips for the additive lower tiers and the treasure pull."""
        rolls = _tier_loot().get(key, [])
        demo = _rolls_html(rolls) if rolls else ""
        chips = []
        if has_lower:
            chips.append('<span class="tc-chip">+ every lower tier, daily</span>')
        treas = _tier_treasure(key)
        if treas:
            chips.append('<span class="tc-chip">+ treasure: '
                         f'{html.escape(treas[-1])}</span>')
        chip_html = f'<div class="tc-chips">{"".join(chips)}</div>' if chips else ""
        return demo + chip_html

    # Ascending so each card sees its band; rendered top-tier first.
    cards = [
        f'<article class="feature tier-card" data-tier="{html.escape(t["key"], quote=True)}">'
        '<div class="tc-head">'
        f'<div class="tc-id"><h3>{html.escape(t["name"])}</h3>'
        f'<span class="thr">{_tier_band(tiers, i)}</span></div>'
        f'<span class="tc-count">{_count_label(t["key"])}</span>'
        "</div>"
        '<div class="tc-you">★ Your rank</div>'
        f'{_demo(t["key"], i > 0)}'
        "</article>"
        for i, t in enumerate(tiers)
    ]
    ladder = "".join(reversed(cards))

    play = html.escape(_play_url(), quote=True)
    body = f"""
<section class="hero" style="padding:3rem 0 1rem">
  <span class="eyebrow">$NDRCHST · ranks</span>
  <h1 style="font-size:2.1rem">Holdings are rank</h1>
  <p class="lede">Your tier is your share of $NDRCHST supply, read from the chain. Each tier opens a
     bigger daily crate — and it's additive, so you also get every tier below.</p>
  <p class="when-in" style="color:var(--accent);font-size:.95rem;margin:.2rem 0 0">
    You're <span class="mono rc-tier" style="color:var(--fg)"></span> ·
    <span class="mono rc-pct"></span> — your card is highlighted below.</p>
  <div class="when-out" style="margin-top:.4rem">
    <a class="cta" href="{play}">Get the client →</a>
    <button class="wbtn connect-trigger" style="margin-left:.5rem">Connect to see your rank</button>
  </div>
</section>

<section class="section" style="border-top:none;padding-top:.2rem">
  <p class="callout" style="margin:0 0 1.3rem;font-size:.9rem">
    Run <code>/daily</code> in-game to open your tier's crate — additive (you also get every lower
    tier) plus a vanilla treasure pull. The icons and odds below are the real loot tables.</p>
  <div class="features ladder detailed">{ladder}</div>
</section>

<footer>Ranks track the chain — buys and sells show on the next refresh. ·
  <a href="{_home_url()}">Home</a> · <a href="{play}">Play</a></footer>
"""
    return _shell("ndrchst — ranks", body, active="ranks")
