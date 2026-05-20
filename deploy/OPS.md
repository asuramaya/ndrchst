# ndrchst ops & infra runbook

The single source of truth for "how do I deploy / where does everything live."
If something here is wrong, fix it here — this file is supposed to make future
ops obvious and prevent re-discovering the topology each time.

## Topology at a glance

| Role | Where | Notes |
|---|---|---|
| **Dev / source of truth** | this workstation, `/home/asuramaya/code/ndrchst-alpha` | All commits happen here → pushed to GitHub. |
| **GitHub origin** | `github.com/asuramaya/ndrchst` (public, OSS) | `git push origin main`. Push needs the `workflow` scope (commits touch `.github/workflows/`) — `gh auth refresh -h github.com -s workflow` if rejected. |
| **The box** | `ndrchst-01` — Tailscale `100.89.8.49`, LAN `192.168.5.127` | Bare-metal Ubuntu, Docker host. Runs the live services + the cloudflared tunnel. Also the only place `pytest -m docker` runs green. |
| **Repo path on box** | `/home/ndrchst-01/ndrchst-alpha` | |
| **SSH** | `ssh ndrchst-01` | alias in `~/.ssh/config`: user `ndrchst-01`, key `~/.ssh/id_rsa`, over Tailscale. |

## Services (systemd **user** units on the box)

| Unit | Port | Exposure | Env |
|---|---|---|---|
| `ndrchst-admin.service` | 8080 | **Tailscale-only** — `100.89.8.49:8080` / `192.168.5.127:8080`. NEVER route through Cloudflare. | bakes `NDRCHST_PUBLIC_HOST=mc.ndrchst.com`, `NDRCHST_EDGE_URL=https://play.ndrchst.com` |
| `ndrchst-public.service` | 8081 | Public — Cloudflare tunnel fronts it at `play.ndrchst.com`. | reads `~/.config/ndrchst/public.env` (optional) |

```bash
ssh ndrchst-01 'systemctl --user status  ndrchst-public ndrchst-admin'
ssh ndrchst-01 'systemctl --user restart ndrchst-public ndrchst-admin'
```

Linger is enabled (services survive logout/boot). **Docker-group gotcha:** if
admin Docker calls fail with `PermissionError`, the user manager was spawned
before the `docker` group was added — `sudo systemctl restart user@$(id -u).service`.

## Deploy = rsync dev → box, then restart. **NOT git pull.**

> **CRITICAL — the box's git is a frozen mirror.** The box working tree is
> maintained by rsync from dev, but its git `HEAD` is an old base commit
> (`03ce8a7…`, pre-wallet-auth) that was never advanced. So `git status` on the
> box shows **hundreds of "uncommitted" lines** that are just accumulated dev
> syncs — this is EXPECTED, not local box work. **Never `git pull` / `git reset`
> / `git checkout` on the box, and never trust box `git status`.** Source of
> truth is always dev + GitHub.

Standard surgical deploy (only the files you changed):

```bash
# 0. on dev: commit + push first so origin matches what you ship
cd /home/asuramaya/code/ndrchst-alpha
git add <files> && git commit && git push origin main

# 1. back up the box copies you're about to overwrite
FILES="src/ndrchst/public.py src/ndrchst/...   # the exact files you changed
ssh ndrchst-01 "cd ~/ndrchst-alpha && B=~/deploy-backups/\$(date +%Y%m%d-%H%M%S) \
  && mkdir -p \$B && for f in $FILES; do [ -e \"\$f\" ] && { mkdir -p \"\$B/\$(dirname \$f)\"; cp -a \"\$f\" \"\$B/\$f\"; }; done && echo \$B"

# 2. rsync those files (relative paths preserved with -R)
rsync -avR $FILES ndrchst-01:/home/ndrchst-01/ndrchst-alpha/

# 3. restart + health-check
ssh ndrchst-01 'systemctl --user restart ndrchst-public ndrchst-admin'
ssh ndrchst-01 'curl -fsS localhost:8081/healthz && echo && curl -fsS localhost:8080/healthz'
```

