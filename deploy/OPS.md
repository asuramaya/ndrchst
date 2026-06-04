# Deploying ndrchst

How the hosted stack fits together and how to run your own. ndrchst is built to
run on **one box** (a single Docker host) fronted by a Cloudflare Worker + R2,
with the game traffic carried over a Cloudflare Tunnel so the box's IP is never
exposed. Everything deployment-specific is read from env vars — see
[SECURITY.md](../SECURITY.md) for the secrets layout.

> Placeholders below — `<box-host>`, `<user>`, `<tunnel-id>`, `<server_id>`,
> your own domain — are things you fill in for your deployment.

## Topology at a glance

| Role | Where | Notes |
|---|---|---|
| **Dev / source of truth** | your workstation | All commits happen here, pushed to your GitHub fork. |
| **The box** | `<box-host>` — a Docker host reachable over a **private network** (Tailscale / WireGuard / LAN) | Runs the two services + the cloudflared tunnel. The only place `pytest -m docker` runs (real containers). |
| **Repo path on box** | `/home/<user>/ndrchst-alpha` | Kept in sync by rsync from dev (see below). |
| **SSH** | `ssh <box-host>` | An alias in `~/.ssh/config` over your private network. |

## Services (systemd **user** units on the box)

| Unit | Port | Exposure | Env |
|---|---|---|---|
| `ndrchst-admin.service` | 8080 | **Private-network only** — never route through Cloudflare. | `NDRCHST_PUBLIC_HOST`, `NDRCHST_EDGE_URL` |

The public client-distribution surface is **fully static** — served by the
Cloudflare Worker from R2, with no box origin behind it (see "Cloudflare edge").
The box runs the admin control plane plus the cloudflared tunnel (for MC game
TCP).

Unit files are in [`deploy/systemd/`](systemd/) (they use the `%h` specifier so
they resolve to whatever user runs them). Install + linger instructions are in
[`deploy/systemd/README.md`](systemd/README.md).

```bash
ssh <box-host> 'systemctl --user status  ndrchst-admin'
ssh <box-host> 'systemctl --user restart ndrchst-admin'
```

**Docker-group gotcha:** if admin Docker calls fail with `PermissionError`, the
user manager was spawned before the `docker` group was added —
`sudo systemctl restart user@$(id -u).service`.

## Deploy = rsync dev → box, then restart. **Not git pull.**

The recommended flow keeps the box working tree in sync by **rsync from dev**,
not `git pull` on the box. (In the reference deployment the box's git `HEAD` is
an old base commit that's never advanced, so box `git status` is meaningless —
don't trust it, don't `git reset`/`checkout` there. Source of truth is dev +
GitHub.)

Surgical deploy (only the files you changed):

```bash
# 0. on dev: commit + push first so origin matches what you ship
git add <files> && git commit && git push origin main

# 1. back up the box copies you're about to overwrite
FILES="src/ndrchst/runtime/lifecycle.py ..."   # the exact files you changed
ssh <box-host> "cd ~/ndrchst-alpha && B=~/deploy-backups/\$(date +%Y%m%d-%H%M%S) \
  && mkdir -p \$B && for f in $FILES; do [ -e \"\$f\" ] && { mkdir -p \"\$B/\$(dirname \$f)\"; cp -a \"\$f\" \"\$B/\$f\"; }; done && echo \$B"

# 2. rsync those files (relative paths preserved with -R)
rsync -avR $FILES <box-host>:/home/<user>/ndrchst-alpha/

# 3. restart + health-check
ssh <box-host> 'systemctl --user restart ndrchst-admin'
ssh <box-host> 'curl -fsS localhost:8080/healthz'
```

Notes:
- **DB migrations auto-apply on restart** (`store/db.py:_apply_additive_migrations`,
  additive `ALTER TABLE ADD COLUMN`). No manual migration step — ship `db.py` +
  `schema.sql` and restart.
- **`regenerate` + `r2-publish` are ADMIN (:8080) endpoints.** Changes to
  `runtime/client.py` (bundle build), `runtime/publish.py`, or anything those
  import only take effect after **`ndrchst-admin`** restarts — restart it, then
  regenerate + publish.
