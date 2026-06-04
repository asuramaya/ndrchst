# Architecture

ndrchst is three planes: the control plane that runs the servers, the desktop
client players launch from, and the Cloudflare edge that distributes the client.
This doc traces how they fit and how the load-bearing flows (create a server,
build a client, launch it) move through them.

```
                        ┌─────────────────────────────────────────┐
   player's browser ───▶│  Cloudflare Worker  (cf/worker)          │
   / desktop client     │   static: client/<sid>/*, servers.json   │
                        │   served from R2 — no box origin          │
                        └───────────────┬───────────────────────────┘
                                        │  artifacts published from the box
                                        ▼
                    ┌───────────────────────────────────────┐
                    │  THE BOX (one Docker host)            │
                    │                                       │
                    │  admin plane :8080  (api/main.py)     │
                    │  SQLite  (servers, installed_assets)  │
                    │                                       │
                    │  ┌─────────────────────────────────┐  │
   Minecraft  ─────▶│  │ game container(s)               │  │
   player           │  │  Paper / NeoForge / Bedrock     │  │
   (via tunnel)     │  └─────────────────────────────────┘  │
                    └───────────────────────────────────────┘
```

## The admin plane

| | Port | Reaches | Mutates? |
|---|---|---|---|
| **Admin** (`api/main.py`) | 8080 | private network only (Tailscale) | yes — Docker lifecycle, RCON, R2 publish |

The admin plane is never tunneled out — it's localhost / private-network only.
The only thing the internet reaches is the static edge.

It creates and runs Java + Bedrock servers in Docker (`runtime/lifecycle.py`),
installs mods/plugins/packs from Modrinth, talks RCON, edits properties, takes
backups, builds per-server client bundles (`runtime/client.py`), and publishes
artifacts to R2 (`runtime/publish.py`).

## The desktop client

`client/` is a launcher built over portablemc. Its job is to keep a player's
install in sync with the server's curated mod set and launch the game connected
to the right host.

- **Mod sync** (`sync.py`) — the client fetches `<edge>/client/<sid>/mods/index.json`
  and mirrors it exactly: add missing jars, replace on sha1 mismatch, prune
  extras. The operator's set on the server is authoritative — no re-resolving
  from an upstream CurseForge manifest. The big modpack `overrides/` (art,
  configs) come from the CurseForge CDN; only mods are server-driven.
- **Launch** (`launcher.py`) — installs the MC version (vanilla or NeoForge via
  portablemc), seeds `servers.dat` + quick-play so the target is one click, and
  runs the game. **Offline mode** (a plain username) by default; **Microsoft**
  sign-in (`online_auth.py`) for online-mode / vanilla servers.
- **Tunnel** (`tunnel.py`) — when `TUNNEL_HOSTNAME` is set, a cloudflared sidecar
  starts and the game dials `127.0.0.1:<local>` instead of the raw host, so the
  server's origin IP stays hidden.
- **Self-update** (`updater.py`) — checks `<edge>/client/latest.json`, downloads
  the new binary, verifies its SHA-256, and re-execs.

A generic build (no baked server) discovers a server from `servers.json` or is
pinned by a `ndrchst://launch?sid=<id>` deep link (`deeplink.py`), which fetches
that server's `config.json` and remembers it.

## Distribution & the edge

The box stays mostly outbound. The admin plane publishes per-server artifacts —
`config.json`, `manifest.json`, `mods/index.json` (+ substitution jars), and the
small `client.zip` — plus `servers.json` into R2 (`runtime/publish.py`). The
Worker (`cf/worker/`) serves those statically; there is no dynamic box origin
behind it. The 200 MB modpack itself is pulled by the client straight from the
CurseForge CDN — the box never re-hosts it. Client binaries ship from R2 with a
SHA-256 in `latest.json`; the launcher verifies it before swapping itself.

## Persistence

SQLite, no ORM (`store/db.py`, schema in `store/schema.sql`). Two tables:
`servers` (per-server metadata) and `installed_assets` (mod/plugin versions per
server). Migrations are additive `ALTER TABLE ADD COLUMN`, applied idempotently
on connect.
