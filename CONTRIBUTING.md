# Contributing

Thanks for looking. ndrchst is a small, opinionated codebase — these notes save
you from re-deriving the conventions.

## Setup

```bash
uv sync                 # or: python -m venv .venv && pip install -e '.[dev]'
.venv/bin/ndrchst run   # http://localhost:8080
.venv/bin/ndrchst doctor
```

Python 3.12+. Docker is needed to actually run game servers, but **not** to run
most of the test suite — see the markers below.

## Tests

```bash
.venv/bin/pytest -q                  # default suite — no network, no Docker
.venv/bin/pytest -q -m live          # spawns a real uvicorn, curls every route
.venv/bin/pytest -q -m integration   # hits live PaperMC / Modrinth / Mojang APIs
.venv/bin/pytest -q -m docker        # needs a real Docker daemon (CI / a Docker host)
.venv/bin/ruff check src tests       # lint — keep it clean
```

The default run mocks the network (httpx `MockTransport`) and Docker (a
`FakeClient`, reused across tests). Anything touching a real daemon or a live
upstream sits behind a marker and is deselected by default. Don't add network or
Docker to the default suite.

## How the app is shaped

Three planes — read [docs/architecture.md](docs/architecture.md) first. The
Python control plane lives in `src/ndrchst/`:

- `platforms/` — install + version resolution per server type, behind one
  `Platform` protocol.
- `runtime/` — everything that touches Docker, RCON, client bundles, or R2.
- `domain/` — pure logic, no network (NBT, properties, players, worlds).
- `store/` — SQLite, no ORM, additive `ALTER TABLE` migrations.
- `web/` + `api/` — the htmx admin UI and the JSON API.

The desktop launcher is a separate package in `client/`.

## Adding a feature

The pattern that's held for every feature so far:

1. Pure logic in `domain/<feature>.py` (or `runtime/` if it touches Docker/RCON).
2. Unit tests in `tests/test_<feature>.py` — mock the network.
3. A route: `web/<feature>_routes.py`, or a sub-route in `web/detail_routes.py`
   if it's a server-detail tab.
4. A template partial under `web/templates/`.
5. An end-to-end test in `tests/test_web_*.py`.
6. `pytest -q && pytest -q -m live && ruff check src tests` before you call it done.

## Conventions that aren't obvious

- **One route serves page *and* fragment.** The same URL returns full chrome on a
  browser nav and an htmx fragment when `HX-Request` is set. The detail catch-all
  `/servers/{id}/{tab}` is registered **last** so specific routes match first —
  don't reorder it.
- **Mutations 503 without Docker.** The `require_lifecycle` dependency gates them;
  read-only routes work either way, so the UI stays usable with no daemon.
- **SQLite crosses threads on purpose** (`check_same_thread=False`) — sync routes
  run in a worker pool and share the lifespan-owned connection.
- **`.gitignore` must never contain a bare `servers/`.** It would silently
  swallow `src/ndrchst/web/templates/servers/`. There's a warning comment in the
  file. This has bitten before.
- **Game/UI assets are generated, not committed** (`scripts/build_game_assets.py`).

## Secrets

Never commit one. Everything deployment-specific is read from env at boot; the
`.gitignore` blocks `*.env`, keys, and `*.db` as a backstop. See
[SECURITY.md](SECURITY.md). Found something sensitive committed, or a
vulnerability? Open a private security advisory, not a public issue.

## Commits & PRs

Keep commits scoped and the message about *why*. Run the test + lint sweep before
pushing. The repo is MIT — by contributing you agree your work ships under it.