- **Game/UI assets are gitignored.** `src/ndrchst/web/static/game/` and
  `client/src/ndrchst_client/assets/` are built by
  `scripts/build_game_assets.py` (not committed). rsync both dirs to the box;
  the box serves `/game` (StaticFiles), `publish.py` uploads them to R2, and
  `build_bundle` folds the client assets into `client.zip`.
- Backups land in `~/deploy-backups/<timestamp>/` on the box — restore by
  copying back and restarting.

## Cloudflare edge (Worker + R2)

- **Worker:** [`cf/worker/`](../cf/worker/) — see `wrangler.toml` for the name,
  routes, and R2 bucket binding. Deploy **from dev**: `cd cf/worker && wrangler
  deploy`. `wrangler whoami` to check auth.
- **What it does:** serves `client/<sid>/*` artifacts, `servers.json`, the
  client self-update binaries, and any operator-provided `index.html`/`play.html`
  from R2 — all **static**, read-only, no box origin. The whole surface lives on
  **one host** (`play.<domain>`); the apex + `www` 301 to it.

### Cutover sequence (play/www/apex → Worker)

1. `cd cf/worker && wrangler deploy` (routes uncommented). Comment the `routes`
   out first for a no-cutover validation deploy (lands on `workers.dev`).
2. **Publish artifacts** so R2 has current `client/<sid>/*` + `servers.json`:
   `curl -X POST localhost:8080/servers/<server_id>/r2-publish` (on the box).
3. Verify: `play.<domain>/client/<sid>/config.json` (R2),
   `curl -I www.<domain>/` → 301 to play.

## Cloudflared tunnel (on the box)

Runs as a **system** service (`sudo systemctl {status,restart} cloudflared`),
`ExecStart … --config /etc/cloudflared/config.yml tunnel run`. **Ingress is
dashboard-managed** (Cloudflare Zero Trust → Tunnels) — the local `config.yml`
only carries `tunnel:` / `credentials-file:`; its `ingress:` block is ignored.
Reference ingress: `mc.<domain> → tcp://localhost:<mc-port>`, fallback 404.
(`play.<domain>` is served by the Worker from R2, not the box — no tunnel
ingress needed for it.)

## Secrets (never in the repo)

| Secret | Location | How |
|---|---|---|
| R2 keys (`NDRCHST_R2_*`) | box `~/.config/ndrchst/r2.env` (chmod 600) | SigV4 creds for R2 publish. |
| GitHub | `gh auth` on dev | needs the `workflow` scope (commits touch `.github/workflows/`). |

## Common admin ops (on the box, against :8080)

```bash
S=<server_id>   # find it: curl -s localhost:8080/api/servers | jq '.[].id,.[].name'

# Pin the modpack to the CurseForge CDN (box stops re-hosting the big zip)
curl -X POST localhost:8080/servers/$S/client/regenerate -d cf_project_id=<pid> -d cf_file_id=<fid>

# Rebuild the mods index; publish artifacts to R2 (light), or heavy (+client.zip)
curl -X POST localhost:8080/servers/$S/mods/build-index
curl -X POST localhost:8080/servers/$S/r2-publish
curl -X POST "localhost:8080/servers/$S/r2-publish?heavy=true"
```

## Domains & token (reference deployment)

- `play.ndrchst.com` — the static client-distribution surface (client artifacts
  + `servers.json`); `ndrchst.com` / `www.ndrchst.com` 301 → play;
  `mc.ndrchst.com` MC game TCP (via tunnel, origin IP never exposed);
  `dl.ndrchst.com` — R2 client binaries.

## Dev quickref (tests/lint before any deploy)

```bash
.venv/bin/pytest -q                 # default suite
.venv/bin/pytest -q -m live         # real uvicorn + routes
.venv/bin/ruff check src tests
node --check cf/worker/src/worker.js # Worker syntax
# pytest -m docker only runs on the box (a Docker host)
```