Notes:
- **DB migrations auto-apply on restart** (`store/db.py:_apply_additive_migrations`,
  additive `ALTER TABLE ADD COLUMN`). No manual migration step. Just ship
  `db.py` + `schema.sql` and restart.
- **`regenerate` + `r2-publish` are ADMIN (:8080) endpoints.** So changes to
  `runtime/pilot.py` (bundle build), `runtime/publish.py`, or anything those
  import only take effect after **`ndrchst-admin`** is restarted — restarting
  only `ndrchst-public` will run the *old* code and silently ship a stale
  bundle. Restart both, then regenerate + publish. (Got bitten: a themed-asset
  bundle came out missing its assets because admin still had old `pilot.py`.)
- **Game/UI assets are gitignored.** `src/ndrchst/web/static/game/` and
  `pilot-client/src/ndrchst_pilot/assets/` are built by
  `scripts/build_game_assets.py` (not committed). rsync both dirs to the box;
  the box serves `/game` (StaticFiles) and `publish.py` uploads them to R2,
  and `build_bundle` folds the pilot assets into `pilot.zip`.
- **Pilot-client drift:** the box's `pilot-client/` can lag dev. Pilot *bundles*
  are built from the box's copy, so if you need fresh bundles, rsync
  `pilot-client/` too and regenerate (below).
- Backups land in `~/deploy-backups/<timestamp>/` on the box — restore by
  copying back and restarting.

## Cloudflare edge (Worker + R2)

- **Worker:** `cf/worker/` — name `ndrchst-edge`, R2 bucket binding `DL` →
  `ndrchst-dl`. Deploy **from dev**: `cd cf/worker && wrangler deploy` (wrangler
  4.88 installed on dev). `wrangler whoami` to check auth.
- **What it does:** serves static pages + `pilot/<sid>/*` artifacts from R2;
  **proxies the dynamic endpoints** (`/auth/*`, `/me`, `/pilot/auth/*`, `/link`,
  `/ranks`) to `ORIGIN_BASE` (any method incl. POST). Cookies have no Domain
  attr, so Set-Cookie flows back transparently on play/www.
- **`routes` in `wrangler.toml`:** `play.ndrchst.com/*`, `www.ndrchst.com/*`.
  Comment them out for a no-cutover validation deploy (lands on workers.dev).
- **`ORIGIN_BASE` var:** MUST point at a tunnel hostname that routes to the
  box's `:8081`, **separate from play/www** (those become the Worker). Blank →
  the dynamic endpoints 503.

### Cutover sequence (play/www → Worker)

1. **Add an origin hostname for the box.** ⚠️ **The tunnel is DASHBOARD-MANAGED
   (remotely configured).** cloudflared loads creds from
   `/etc/cloudflared/config.yml` but then pulls its **ingress from Cloudflare**
   (`journalctl -u cloudflared` shows `Updated to new configuration … version=N`,
   listing `play`/`mc`/404). **Editing the local `config.yml` ingress does
   NOTHING** — and `cloudflared tunnel route dns` fails (`cert.pem` unauthed,
   code 10000). To add `origin.ndrchst.com`:
   - **Cloudflare Zero Trust → Networks → Tunnels → tunnel `05da0bcd-…` → Public
     Hostnames → Add public hostname:** subdomain `origin`, domain `ndrchst.com`,
     type **HTTP**, URL **`localhost:8081`**. This creates the ingress AND the
     DNS record together. (If a manual `origin` DNS record already exists, delete
     it first so the dashboard can create its own.)
   - Verify: `curl https://origin.ndrchst.com/healthz` → public-surface JSON (200).
   - Same applies to `www.ndrchst.com` — no DNS yet; add a public hostname (or a
     proxied record + Worker route) when you want it live. `play.ndrchst.com` is
     the live surface.
