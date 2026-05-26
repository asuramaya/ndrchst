# Architecture

ndrchst is five planes around one trust root. This doc traces how they fit and
how the load-bearing flows (a sign-in, a join, a `/daily`) actually move through
them.

```
                        ┌─────────────────────────────────────────┐
   player's browser ───▶│  Cloudflare Worker  (cf/worker)          │
                        │   static + client/<sid>/* ── from R2      │
                        │   /auth /me /gate /link /ranks ── proxied │
                        └───────────────┬───────────────────────────┘
                                        │  origin tunnel (IP hidden)
                                        ▼
                    ┌───────────────────────────────────────┐
   desktop client ─▶│  THE BOX (one Docker host)            │
   (client/)        │                                       │
                    │  public surface :8081  (public.py)    │◀── the trust root
                    │  admin plane    :8080  (api/main.py)  │    for identity,
                    │  SQLite  (wallet_links, identity_links)│    tiers, rewards
                    │                                       │
                    │  ┌─────────────────────────────────┐  │
   Minecraft  ─────▶│  │ game container(s)               │  │
   player           │  │  NeoForge + ndrchst-auth   OR   │  │
                    │  │  Paper + ndrchst-paper          │──┼──▶ HTTP to :8081
                    │  └─────────────────────────────────┘  │    over Docker bridge
                    └───────────────────────────────────────┘
```

The mods never decide anything. Every gate, tier lookup, and reward is an HTTP
call back to the box, and the box answers those calls **only from the Docker
bridge** (`_is_internal_caller()` in `public.py`). Compromising a mod jar or the
client buys nothing — the chain is verified server-side.

## The two ports

| | Port | Reaches | Mutates? |
|---|---|---|---|
| **Admin** (`api/main.py`) | 8080 | private network only (Tailscale) | yes — Docker lifecycle, RCON, R2 publish |
| **Public** (`public.py`) | 8081 | the internet, via the Worker | no, apart from sign-in + link binding |

They're two FastAPI apps so a misrouted public request can never reach a Docker
mutation. The admin plane is never tunneled out.

## Identity: one wallet, two binding paths

A Solana wallet is the identity. How it binds to an in-game player depends on the
server type:

- **Modded (offline-mode, NeoForge).** The player's name is *derived* from the
  wallet (`derive_mc_name`), and the gate is a **signed join token** the client
  presents during the configuration phase. No persistent UUID↔wallet row needed.
- **Cross-play (online-mode, Paper).** The player keeps their real Mojang UUID
  (or Bedrock xuid via Floodgate). That real identity is bound to a wallet once,
  in-game, via `/link`, and stored in `identity_links`.

Either way the tier lives in `wallet_links`, and the gate composes
**identity → wallet → tier**.

## Flow 1 — Sign in with Solana (browser)

```
POST /auth/challenge {pubkey}      → server issues a one-time nonce, builds the SIWS message
  (wallet signs the message — Phantom / Solflare, ed25519)
POST /auth/verify {pubkey, message, signature}
  → consume nonce, re-derive the message, verify_signature()  (vendored ed25519)
  → refresh holdings if stale (throttled live RPC, ≤1 / 300s / wallet)
  → set ndrchst_session cookie (HMAC, 7-day)
  → upsert wallet_links
```

`verify_signature` is the whole of the crypto dependency: base58-decode the
pubkey, run a verify-only RFC 8032 ed25519 check. Both are in
`domain/wallet.py`, no library.

## Flow 2 — The client and the join token

The desktop client never holds a private key. It authenticates by **device
flow** in the browser, then carries short-lived tokens:

```
client → /client/auth/start            → pair_id + user_code + verify_url
browser → /link?code=...  (wallet signs) → /client/auth/approve binds the code
client → /client/auth/poll  (until approved) → identity + a 30-day DEVICE token
...later, every launch:
client → /device/exchange {device_token} → a fresh 30-minute JOIN token
client writes ndrchst-join.token into the game dir
```

Token zoo (all HMAC-signed off `NDRCHST_SESSION_SECRET`, domain-separated,
defined under `domain/`):

| Token | TTL | Carries | Purpose |
|---|---|---|---|
| session | 7 d | wallet | browser cookie |
| join | 30 m | wallet, mc_name, tier | the actual server gate |
| device | 30 d | wallet | client persists it; exchanged for a fresh join token each launch |
| handoff | 3 m | (one-time) | rides the `ndrchst://` deep link; redeems for a device token |
| pairing code | 10 m | (in-memory) | the human-readable code that bridges browser ↔ client/mod |

