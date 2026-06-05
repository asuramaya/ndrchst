# ndrchst — state of the project

Authoritative current-state doc. Originally written 2026-05-18 (v0 close + the
v0.1 sweep); the lower sections still describe that control-plane foundation
accurately. This top section reflects where the project actually is now.

## What this is

A single-machine OSS Minecraft server **control plane** plus a **desktop client
distribution** system on one box (Python 3.12+, FastAPI + htmx + SQLite +
docker-py; Java + Bedrock first-class). Three planes. See
[docs/architecture.md](docs/architecture.md) for the flows.

| Plane | Where | Role |
|---|---|---|
| Control plane | `src/ndrchst/` (`api`, `web`, `runtime`, `:8080`) | run servers in Docker, Modrinth installs, RCON, backups, client-bundle build + R2 publish |
| Desktop client | `client/` | portablemc launcher; server-driven modpack/mod sync, tunnel, self-update; offline or Microsoft login |
| Edge | `cf/worker/` | Cloudflare Worker: static landing/play pages + per-server client artifacts + `servers.json`, served from R2 |

Predecessors:
- `~/code/ndrchst/ndrchst/` — Python v2.3.0 (working but bloated, ~38k LOC)
- `~/code/ndrchst/ndrchst_3/` — abandoned Rust SaaS pivot (do not resurrect)

Scale now: ~6k LOC Python (`src/`) + a ~3k-LOC client + a ~100-line Worker; ~300
tests. The v0.1 numbers below (~7k LOC, 162 tests) are a historical baseline.

## Solana token-gating removed

The project briefly carried a Solana wallet token-gating layer (wallet identity,
holdings→tier ranks, snapshot-based daily rewards, SIWS auth, in-game gate mods,
a separate public app on `:8081`, and a dynamic edge proxy). **That entire layer
was removed** — see git history for the `strip-solana` work. What remains is the
single-machine control plane below plus the client/edge distribution path. The
client now launches in offline mode (a username) by default, or with a Microsoft
account for online-mode / vanilla servers (`client/.../online_auth.py`); its
modpack/mod sync from the server was always wallet-free and is preserved intact.

Operational runbook for the hosted deployment: [deploy/OPS.md](deploy/OPS.md).

---

## Control-plane foundation (v0.1 — still current)

The remainder of this doc describes the control plane as it stood at the v0.1
close. It remains accurate for that layer.

## Layout

