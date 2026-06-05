# ndrchst — state of the project

Authoritative current-state doc. Pairs with [CLAUDE.md](CLAUDE.md) (agent
orientation) and [docs/architecture.md](docs/architecture.md) (how the pieces
talk). For deploy/infra, see [deploy/OPS.md](deploy/OPS.md).

## What this is

A single-machine OSS Minecraft server **control plane** plus a **desktop client
distribution** system on one box (Python 3.12+, FastAPI + htmx + SQLite +
docker-py; Java + Bedrock first-class). Three planes:

| Plane | Where | Role |
|---|---|---|
| Control plane | `src/ndrchst/` (`api`, `web`, `runtime`, `:8080`) | run servers in Docker, Modrinth installs, RCON, backups, client-bundle build + R2 publish |
| Desktop client | `client/` | portablemc launcher; server-driven modpack/mod sync, tunnel, self-update; offline or Microsoft login |
| Edge | `cf/worker/` | Cloudflare Worker: static landing/play pages + per-server client artifacts + `servers.json`, served from R2 |

Predecessors:
- `~/code/ndrchst/ndrchst/` — Python v2.3.0 (working but bloated, ~38k LOC)
- `~/code/ndrchst/ndrchst_3/` — abandoned Rust SaaS pivot (do not resurrect)

Scale: ~6k LOC Python (`src/`) + a ~3k-LOC client + a ~100-line Worker; ~300
tests across 4 markers.

## History: Solana token-gating removed

