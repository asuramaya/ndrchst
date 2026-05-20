// ndrchst edge Worker — serves the public surface from the R2 bucket so
// the residential box stays out of the per-client hot path.
//
// URL → R2 key mapping:
//   GET /            → play.html (on play.*) | index.html (otherwise)
//   GET /play        → play.html
//   GET /pilot/<...> → pilot/<...>   (config, manifest, modpack.zip,
//                                     mods/index.json, mods/<jar>, pilot.zip)
//   GET /<other>     → <other>       (servers.json, index.html, …)
//   GET /healthz     → "ok"
//
// Object Content-Type / Cache-Control come from what the box stored on
// upload (writeHttpMetadata), so the box controls freshness centrally.

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const url = new URL(request.url);
    const host = url.hostname;
    const path = url.pathname;

    if (path === "/healthz") {
      return new Response("ok", { headers: { "content-type": "text/plain" } });
    }

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
