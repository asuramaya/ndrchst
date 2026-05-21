// ndrchst edge Worker — serves the public surface from the R2 bucket so
// the residential box stays out of the per-client hot path, and proxies the
// handful of DYNAMIC endpoints (wallet auth + client pairing) back to the box
// origin, which can't be static.
//
// The whole surface lives on ONE host (play.ndrchst.com); apex + www 301 here
// (see fetch()), so every route below is served from that single origin.
//
// STATIC (from R2 bucket DL):
//   GET /            → index.html    (the marketing landing)
//   GET /play        → play.html     (the player / app page)
//   GET /client/<sid>/<...> → client/<sid>/<...>   (config, manifest, client.zip,
//                                                 mods/index.json, mods/<jar>)
//   GET /<other>     → <other>       (servers.json, …)
//   GET /healthz     → "ok"
//
// DYNAMIC (proxied to env.ORIGIN_BASE, any method incl. POST):
//   /auth/challenge  /auth/verify  /auth/logout   (Sign-In-With-Solana)
//   /me                                            (session check)
//   /client/auth/start  /client/auth/approve  /client/auth/poll  (device pairing)
//   /link                                          (client pairing approval page)
//   /ranks                                         (live holders leaderboard)
//
// The box sets the session cookie with path=/ and NO Domain attribute. With a
// single host that's exactly right: the cookie scopes to play.ndrchst.com and
// never has to follow the user across origins. Proxying is transparent —
// Set-Cookie flows straight back through to the one host.
//
// Object Content-Type / Cache-Control come from what the box stored on
// upload (writeHttpMetadata), so the box controls freshness centrally.

// Distinct from the static /client/<sid>/* artifacts: a hex server id is never
// "auth", so /client/auth/* can't collide with a real server's client bundle.
function isDynamic(path) {
  return (
    path === "/me" ||
    path === "/link" ||
    path === "/ranks" ||
    path.startsWith("/me/") ||        // /me/client/<sid> download, /me/skin
    path.startsWith("/skins/") ||     // per-wallet profile skins (box-stored)
    path.startsWith("/device/") ||    // /device/exchange
    path.startsWith("/auth/") ||
    path.startsWith("/client/auth/")
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // One canonical host: play.ndrchst.com. The app (wallet session + client
    // pairing) lives here and the session cookie is host-scoped (no Domain
    // attr), so keeping the WHOLE surface — landing included — on this single
    // origin is what stops sign-in from stranding when a player moves between
    // the landing and the app. apex + www therefore just 301 here, preserving
    // path and query (so e.g. www.ndrchst.com/play → play.ndrchst.com/play).
    if (url.hostname === "ndrchst.com" || url.hostname === "www.ndrchst.com") {
      return Response.redirect("https://play.ndrchst.com" + path + url.search, 301);
    }

    if (path === "/healthz") {
      return new Response("ok", { headers: { "content-type": "text/plain" } });
    }

    // Dynamic endpoints → proxy to the box origin (the box's own tunnel
    // hostname, set as ORIGIN_BASE; NOT play/www, which now hit this Worker).
    if (isDynamic(path)) {
      if (!env.ORIGIN_BASE) return originDown(env, request);
      const target = env.ORIGIN_BASE.replace(/\/+$/, "") + path + url.search;
      try {
        // Preserve method, headers (Cookie, Content-Type, …) and body.
        const resp = await fetch(new Request(target, request));
        // Gateway-class failures mean the box is down → maintenance fallback.
        if (resp.status >= 502 && resp.status <= 504) return originDown(env, request);
        return resp;
      } catch (e) {
        return originDown(env, request);
      }
    }

    // Everything else is a static asset from R2 — read-only.
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    let key;
    if (path === "/") {
      key = "index.html";   // root of the one host is the marketing landing
    } else if (path === "/play") {
      key = "play.html";    // the player / app page
    } else if (path === "/favicon.ico") {
      key = "game/favicon.png";   // browsers auto-request /favicon.ico
    } else {
      key = decodeURIComponent(path.replace(/^\/+/, ""));
    }
    if (!key) return notFound();

    const range = request.headers.get("range");
    const obj = await env.DL.get(key, range ? { range: parseRange(range) } : undefined);
    if (!obj) return notFound();

    const headers = new Headers();
    obj.writeHttpMetadata(headers);
    headers.set("etag", obj.httpEtag);
    if (!headers.has("cache-control")) headers.set("cache-control", "no-cache");

    if (range && obj.range) {
      const len = obj.size;
      const start = obj.range.offset ?? 0;
      const end = start + (obj.range.length ?? len - start) - 1;
      headers.set("content-range", `bytes ${start}-${end}/${len}`);
      headers.set("accept-ranges", "bytes");
      return new Response(request.method === "HEAD" ? null : obj.body, {
        status: 206,
        headers,
      });
    }
    return new Response(request.method === "HEAD" ? null : obj.body, { headers });
  },
};

// Origin (the box) is unreachable. For a top-level HTML navigation, fall back to
// the published maintenance page so downtime still looks like ndrchst; for API
// callers (XHR/fetch expecting JSON), return a terse 503 they can handle.
async function originDown(env, request) {
  const accept = request.headers.get("accept") || "";
  if (request.method === "GET" && accept.includes("text/html")) {
    const obj = await env.DL.get("maintenance.html");
    if (obj) {
      const headers = new Headers();
      obj.writeHttpMetadata(headers);
      headers.set("cache-control", "no-store");
      headers.set("retry-after", "30");
      return new Response(obj.body, { status: 503, headers });
    }
  }
  return new Response("ndrchst is temporarily unavailable", {
    status: 503,
    headers: { "content-type": "text/plain", "retry-after": "30" },
  });
}

function notFound() {
  const body = `<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>404 — ndrchst</title>
<style>html{height:100%}body{margin:0;min-height:100%;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:1.1rem;text-align:center;padding:2rem;
background:#0a0613;color:#f4f0ff;font-family:'Space Grotesk',system-ui,sans-serif}
h1{font-size:5rem;margin:0;color:#14f195;letter-spacing:.05em}p{margin:0;color:#a99fc7}
a{color:#f4f0ff;text-decoration:none;border:1px solid #2a2150;padding:.6rem 1.2rem;
border-radius:.6rem}a:hover{border-color:#14f195}</style>
<h1>404</h1><p>This page drifted into the End.</p><a href="/">Back to ndrchst</a>`;
  return new Response(body, {
    status: 404,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function parseRange(header) {
  // Single "bytes=start-end" range → R2 get options.
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m) return undefined;
  const [, s, e] = m;
  if (s === "" && e === "") return undefined;
  if (s === "") return { suffix: Number(e) };
  const opt = { offset: Number(s) };
  if (e !== "") opt.length = Number(e) - Number(s) + 1;
  return opt;
}
