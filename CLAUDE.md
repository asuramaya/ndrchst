# CLAUDE.md — ndrchst-alpha orientation

## TL;DR

Single-machine OSS Minecraft server control plane. Python 3.12+, FastAPI + htmx + Jinja + SQLite + docker-py. Java + Bedrock first-class. ~6.7k LOC, 138 tests, v0 closed.

**For a full state-of-the-project picture: read [STATE.md](STATE.md) first.**
**For deploy / infra (the box, services, rsync deploy, Cloudflare edge, secrets): read [deploy/OPS.md](deploy/OPS.md).**

## Common commands

```bash
# Run tests (default suite excludes integration/live/docker)
.venv/bin/pytest -q

# Run live tests (real uvicorn + curl every route)
.venv/bin/pytest -q -m live

# Run integration tests (live PaperMC/Modrinth/Mojang APIs)
.venv/bin/pytest -q -m integration

# Run Docker tests (would need a real daemon; skip on this dev machine)
.venv/bin/pytest -q -m docker

# Lint
.venv/bin/ruff check src tests

# Boot the app
.venv/bin/ndrchst run            # uvicorn :8080 localhost
.venv/bin/ndrchst doctor         # env preflight

# Direct uvicorn (if cli.py is broken or for non-default flags)
.venv/bin/python -m uvicorn ndrchst.api.main:app --host 127.0.0.1 --port 8080
```

## Where to find things

| Looking for | File |
|---|---|
| Server lifecycle (create/start/stop/delete) | `src/ndrchst/runtime/lifecycle.py` |
| Docker abstraction (FakeClient-able) | `src/ndrchst/runtime/docker.py` |
| Per-platform install logic | `src/ndrchst/platforms/<id>.py` |
| Modrinth client | `src/ndrchst/mods/modrinth.py` |
| App factory + lifespan + AppState | `src/ndrchst/api/deps.py` + `api/main.py` |
| HTML routes (htmx UI) | `src/ndrchst/web/servers_routes.py` + `web/detail_routes.py` |
| Templates | `src/ndrchst/web/templates/` |
| CSS (design tokens + components) | `src/ndrchst/web/static/app.css` |
| Test scenario builder (realistic data dir) | `tests/scenario.py` |
| Fake Docker client (reused across tests) | `tests/test_docker_runtime.py:FakeClient` |

## Conventions that aren't obvious

1. **One-route-pattern for HTML:** same URL serves full page or htmx fragment based on `HX-Request` header. The detail catch-all `/servers/{id}/{tab}` is registered **last** so specific routes match first. Don't reorder.

2. **Mutation routes return 503 without Docker.** `require_lifecycle` dependency does the check. Read-only routes work either way.

3. **SQLite conn is shared across threads via `check_same_thread=False`.** FastAPI sync routes run in a worker pool; the lifespan-owned conn must cross threads. SQLite C-level is thread-safe.

4. **EULA is auto-written.** Per project policy: running ndrchst = agreeing. See `runtime/eula.py`. Don't surface an interactive prompt for it.

5. **`.gitignore` must NOT contain bare `servers/`.** It would silently swallow `src/ndrchst/web/templates/servers/`. Already bitten once; warning comment in the file.

6. **Tests use `FakeClient` everywhere except `-m docker`.** Real container boot only happens on a Docker-enabled machine. Don't pretend otherwise.

## When asked to add a feature

The pattern that's worked for every feature so far:

1. Pure logic in `domain/<feature>.py` (or `runtime/` if it touches Docker/RCON)
2. Unit tests in `tests/test_<feature>.py` (mock the network with httpx MockTransport)
3. HTML route in `web/<feature>_routes.py` OR add a sub-route in `web/detail_routes.py` if it's a tab
4. Template partial in `web/templates/<feature>/` (or `templates/servers/tabs/_<feature>.html`)
5. End-to-end test in `tests/test_web_*.py` or `tests/test_scenario_end_to_end.py`
6. Verify: `pytest -q && pytest -q -m live && ruff check src tests`

## When asked to port from v2

The v2 Python source lives at `~/code/ndrchst/ndrchst/`. Treat it as **reference for what features did**, not source-of-truth code to paste. See the memory file `feedback_dont_port_one_to_one.md` for the rule and `reference_v2_source_tree.md` for the file-by-file map.

## Background context (memory)

If you have access to the auto-memory system, the most load-bearing files for this project are:
- `feedback_deslop_mindset.md` — strong preference for cutting features
- `feedback_server_handles_human_steps.md` — EULA/config files written automatically
- `feedback_stress_before_handoff.md` — run tests/lint/live boot before reporting done
- `project_ndrchst_alpha.md` — project framing
- `project_v1_deferred.md` — what's intentionally not done yet
- `project_no_local_docker.md` — this dev machine has no Docker daemon

If you don't have memory access, the same context lives in this file + [STATE.md](STATE.md).