2. Set `ORIGIN_BASE = "https://origin.ndrchst.com"` in `cf/worker/wrangler.toml`.
3. `cd cf/worker && wrangler deploy` (routes uncommented → takes over play/www).
4. **Republish pages** so R2 has the current static pages:
   `ssh ndrchst-01 'curl -X POST localhost:8080/servers/<sid>/r2-publish'`.
5. Verify: `play.ndrchst.com/` (R2), `/ranks` + `/me` (proxied→box),
   `origin.ndrchst.com/healthz` (box direct).

## Cloudflared tunnel (on the box)

- Tunnel ID `05da0bcd-8b7c-446a-bc55-89aa56dbf79e`. Runs as **system** service
  (`sudo systemctl {status,restart} cloudflared`), `ExecStart … --config
  /etc/cloudflared/config.yml tunnel run`. Creds
  `~/.cloudflared/05da0bcd-….json`.
- **Ingress is DASHBOARD-MANAGED**, not from `config.yml` — manage public
  hostnames in Cloudflare Zero Trust → Tunnels. Current: `play.ndrchst.com →
  http://localhost:8081`, `mc.ndrchst.com → tcp://localhost:25590`, fallback 404.
  The local `config.yml` only carries `tunnel:`/`credentials-file:` (its
  `ingress:` block is ignored).

## Secrets (never in the repo)

| Secret | Location | How |
|---|---|---|
| `NDRCHST_SESSION_SECRET` | box `~/.config/ndrchst/public.env` (chmod 600) | `printf 'NDRCHST_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" >> ~/.config/ndrchst/public.env` — **without it, every restart logs out all wallets.** |
| R2 keys | box `~/.config/ndrchst/r2.env` (chmod 600) | SigV4 creds for publish. **Rotate the keys pasted in chat earlier — still pending.** |
| GitHub | `gh auth` on dev | needs `workflow` scope. |

Optional in `public.env`: `NDRCHST_SOLANA_RPC`, `NDRCHST_TOKEN_MINT`,
`NDRCHST_RANK_CMD` (e.g. `lp user {name} parent set {tier}`).

## Common admin ops (run on the box against :8080)

```bash
S=<server_id>   # confirm with: curl -s localhost:8080/api/servers | jq '.[].id,.[].name'
                # ATM10 server is expected to be b757b2ea9cea — verify before use.

# Pin the modpack pack to CurseForge CDN (box stops re-hosting the 200MB zip)
curl -X POST localhost:8080/servers/$S/pilot/regenerate -d cf_project_id=925200 -d cf_file_id=8091114

# Rebuild the mods index, publish artifacts/pages to R2 (light), or heavy (+pilot.zip)
curl -X POST localhost:8080/servers/$S/mods/build-index
curl -X POST localhost:8080/servers/$S/r2-publish            # light: pages + index
curl -X POST "localhost:8080/servers/$S/r2-publish?heavy=true"

# Wallet/rank loop
curl -X POST localhost:8080/wallets/refresh                  # re-read chain, recompute tiers
curl -X POST localhost:8080/servers/$S/wallets/sync          # push whitelist (+rank) over RCON
```

## Domains & token

- `play.ndrchst.com` public surface · `www.ndrchst.com` landing · `mc.ndrchst.com`
  MC game TCP (via tunnel, origin IP never exposed).
- $NDRCHST mint `EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump`.
- ATM10 modpack: CF project `925200`, pinned file `8091114`
  (`All the Mods 10-7.0.zip`).

## Dev quickref (tests/lint before any deploy)

```bash
.venv/bin/pytest -q                 # default suite
.venv/bin/pytest -q -m live         # real uvicorn + routes
.venv/bin/ruff check src tests
node --check cf/worker/src/worker.js # Worker syntax
# pytest -m docker only runs on the box (Docker host)
```
