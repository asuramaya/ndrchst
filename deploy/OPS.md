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
| `ndrchst-public.service` | 8081 | Public — the Cloudflare tunnel fronts it. | reads `~/.config/ndrchst/public.env` (optional) |

Unit files are in [`deploy/systemd/`](systemd/) (they use the `%h` specifier so
they resolve to whatever user runs them). Install + linger instructions are in
[`deploy/systemd/README.md`](systemd/README.md).

```bash
ssh <box-host> 'systemctl --user status  ndrchst-public ndrchst-admin'
ssh <box-host> 'systemctl --user restart ndrchst-public ndrchst-admin'
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
FILES="src/ndrchst/public.py ..."   # the exact files you changed
ssh <box-host> "cd ~/ndrchst-alpha && B=~/deploy-backups/\$(date +%Y%m%d-%H%M%S) \
  && mkdir -p \$B && for f in $FILES; do [ -e \"\$f\" ] && { mkdir -p \"\$B/\$(dirname \$f)\"; cp -a \"\$f\" \"\$B/\$f\"; }; done && echo \$B"

# 2. rsync those files (relative paths preserved with -R)
rsync -avR $FILES <box-host>:/home/<user>/ndrchst-alpha/

# 3. restart + health-check
ssh <box-host> 'systemctl --user restart ndrchst-public ndrchst-admin'
ssh <box-host> 'curl -fsS localhost:8081/healthz && echo && curl -fsS localhost:8080/healthz'
```

Notes:
- **DB migrations auto-apply on restart** (`store/db.py:_apply_additive_migrations`,
  additive `ALTER TABLE ADD COLUMN`). No manual migration step — ship `db.py` +
  `schema.sql` and restart.
- **`regenerate` + `r2-publish` are ADMIN (:8080) endpoints.** Changes to
  `runtime/client.py` (bundle build), `runtime/publish.py`, or anything those
  import only take effect after **`ndrchst-admin`** restarts — restarting only
  `ndrchst-public` runs the *old* code and silently ships a stale bundle.
  Restart both, then regenerate + publish.
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
- **What it does:** serves static pages + `client/<sid>/*` artifacts from R2;
  **proxies the dynamic endpoints** (`/auth/*`, `/me`, `/client/auth/*`,
  `/gate/*`, `/link`, `/ranks`) to `ORIGIN_BASE` (any method incl. POST). The
  whole surface lives on **one host** (`play.<domain>`): `/` is the landing,
  `/play` the app; the apex + `www` 301 to it. Cookies carry no Domain attr —
  one host, host-scoped session, no cross-origin stranding.
- **`ORIGIN_BASE` var:** MUST point at a tunnel hostname that routes to the
  box's `:8081`, **separate from `play`** (which is the Worker). Blank → the
  dynamic endpoints 503 (static surface still works). Comment the `routes` out
  for a no-cutover validation deploy (lands on `workers.dev`).

### Cutover sequence (play/www/apex → Worker)

1. **Add an origin hostname for the box.** The tunnel is **dashboard-managed** —
   cloudflared loads creds from `/etc/cloudflared/config.yml` but pulls its
   **ingress from Cloudflare**, so editing the local `config.yml` ingress does
   nothing. In **Cloudflare Zero Trust → Networks → Tunnels → `<tunnel-id>` →
   Public Hostnames**, add: subdomain `origin`, your domain, type **HTTP**, URL
   **`localhost:8081`** (creates ingress + DNS together). Verify
   `curl https://origin.<domain>/healthz` → 200.
2. Set `ORIGIN_BASE = "https://origin.<domain>"` in `cf/worker/wrangler.toml`.
3. `cd cf/worker && wrangler deploy` (routes uncommented).
4. **Republish pages + artifacts** so R2 has current static + `client/<sid>/*`:
   `curl -X POST localhost:8080/servers/<server_id>/r2-publish` (on the box).
5. Verify: `play.<domain>/` (landing, R2), `/play` (app), `/ranks` + `/me`
   (proxied → box), `curl -I www.<domain>/` → 301 to play.

## Cloudflared tunnel (on the box)

Runs as a **system** service (`sudo systemctl {status,restart} cloudflared`),
`ExecStart … --config /etc/cloudflared/config.yml tunnel run`. **Ingress is
dashboard-managed** (Cloudflare Zero Trust → Tunnels) — the local `config.yml`
only carries `tunnel:` / `credentials-file:`; its `ingress:` block is ignored.
Reference ingress: `play.<domain> → http://localhost:8081`, `mc.<domain> →
tcp://localhost:<mc-port>`, fallback 404.

## Secrets (never in the repo)

| Secret | Location | How |
|---|---|---|
| `NDRCHST_SESSION_SECRET` | box `~/.config/ndrchst/public.env` (chmod 600) | `printf 'NDRCHST_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" >> ~/.config/ndrchst/public.env` — **without it, every restart logs out all wallets.** |
| R2 keys (`NDRCHST_R2_*`) | box `~/.config/ndrchst/r2.env` (chmod 600) | SigV4 creds for R2 publish. |
| GitHub | `gh auth` on dev | needs the `workflow` scope (commits touch `.github/workflows/`). |

Optional in `public.env`: `NDRCHST_SOLANA_RPC`, `NDRCHST_TOKEN_MINT`,
`NDRCHST_RANK_CMD` (e.g. `lp user {name} parent set {tier}`).

## Common admin ops (on the box, against :8080)

```bash
S=<server_id>   # find it: curl -s localhost:8080/api/servers | jq '.[].id,.[].name'

# Pin the modpack to the CurseForge CDN (box stops re-hosting the big zip)
curl -X POST localhost:8080/servers/$S/client/regenerate -d cf_project_id=<pid> -d cf_file_id=<fid>

# Rebuild the mods index; publish artifacts/pages to R2 (light), or heavy (+client.zip)
curl -X POST localhost:8080/servers/$S/mods/build-index
curl -X POST localhost:8080/servers/$S/r2-publish
curl -X POST "localhost:8080/servers/$S/r2-publish?heavy=true"

# Wallet/rank loop
curl -X POST localhost:8080/wallets/refresh                  # re-read chain, recompute tiers
curl -X POST localhost:8080/servers/$S/wallets/sync          # push whitelist (+rank) over RCON
```

## Domains & token (reference deployment)

- `play.ndrchst.com` — the whole public surface (landing `/` + app `/play`);
  `ndrchst.com` / `www.ndrchst.com` 301 → play; `mc.ndrchst.com` MC game TCP
  (via tunnel, origin IP never exposed); `dl.ndrchst.com` — R2 client binaries.
- **$NDRCHST mint:** `FCNoxy62oN9HhjqM49StjRAzXqehquNRwNpRVL6qpump` (pump.fun).

## Dev quickref (tests/lint before any deploy)

```bash
.venv/bin/pytest -q                 # default suite
.venv/bin/pytest -q -m live         # real uvicorn + routes
.venv/bin/ruff check src tests
node --check cf/worker/src/worker.js # Worker syntax
# pytest -m docker only runs on the box (a Docker host)
```