```
ndrchst/
├── pyproject.toml              uv-shaped (works with pip too); ruff + pytest + nbtlib + httpx
├── README.md                   user-facing one-pager
├── STATE.md                    this file — authoritative architecture/state doc
├── CLAUDE.md                   orientation for future Claude sessions
├── LICENSE                     MIT
├── .gitignore                  ⚠ NEVER add bare `servers/` (would swallow templates)
├── src/ndrchst/
│   ├── __init__.py             __version__
│   ├── cli.py                  typer: `ndrchst run`, `ndrchst doctor`
│   ├── logging_setup.py        idempotent JSON logger + optional rotating file (env-driven)
│   ├── doctor.py               env preflight: Python, docker-py, daemon, group, disk, port + per-server UDP/TCP
│   ├── platforms/              install + version resolution per platform
│   │   ├── base.py             Platform protocol + Family enum + InstallArtifact dataclass
│   │   ├── paper.py            PaperMC v2 API + SHA256 verify
│   │   ├── purpur.py           stub (NotImplementedError; v1)
│   │   ├── vanilla.py          stub
│   │   ├── fabric.py           stub
│   │   ├── forge.py            stub
│   │   ├── neoforge.py         stub
│   │   └── bedrock.py          Mojang BDS feed + zip slip guard
│   ├── mods/                   asset sources (search + version listing)
│   │   ├── base.py             Source protocol + Asset/AssetKind
│   │   └── modrinth.py         api.modrinth.com v2; typed facets; SHA1 surfaced
│   ├── runtime/                actual execution layer
│   │   ├── docker.py           bollard-free; asyncio.to_thread around docker-py
│   │   ├── rcon.py             async; AuthError; fragmentation handling
│   │   ├── lifecycle.py        composes platform install + docker + store
│   │   ├── eula.py             auto-write EULA + bookkeeping (per user policy)
│   │   ├── geyser.py           Geyser+Floodgate auto-install for cross-play
│   │   ├── installer.py        Modrinth mod/plugin/pack install + DB record
│   │   ├── backup.py           tar.gz create/list/restore/delete + auto-snapshot before destructive ops
│   │   └── ports.py            host-level bind probe (TCP/UDP per family)
│   ├── domain/                 platform-agnostic features
│   │   ├── models.py           Server dataclass, ServerStatus enum
│   │   ├── players.py          RCON command formatters + /list parser
│   │   ├── worlds.py           Java NBT read/write (nbtlib)
│   │   ├── files.py            traversal-safe browser
│   │   └── properties.py       server.properties read/write w/ comment preservation
│   ├── store/                  SQLite (no ORM)
│   │   ├── db.py               connect() + WAL + check_same_thread=False
│   │   ├── schema.sql          servers + installed_assets
│   │   └── servers.py          CRUD
│   ├── api/                    JSON API + app factory
│   │   ├── main.py             create_app() with lifespan
│   │   ├── deps.py             AppState + Depends helpers + lifespan
│   │   ├── servers.py          /api/servers JSON
│   │   ├── platforms.py        /api/platforms JSON
│   │   └── mods.py             /api/mods/sources JSON
│   └── web/                    htmx-driven UI
│       ├── routes.py           HTML routes + /assets (global view) + placeholders for system/settings
│       ├── servers_routes.py   /servers list/create/start/stop/delete
│       ├── detail_routes.py    /servers/{id}/{tab} + per-tab handlers + WS console
│       ├── static/app.css      design tokens (from v2) + fresh components
│       └── templates/          Jinja
│           ├── base.html       sidebar + header + main shell
│           ├── index.html      (legacy; unused)
│           ├── placeholder.html
│           ├── assets.html     global installed-assets view
│           └── servers/        all server UI; ⚠ MUST be tracked in git
│               ├── list.html / detail.html
│               ├── _card.html / _grid.html / _create_form.html
│               └── tabs/       7 tab partials + 4 sub-partials
└── tests/                      pytest, async mode auto, 4 markers
    ├── scenario.py             realistic Paper-shaped data dir builder
    ├── test_smoke.py           healthz + platforms registry
    ├── test_rcon.py            in-process fake RCON server; 3 stress paths
    ├── test_paper.py           httpx MockTransport; SHA256 mismatch cleanup
    ├── test_bedrock.py         zip-slip guard, schema-drift detection
    ├── test_modrinth.py        facet construction; primary-file selection
    ├── test_docker_runtime.py  FakeClient; status mapping; stats parser
    ├── test_lifecycle.py       full create/start/stop/delete (Java + Bedrock)
    ├── test_geyser.py          install + lifecycle integration
    ├── test_store.py           SQLite round-trip
    ├── test_eula.py            Java eula.txt + Bedrock bookkeeping
    ├── test_installer.py       mod install per-family destinations
    ├── test_ports.py           host bind probe
    ├── test_doctor.py          env checks
    ├── test_files.py           traversal protection
    ├── test_properties.py      comment-preserving edit
    ├── test_players.py         RCON command formatting + /list parsing
    ├── test_backup.py          create/list/restore/delete round-trip
    ├── test_worlds.py          NBT parsing including Long.MIN_VALUE seed
    ├── test_web_servers.py     TestClient — index, create, htmx round-trip
    ├── test_web_detail.py      TestClient — every tab, every mutation
    ├── test_scenario_end_to_end.py  realistic seeded scenario
    ├── test_integration_upstream.py  -m integration; live PaperMC/Mojang/Modrinth
    ├── test_live_stress.py     -m live; real uvicorn + curl every route
    └── test_real_docker.py     -m docker; real Paper/Bedrock container boot
```

## Architecture decisions (with rationale)

**Python over Rust.** The SaaS pivot needed Rust; the single-machine OSS reshape doesn't. Iteration speed matters more than perf for a pre-PMF tool. The Rust workspace's *module shape* (per-domain split, typed protocol) was stolen even so.