The project briefly carried a Solana wallet token-gating layer (wallet identity,
holdings→tier ranks, snapshot-based daily rewards, SIWS auth, in-game gate mods,
a separate public app on `:8081`, and a dynamic edge proxy). **That entire layer
was removed** (the `strip-solana` work — PRs #1/#2, see git history). What
remains is the control plane + the client/edge distribution path. The client now
launches in offline mode (a username) by default, or with a Microsoft account
for online-mode / vanilla servers (`client/.../online_auth.py`); its modpack/mod
sync from the server was always wallet-free and is preserved intact. The name +
the End/void palette are Minecraft theming (ndrchst = enderchest), not residue.

## Layout

```
src/ndrchst/
  platforms/   per-platform install + version logic (paper, neoforge, bedrock, modpack, vanilla)
  mods/        Modrinth asset source (search + version listing)
  runtime/     docker, rcon, lifecycle, client-bundle build, R2/edge publish, eula
  domain/      pure logic, no network (models, players, worlds, files, properties)
  store/       SQLite, no ORM — servers + installed_assets (schema.sql + db.py)
  api/         JSON API + app factory + lifespan/deps (:8080)
  web/         htmx UI (routes, servers_routes, detail_routes) + templates + static;
               public_pages.py renders the static landing/play pages
  cli.py       `ndrchst run` / `ndrchst doctor`
client/        desktop launcher (sync, launcher, online_auth, tunnel, updater, deeplink)
cf/worker/     Cloudflare edge Worker (static R2 distribution)
deploy/        OPS.md + systemd units
docs/          architecture.md + distribution.md
tests/         pytest; markers: integration, live, docker
```

A finer-grained "where to find things" table lives in [CLAUDE.md](CLAUDE.md).

## Architecture decisions (with rationale)

**Python over Rust.** The SaaS pivot needed Rust; the single-machine OSS reshape doesn't. Iteration speed matters more than perf for a pre-PMF tool. The Rust workspace's *module shape* (per-domain split, typed protocol) was kept even so.

**FastAPI + htmx + Jinja + SQLite, no JS toolchain.** Ships in days, not weeks. htmx replaces the v2 `app.js` orchestrator (6500+ LOC) with a few hundred LOC of templates + attributes. SQLite single-file persistence avoids v2's config-files-everywhere.

**One-route-pattern for HTML.** Same URL (`/servers/{id}/files`) serves either a full chrome page (browser nav) or an htmx fragment (HX-Request header), so refresh/back-button work without route duplication. The detail-page catch-all `/servers/{id}/{tab}` is registered **last** so specific routes match first.

**Docker via docker-py wrapped in `asyncio.to_thread`.** docker-py is sync; the ~250-LOC wrapper makes it awaitable and lets tests inject a `FakeClient`.

**No `itzg/minecraft-server` image.** `platforms/*.install()` owns the install path (version pinning, SHA256 verify, zip-slip guard); the runtime layer is a thin shell that runs what's on disk. Java uses `eclipse-temurin:{21,17}-jre`; Bedrock uses `ubuntu:24.04`.

**EULA written automatically.** Project policy: running ndrchst = agreeing to Mojang's EULA. `runtime/eula.py` writes `eula.txt` (Java) + `permissions.json`/`allowlist.json` (Bedrock) at the end of `lifecycle.create()`.

**Lifespan-owned shared state.** `app.state.ndrchst` holds the SQLite conn, the Lifecycle (None if Docker is unreachable), the shared httpx client, and the cached Modrinth source. `require_lifecycle` returns 503 when Docker is gone, so the read-only UI still works.

**SQLite `check_same_thread=False`.** FastAPI runs sync routes in a worker pool, so the lifespan-created conn must cross threads. SQLite is thread-safe at the C level (SERIALIZED); the Python wrapper just gates it by default.

## What works end-to-end

The control plane is exercised by ~300 unit tests (FakeClient/MockTransport) plus
real-process markers. Headline capabilities, all green:

- App boots with or without Docker (read-only mode + 503 on mutations when Docker is gone).
- Paper / NeoForge / Modpack / Vanilla / Bedrock install (version pin + SHA verify + zip-slip guard).
- Container create/start/stop/delete (Java TCP, Bedrock UDP, cross-play bridge).
- Modrinth search + install to the per-family destination; auto-snapshot before destructive ops.
- RCON async client; players list/dispatch; properties (comment-preserving); files (traversal-safe); worlds (NBT); backups (tar.gz round-trip).
- Client bundle build + R2/edge publish (artifacts + servers.json + the static pages).
- **Real container boot** (Paper + Bedrock) via `-m docker` — verified on the box and on the dev machine (Docker is available now).

Still partial: the WebSocket console backfills logs but live command/stdin dispatch is stubbed (see Deferred).

## Tests

```toml
[tool.pytest.ini_options]
addopts = "-m 'not integration and not live and not docker'"
markers = [
  "integration: live PaperMC/Modrinth/Mojang APIs",
  "live: spawns a real uvicorn process",
  "docker: needs a real Docker daemon",
]
```

Default suite: **312 passed, 16 deselected**. Marker suites: `-m docker` (2, real
Paper+Bedrock boot), `-m integration` (live upstreams), `-m live` (real uvicorn —
note a few cases assume no Docker + an empty default DB; see the dev-env memory
note). The full per-file inventory is just `ls tests/`.

## On-disk state & CLI

| Path | What |
|---|---|
| `~/.ndrchst/ndrchst.db` | SQLite (WAL mode) — `servers`, `installed_assets` |
| `~/.ndrchst/servers/<id>/` | per-server data dir (jar/bin, world, plugins, properties) |
| `~/.ndrchst/backups/<server_id>/<timestamp>.tar.gz` | backups |

CLI: `ndrchst run` (uvicorn on `:8080`, localhost) · `ndrchst doctor` (env preflight).

## Known gotchas

1. **`.gitignore` must not contain bare `servers/`.** It would silently swallow `src/ndrchst/web/templates/servers/` and break the UI on a fresh clone. Already bitten once; there's a warning comment in `.gitignore`.
2. **Mojang BDS feed URL is unofficial.** `net-secondary.web.minecraft-services.net/api/v1.0/download/links` is what the launcher uses. Watch for drift; `-m integration` will catch it.
3. **Mojang feed gates on User-Agent, AND Akamai blocks UAs containing `+URL`.** The default httpx UA gets a 403; `platforms/bedrock.py` sets a minimal `Mozilla/5.0 (ndrchst)`. Do **not** add a `+https://github.com/...` reference to *that* UA — Akamai's bot detection on the BDS zip CDN resets the HTTP/2 stream (surfaces as a 300s ReadTimeout). (The Modrinth/general UA *does* carry the repo URL — Modrinth asks for it; only the BDS CDN is sensitive.)
4. **UDP host-port probe is best-effort.** `runtime/ports.py` uses `SO_REUSEADDR`, which on Linux can let a probe succeed even with a real listener bound. TCP is reliable; UDP isn't.
5. **Catch-all detail route order matters.** `/servers/{id}/{tab}` is registered last in `web/detail_routes.py` so specific routes (`/files`, `/properties`, …) match first. Don't move it.
6. **Java 17 vs 21 cutover at MC 1.20.5.** Hardcoded in `runtime/docker.py:java_image_for()`. A future Java 22+ requirement needs updating here.
7. **BDS 1.21+ needs libcurl4, absent in `ubuntu:24.04`.** The Bedrock cmd in `lifecycle.py:_build_spec` installs it on first boot. Verify libcurl if `BEDROCK_IMAGE` ever changes.
8. **`Docker.create_container` pulls on ImageNotFound.** docker-py's `containers.create()` doesn't pull on miss — the wrapper does, so the first container for a new image pauses for the pull.

## Out of scope (explicit cuts, not gaps)

- Auth / multi-user / RBAC / 2FA (admin plane is private-network only)
- Scheduler / cron · UPnP / DDNS / SSH tunnels · plugin diagnostics
- Cloud / SaaS features (killed with the Rust pivot)
- Spiget + Hangar mod sources (Modrinth covers ~80% of demand)
- Bedrock LevelDB world editing (read paths only; high effort, low value)
- Cross-platform server host: Linux-first (macOS soon, Windows via WSL)

## Deferred

1. **WebSocket console live dispatch** — backfill works; RCON/stdin command dispatch into the running container is stubbed (the box has real Docker now, so this is wireable).
2. **Async-uniform routes** — currently mixed sync/async; works but fragile.
3. **Microsoft online-mode login** — implemented (`client/.../online_auth.py`, portablemc's flow) but not yet smoke-tested with a live account.

## How to pick up

1. `.venv/bin/python -m pytest -q` — ~312 passed, 16 deselected. (Venv shebangs may be stale after a Python upgrade/move; invoke via `python -m`.)
2. `.venv/bin/python -m pytest -q -m docker` — real Paper/Bedrock container boot (Docker is available on the box now).
3. `.venv/bin/python -m pytest -q -m integration` / `-m live` — real upstreams / real uvicorn.
4. `.venv/bin/ndrchst run` → <http://localhost:8080>; `ndrchst doctor` for preflight.
5. Read this file + [CLAUDE.md](CLAUDE.md) for orientation; `git log --oneline` for history.