## Flow 3 — Joining a server

**Modded path** (`ndrchst-auth`, `core/JoinVerifier`):

```
config phase: server asks the client for its token
client sends ndrchst-join.token
mod → POST /join/verify {token}   (Docker bridge)
  → join_token.verify(): HMAC + expiry, returns {wallet, mc_name, tier, skin}
  → name mismatch / bad token → disconnect with a "sign in" prompt
on success: apply FTB rank, scoreboard team, skin, welcome flare
```

**Cross-play path** (`ndrchst-paper`, `core/IdentityGateClient`):

```
async pre-login: plugin → POST /gate/identity {uuid, xuid, username}
  → linked?  apply tier (LuckPerms group) + welcome
  → unlinked? let them in, prompt /link (soft gate by design)
/link in-game:
  plugin → POST /gate/link/start → pair_id + a /link?code=...&m=g URL
  player signs in browser → POST /gate/link/approve binds uuid↔wallet (identity_links)
  plugin polls /gate/link/poll → approved → tier applied live, no rejoin
```

## Flow 4 — `/daily` (un-farmable by construction)

```
in-game /claim  →  mod → POST /daily/claim {wallet}   (Docker bridge)
  → daily_claims: atomic 24h cooldown check-and-set (survives restarts)
  → reward tier = the LAST HOURLY SNAPSHOT, not the live balance
  → mod dispatches `loot give <name> loot ndrchst:daily/<tier>`
```

The split matters: `wallet_links.tier` is the *latest* tier (refreshed on
sign-in), but `snapshot_tier` is written **only** by the hourly job. Rewards read
the snapshot, so a wallet can't flash-borrow tokens at claim time to pull a
better crate. The schema comment in `store/schema.sql` spells this out.

## Holdings → tier, and the background jobs

`runtime/solana.py` computes holdings with two raw RPC calls —
`getTokenSupply` (cached 600 s) and `getTokenAccountsByOwner` — and returns
`balance / supply * 100`. On RPC failure it returns `None`; **callers never
demote a holder to zero on a flaky read**, they keep the last snapshot.

`domain/wallet.py:tier_for()` maps that % to the ladder, floored at `holder`.

Three loops run on the public app:

| Job | Cadence (tunable via `/ops/config`) | What |
|---|---|---|
| `holdings_refresh` | hourly | re-read every linked wallet, write `snapshot_*`; skip (don't demote) on RPC failure |
| `token_price` | 10 min | cache the $NDRCHST ticker from DexScreener (free, not metered RPC) |
| `whitelist_sync` | on demand (admin) | push whitelist + rank to a server over RCON |

## Distribution & the edge

The box stays mostly outbound. The admin plane publishes static pages and
per-server artifacts (`config.json`, `manifest`, `mods/index.json`, `client.zip`)
into R2; the Worker serves those and proxies the dynamic endpoints back through a
tunnel hostname. The 200 MB modpack itself is pulled by the client straight from
the CurseForge CDN — the box never re-hosts it. Client binaries ship from R2 with
a SHA-256 in `latest.json`; the launcher verifies it before swapping itself.

## The shared Java core

`mods-src/core/` is Minecraft-free and compiled into *both* adapters
(`sourceSets.main.java.srcDir "../core/..."`):

- `Tier.java` — the same ladder as `wallet.py`, with presentation (glyph, color).
- `JoinVerifier`, `IdentityGateClient`, `TierClient`, `DailyClient`, `OpsClient`
  — typed HTTP clients for the box endpoints, returning `null` on failure.

So the NeoForge mod and the Paper plugin share their entire box-facing surface
and diverge only where the runtimes force it (offline token vs online UUID, FTB
Ranks vs LuckPerms).

### Box HTTP surface (mod ↔ box, bridge-only)

| Route | Caller | Returns |
|---|---|---|
| `POST /join/verify` | NeoForge gate | `{ok, wallet, mc_name, tier, skin}` |
| `POST /gate/identity` | Paper pre-login | `{ok, wallet, tier}` |
| `POST /gate/link/start` · `GET /gate/link/poll` | Paper `/link` | pairing + status |
| `POST /tier` · `GET /price` | `/tier`, `/price`, tab list | standing + cached ticker |
| `POST /daily/claim` · `POST /daily/reset` | `/daily` | reward tier / cooldown |
| `POST /ops/config` · `/ops/config/set` | op `/ndrchst config` | runtime knobs |
