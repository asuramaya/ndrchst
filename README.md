# ndrchst


![ndrchst: a weird experiment](logo.png)


A token-gated modded-Minecraft stack that runs on one box. Link a Solana wallet
and what you hold becomes your identity and your rank — your tier, your perks,
and your daily crate all key off your on-chain balance. MIT licensed.

Most Minecraft "panels" stop at start / stop / console. ndrchst is the whole
vertical in one repo: the control plane that runs the server, the wallet auth +
on-chain rank engine, the game-side mods that enforce it, the desktop client
players launch from, and the Cloudflare edge that fronts it.

## What's in here

- **Control plane** — `src/ndrchst/`, FastAPI + htmx + SQLite on `:8080`.
  Create and run Java + Bedrock servers in Docker, install mods/plugins/packs
  from Modrinth, RCON console, backups, properties editor. Localhost-first.
  **This half is a complete server panel on its own — no wallet, no token, no
  chain required.**
- **Identity & economy** — `src/ndrchst/public.py` on `:8081`. Sign in with a
  Solana wallet (SIWS), holdings map to a tier by % of supply, and a 24-hour
  daily reward reads an *hourly* holdings snapshot so it can't be farmed by
  flash-holding at claim time. No web3 SDK anywhere: ed25519 verification and
  base58 are vendored (RFC 8032, ~120 lines, verify-only — you never hold a key).
- **Game adapters** — `mods-src/`. One shared Java core (the tier model + the
  box HTTP client) compiled straight into two runtimes: `ndrchst-auth`, a
  NeoForge mod that token-gates the join and drives FTB Ranks + `/daily`; and
  `ndrchst-paper`, a Paper plugin for online-mode cross-play (Java + Bedrock via
  Geyser/Floodgate) with in-game `/link` and LuckPerms-gated perks.
- **Desktop client** — `client/`, a launcher over portablemc. Device-flow wallet
  auth (no private key ever touches it), modpack + mod sync straight from CDN,
  the game connection wrapped in a Cloudflare tunnel so the host IP stays hidden,
  and self-update with SHA-256 verification.
- **Edge** — `cf/worker/`. A Cloudflare Worker serving static pages and
  per-server artifacts from R2, proxying the dynamic auth/pairing endpoints back
  to the box.

The rank ladder: **holder** (just link) → **bronze** 0.1% → **silver** 0.5% →
**gold** 1% → **diamond** 2.5% → **whale** 5% of supply.

## Design choices worth knowing

- **The box is the trust root.** Mods hold no state and decide nothing — every
  gate and reward is an HTTP call to the box, and those endpoints only answer the
  Docker bridge. A tampered mod jar gets you nowhere.
- **No third-party crypto, no web3 dependency.** You never custody a key, so
  verify-only is all you need. Smaller attack surface, no supply chain to trust.
- **One source of truth, derived not copied.** The tier ladder lives in one
  Python file and one Java file; crate odds are read from the actual loot tables,
  not a parallel list that rots.
- **Two ports, two exposure models.** The admin plane (`:8080`) is meant for a
  private network only (e.g. Tailscale). The public surface (`:8081`) is the only
  thing the internet reaches, and it's read-only apart from sign-in.

## Quickstart (control plane)

```bash
uv sync
uv run ndrchst run      # uvicorn on http://localhost:8080
uv run ndrchst doctor   # env preflight: Docker, ports, disk, group
```

Open <http://localhost:8080>, create a Paper or NeoForge server, install a mod.
The control plane stands alone — you don't need a wallet or the edge to use it.

Wiring up the full token gate (the public surface, a mint, the mods, the client)
is a deployment, not a `pip install`. See [deploy/OPS.md](deploy/OPS.md) for how
the hosted stack fits together.

## Requirements

- Linux (macOS soon, Windows via WSL)
- Docker
- Python 3.12+

## Layout

```
src/ndrchst/
  platforms/   per-platform install + version logic (Paper, NeoForge, Bedrock, Modpack, …)
  mods/        Modrinth asset source (search + version listing)
  runtime/     Docker, RCON, lifecycle, Solana holdings, R2/edge publish, background jobs
  domain/      wallet (vendored ed25519), tokens (session/join/device/handoff), players, worlds, files
  store/       SQLite (no ORM) — servers, wallet_links, identity_links, daily_claims
  api/         admin JSON API + app factory (:8080)
  web/         htmx admin UI + the public site renderer
  public.py    the public wallet/economy surface (:8081)
  cli.py       `ndrchst run` / `ndrchst doctor`
mods-src/      core/ (shared Java) + ndrchst-auth/ (NeoForge) + ndrchst-paper/ (Paper)
client/        the desktop launcher (PyInstaller-packaged)
cf/worker/     the Cloudflare edge Worker
deploy/        deployment guide, systemd units, datapacks (daily crate loot), ranks config
```

See [docs/architecture.md](docs/architecture.md) for how a join, a sign-in, and a
`/daily` actually flow through all of it.

## Status

Alpha, single-operator by design — there's no multi-user/RBAC, because the admin
plane is meant to sit behind a private network, not face the world. Roughly 11k
LOC Python (plus ~2k Java and a ~3k-LOC client) and ~440 tests. The reference
deployment runs $NDRCHST (mint `FCNoxy62oN9HhjqM49StjRAzXqehquNRwNpRVL6qpump`).

## License

MIT — see [LICENSE](LICENSE). Security posture and what's deliberately kept out
of the repo: [SECURITY.md](SECURITY.md).
