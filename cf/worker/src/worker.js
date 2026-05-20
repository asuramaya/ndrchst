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
    path.startsWith("/auth/") ||
    path.startsWith("/pilot/auth/")
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

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
  return new Response("Not found", { status: 404, headers: { "content-type": "text/plain" } });
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