**FastAPI + htmx + Jinja + SQLite, no JS toolchain.** Ships in days, not weeks. htmx replaces 6500+ LOC of the v2 `app.js` orchestrator with ~280 LOC of templates + attributes. SQLite single-file persistence avoids the JSON-config-files-everywhere of v2.

**One-route-pattern for HTML.** Same URL (`/servers/{id}/files`) serves either full chrome page (browser nav) or htmx fragment (HX-Request header). Lets refresh/back-button work without route duplication. The detail-page catch-all `/servers/{id}/{tab}` is registered **last** so specific routes match first — `/servers/{id}/files?path=...` flows through the specific handler.

**Docker via docker-py wrapped in `asyncio.to_thread`.** Pure async would need an async docker client; docker-py is sync. The wrapper is ~250 LOC and lets us inject a `FakeClient` for tests.

**No `itzg/minecraft-server` image.** Our `platforms/*.install()` owns the install path (version pinning, SHA256 verify, zip-slip guard). The runtime layer is a thin shell that runs what's already on disk. Java uses `eclipse-temurin:21-jre` (1.20.5+) or `:17-jre`. Bedrock uses `ubuntu:24.04` (BDS is native ELF, only needs glibc).

**EULA written automatically.** Project policy per user: running ndrchst = agreeing to Mojang's EULA. `runtime/eula.py` writes `eula.txt` for Java + `permissions.json`/`allowlist.json` for Bedrock at the end of `lifecycle.create()`.

**Two enums made `StrEnum` (Python 3.12+).** `Family`, `AssetKind`, `ServerStatus`. Ruff UP042 cleanup; gives `.value` for free.

**Lifespan-owned shared state.** `app.state.ndrchst` holds: SQLite conn, Lifecycle (None if Docker unreachable), shared httpx client, cached Modrinth source. Lifespan creates and tears them all down. `require_lifecycle` dep returns 503 when Docker is gone, letting the UI work in read-only mode.

**SQLite `check_same_thread=False`.** FastAPI dispatches sync routes to a worker thread pool. The lifespan-created conn has to cross threads. SQLite itself is thread-safe at the C level (SERIALIZED mode); the Python wrapper just gates it by default.

## What works end-to-end

| Capability | Verified by | Status |
|---|---|---|
| App boots without Docker → banner + read-only mode | `test_web_servers.py` + live curl | ✅ |
| App boots with Docker → full create flow | `test_lifecycle.py` (FakeClient) | ✅ |
| Paper install via PaperMC API + SHA256 verify | `test_paper.py` + `test_integration_upstream.py` | ✅ |
| Bedrock install via Mojang BDS feed + zip extract | `test_bedrock.py` + `test_integration_upstream.py` | ✅ |
| Modrinth search + version listing + SHA1 | `test_modrinth.py` + `test_integration_upstream.py` | ✅ |
| RCON async client w/ fragmentation + auth error | `test_rcon.py` (in-process fake server) | ✅ |
| Docker container create/start/stop/delete (Java + Bedrock, TCP/UDP) | `test_docker_runtime.py` | ✅ |
| Lifecycle: validation + dup port + host port probe + cross-play | `test_lifecycle.py` | ✅ |
| Geyser+Floodgate auto-install | `test_geyser.py` | ✅ |
| EULA + bookkeeping written at create | `test_eula.py` + lifecycle integration | ✅ |
| Mod installer (Modrinth → per-family destination) | `test_installer.py` | ✅ |
| Worlds NBT read + game rule edit | `test_worlds.py` + `test_scenario_end_to_end.py` | ✅ |
| Files browser, traversal-safe, in-place edit | `test_files.py` + `test_web_detail.py` | ✅ |
| Properties editor preserves comments | `test_properties.py` | ✅ |
| Players RCON dispatch + /list parser | `test_players.py` | ✅ |
| Backup tar.gz create/list/restore/delete | `test_backup.py` | ✅ |
| WebSocket console (log backfill works; command dispatch stubbed — see v1) | manual + `test_web_detail.py` | 🟡 |
| Auto-snapshot before destructive ops (mod install, restore) | `test_backup.py` + `test_web_detail.py` | ✅ |
| Global installed-assets view (`/assets`) | `test_web_servers.py` | ✅ |
| Doctor probes registered server ports with right protocol (UDP/TCP) | `test_doctor.py` | ✅ |
| Structured JSON logging + rotating file (env-configurable) | `test_logging_setup.py` + live boot | ✅ |
| Real container boot (Paper + Bedrock) | `-m docker` gated; no daemon on dev machine | ⏭ |

