// ndrchst edge Worker — serves the public surface from the R2 bucket so
// the residential box stays out of the per-client hot path, and proxies the
// handful of DYNAMIC endpoints (wallet auth + pilot pairing) back to the box
// origin, which can't be static.
//
// STATIC (from R2 bucket DL):
//   GET /            → play.html (on play.*) | index.html (otherwise)
//   GET /play        → play.html
//   GET /pilot/<sid>/<...> → pilot/<sid>/<...>   (config, manifest, pilot.zip,
//                                                 mods/index.json, mods/<jar>)
//   GET /<other>     → <other>       (servers.json, index.html, …)
//   GET /healthz     → "ok"
//
// DYNAMIC (proxied to env.ORIGIN_BASE, any method incl. POST):
//   /auth/challenge  /auth/verify  /auth/logout   (Sign-In-With-Solana)
//   /me                                            (session check)
//   /pilot/auth/start  /pilot/auth/approve  /pilot/auth/poll  (device pairing)
//   /link                                          (pilot pairing approval page)
//   /ranks                                         (live holders leaderboard)
//
// The box sets the session cookie with path=/ and NO Domain attribute, so the
// browser scopes it to whatever host is in its address bar (play/www) — which
// is the Worker. Proxying is therefore transparent: Set-Cookie flows straight
// back through and lands on the right host.
//
// Object Content-Type / Cache-Control come from what the box stored on
// upload (writeHttpMetadata), so the box controls freshness centrally.

// Distinct from the static /pilot/<sid>/* artifacts: a hex server id is never
// "auth", so /pilot/auth/* can't collide with a real server's pilot bundle.
function isDynamic(path) {
  return (
    path === "/me" ||
    path === "/link" ||
    path === "/ranks" ||
    path.startsWith("/me/") ||        // /me/pilot/<sid> personalized download
    path.startsWith("/device/") ||    // /device/exchange
    path.startsWith("/auth/") ||
    path.startsWith("/pilot/auth/")
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Canonical marketing host is the apex (ndrchst.com); www 301s to it so we
    // don't serve duplicate landings. play.ndrchst.com stays the app + the
    // pilot's API/cookie host (never redirected).
    if (url.hostname === "www.ndrchst.com") {
      return Response.redirect("https://ndrchst.com" + path + url.search, 301);
    }

    // The app (wallet session + pilot pairing) is canonical on the play host.
    // The session cookie has no Domain attr, so reaching the app via the apex
    // marketing host would scope it to the wrong origin — send the app entry to
    // the canonical host so a player who arrives via the landing stays signed in.
    if (url.hostname === "ndrchst.com" && path === "/play") {
      return Response.redirect("https://play.ndrchst.com/play" + url.search, 301);
    }

    if (path === "/healthz") {
      return new Response("ok", { headers: { "content-type": "text/plain" } });
    }

    // Dynamic endpoints → proxy to the box origin (the box's own tunnel
    // hostname, set as ORIGIN_BASE; NOT play/www, which now hit this Worker).
    if (isDynamic(path)) {
      if (!env.ORIGIN_BASE) {
        return new Response("origin not configured", { status: 503 });
      }
      const target = env.ORIGIN_BASE.replace(/\/+$/, "") + path + url.search;
      // Preserve method, headers (Cookie, Content-Type, …) and body.
      return fetch(new Request(target, request));
    }

    // Everything else is a static asset from R2 — read-only.
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const host = url.hostname;
    let key;
    if (path === "/") {
      key = host.startsWith("play.") ? "play.html" : "index.html";
    } else if (path === "/play") {
      key = "play.html";
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