## Test markers

```toml
[tool.pytest.ini_options]
addopts = "-m 'not integration and not live and not docker'"
markers = [
  "integration: live PaperMC/Modrinth/Mojang APIs (run with -m integration)",
  "live: spawns real uvicorn (run with -m live)",
  "docker: needs a real Docker daemon (run with -m docker)",
]
```

Counts as of 2026-05-19 (post v0.1 sweep + real-Docker bring-up on the box):
- default: 150 unit tests (+28: doctor UDP, safety snapshot, global assets, JSON logging, image auto-pull, no-Docker determinism)
- `-m live`: 9 (real uvicorn + curl every route + HTML structure assertions)
- `-m integration`: 5 (live upstream APIs)
- `-m docker`: 2 (Paper + Bedrock real container boot — passes on the box, gated on dev machine)

Total: **166 tests**, all passing. Lint clean.

## Defaults + on-disk state

| Path | What |
|---|---|
| `~/.ndrchst/ndrchst.db` | SQLite (WAL mode) |
| `~/.ndrchst/servers/<id>/` | per-server data dir (jar/bin, world, plugins, properties, etc.) |
| `~/.ndrchst/backups/<server_id>/<timestamp>.tar.gz` | backups |

CLI: `ndrchst run` (uvicorn on `:8080`, localhost), `ndrchst doctor`.

## Known gotchas

1. **`.gitignore` must not contain bare `servers/`.** Would silently swallow `src/ndrchst/web/templates/servers/` and break the UI on fresh clone. Already bitten once; comment in `.gitignore` warns explicitly.
2. **Mojang BDS feed URL is unofficial.** `net-secondary.web.minecraft-services.net/api/v1.0/download/links` is what the launcher uses. Watch for drift; `-m integration` will catch it.
3. **Mojang feed gates on User-Agent, AND Akamai blocks UAs containing `+URL` references.** The default httpx UA gets a 403 from the feed. We set a Mozilla-shaped UA in `platforms/bedrock.py`. But: do NOT include a `+https://github.com/...` reference URL in the UA — Akamai's bot detection on the BDS zip CDN treats that pattern as a bot and resets the HTTP/2 stream with INTERNAL_ERROR, which surfaces as a 300s httpx ReadTimeout. Keep the UA minimal: `Mozilla/5.0 (ndrchst)`.
4. **No host UDP port probe is reliable cross-OS.** Our `runtime/ports.py` probe uses `SO_REUSEADDR` which on Linux may let a probe succeed even when a real listener is bound. TCP is the common case; UDP detection is best-effort.
5. **Catch-all detail route order matters.** `/servers/{id}/{tab}` is registered last in `web/detail_routes.py` so specific routes (`/files`, `/properties`, etc.) match first. Don't move it.
6. **Java 17 vs 21 cutover at Minecraft 1.20.5.** Hardcoded in `runtime/docker.py:java_image_for()`. If Mojang requires Java 22+ in some future MC release, this needs updating.
7. **BDS 1.21+ links against libcurl4 which is not in ubuntu:24.04 by default.** The Bedrock cmd in `runtime/lifecycle.py:_build_spec` wraps `./bedrock_server` in `sh -c 'command -v curl >/dev/null || apt install -y libcurl4 && exec ./bedrock_server'`. If we ever switch BEDROCK_IMAGE off ubuntu:24.04, verify libcurl is present in the replacement.
8. **`Docker.create_container` pulls on ImageNotFound.** docker-py's `containers.create()` doesn't pull on miss — the wrapper does. First container for any new image will pause for the pull.

## v1 deferred items

Tracked in detail at `~/.claude/projects/-home-asuramaya-code-ndrchst/memory/project_v1_deferred.md`. Summary of what remains after the v0.1 sweep + the box bring-up:

1. WebSocket console RCON/stdin live-container dispatch (currently stubbed; the box now has real Docker — could be wired now)
2. Async-uniform routes (currently mixed sync/async, works but fragile)
3. Bedrock LevelDB world support (significant effort, low v0 value)

Real Docker boot is now ✅ on the box (Ubuntu 26.04, Docker 29.5.1, kernel 7.0). Paper + Bedrock both verified end-to-end via `-m docker`.

Completed in v0.1 (2026-05-18, this sweep):
- Doctor probes registered server ports with right protocol (UDP for Bedrock, TCP for Java)
- Auto-snapshot before mod install + backup restore (rotation keeps 5; user backups never trimmed)
- Global `/assets` view groups installed mods/plugins/packs by server
- Structured JSON logging via `logging_setup.configure()`, env-driven (`NDRCHST_LOG_*`), idempotent

## Modded-Java pivot (2026-05-19)

Direction shift: product narrows to vertically-integrated modded Java, as
foundation for crypto integration. Bedrock stays in the codebase (hidden
behind `default_visible=False`) so it can be OSS'd or re-enabled later;
just not surfaced in the create form.

New platforms shipped this sweep:

- **NeoForge** — full implementation. Versions come from Maven; install
  downloads the per-version installer.jar and runs it via a one-shot
  `eclipse-temurin:21-jdk` container. Container cmd at boot is
  `bash run.sh nogui`; memory + user JVM flags ride on
  `JAVA_TOOL_OPTIONS` because we can't intercept run.sh's @-args files.
  Verified end-to-end on the box: install in ~16s, boot `Done (2.053s)`,
  RCON `list` / `seed` / `time query daytime` all green.

- **Modpack** — install from a server-pack zip URL. Streams the download
  (cap 4 GiB), validates it's actually a zip (CurseForge sometimes returns
  HTML interstitials), unzips with zip-slip guard, runs whichever bundled
  installer.jar produces `run.sh`. ATM10-class packs are the target.

- Per-platform `default_memory_mb` so the create form pre-fills 8192 for
  NeoForge / Modpack instead of the Paper-sized 2048.

Gotcha captured: JDK-image installers run as root by default and the
output ends up root-owned, unwritable by the admin user. Fix in
`runtime/jvm_installer.py` is to pass `user=$(id -u):$(id -g)` to
docker.containers.run.

Open: real ATM10 boot test pending — needs the server-pack zip URL.
CurseForge gates direct downloads behind their (free) API key. Either:
get a CF API key and call `/v1/mods/{id}/files/{fileId}/download-url`,
or fetch the URL manually from the CurseForge website once and pass it
to the modpack platform.

## Out of scope (explicit cuts, not gaps)

- Auth / multi-user / RBAC / 2FA
- Scheduler / cron
- Auto-update of the binary
- UPnP / DDNS / SSH tunnels
- Plugin diagnostics / safe-mode
- Cloud features (killed with the SaaS pivot)
- Spiget + Hangar mod sources (Modrinth covers ~80% of demand)
- Cross-platform: Linux-first; macOS soon; Windows via WSL or never

## How to pick up

1. `.venv/bin/python -m pytest -q` — ~312 passed, 16 deselected (the venv shebangs
   may be stale after a Python upgrade/move; invoke via `python -m`).
2. `.venv/bin/python -m pytest -q -m live` — real uvicorn (some cases assume no
   Docker + an empty default DB; see the dev-env memory note).
3. `.venv/bin/python -m pytest -q -m integration` — real upstream APIs.
4. `.venv/bin/python -m pytest -q -m docker` — real containers (Docker is now
   available on the box).
5. `.venv/bin/ndrchst run` — open <http://localhost:8080>; `ndrchst doctor` for preflight.
6. Read `STATE.md` (this file) + `CLAUDE.md` for orientation
7. Check `git log --oneline` for commit history (8 commits)
