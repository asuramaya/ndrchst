# Client distribution & auto-update (Cloudflare R2)

The client ships as a per-OS binary and **self-updates** from a Cloudflare R2
bucket fronted by a custom domain. Bytes come off Cloudflare's global CDN, not
the operator's residential uplink — so distribution scales to any number of
clients without touching the host.

```
 GitHub tag client-vX.Y.Z
        │  (build-client.yml)
        ├── build per-OS binaries (PyInstaller)
        └── publish → R2 bucket  ┐
                                 │  https://dl.ndrchst.com/client/
   ┌─────────────────────────────┘
   ├── ndrchst-client-linux-x86_64
   ├── ndrchst-client-windows-x86_64.exe
   ├── ndrchst-client-macos-arm64
   └── latest.json   ← client checks this on launch
```

## One-time Cloudflare setup

1. **Create an R2 bucket** (Cloudflare dashboard → R2 → *Create bucket*), e.g.
   `ndrchst-dl`.
2. **Connect a custom domain** to the bucket (bucket → *Settings* → *Public
   access* → *Connect domain*), e.g. `dl.ndrchst.com`. This serves objects over
   HTTPS through Cloudflare's CDN.
3. **Create an R2 API token** (R2 → *Manage API Tokens* → *Create*, with
   Object Read & Write on the bucket). Note the **Access Key ID**, **Secret
   Access Key**, and your **Account ID**.

The published layout is `<bucket>/<prefix>/…` (prefix defaults to `client`), so
objects resolve at `https://dl.ndrchst.com/client/latest.json`, etc.

## GitHub configuration

In the repo (*Settings → Secrets and variables → Actions*):

| Kind     | Name                   | Value                                  |
|----------|------------------------|----------------------------------------|
| Variable | `PUBLISH_R2`           | `true` (enables the publish job)       |
| Variable | `R2_PREFIX`            | `client` (optional; this is the default)|
| Secret   | `R2_ACCOUNT_ID`        | Cloudflare account id                  |
| Secret   | `R2_ACCESS_KEY_ID`     | R2 token access key id                 |
| Secret   | `R2_SECRET_ACCESS_KEY` | R2 token secret                        |
| Secret   | `R2_BUCKET`            | bucket name, e.g. `ndrchst-dl`         |

Until `PUBLISH_R2=true` is set, builds still run and upload artifacts to the
Actions run — they just aren't pushed to R2.

## Cutting a release

```bash
git tag client-v0.2.0
git push origin client-v0.2.0
```

The workflow stamps `__version__` from the tag, builds all three binaries, then
(if `PUBLISH_R2=true`) uploads them plus a freshly-hashed `latest.json` to R2.
`latest.json` is served `no-cache`; binaries are `immutable`.

## Pointing clients + the play page at it

Both the self-updater and the play page's "standalone launcher" links read the
same base URL. Set, wherever the client config / public app run:

- Client config / build: **`UPDATE_BASE_URL=https://dl.ndrchst.com/client`**
  (baked into `config.py` per drop, or via `client-config.json`).
- Public app (play page download links): **`NDRCHST_CLIENT_DOWNLOADS_BASE=https://dl.ndrchst.com/client`**

On launch the client fetches `<UPDATE_BASE_URL>/latest.json`; if it advertises a
newer `version` with an asset for the client's OS/arch, the launcher shows
**Update & restart**. Applying it downloads the binary, verifies its SHA-256,
swaps the running executable (POSIX in-place + re-exec; Windows via a detached
helper), and relaunches. Unset `UPDATE_BASE_URL` disables auto-update.

---

# Serving the public surface from the edge (R2 + Worker)

To take the residential box off every client's hot path, the admin **pushes**
per-server artifacts and the rendered pages to the same R2 bucket, and a
**Cloudflare Worker** (`cf/worker/`) serves them. The box then only does
occasional outbound uploads — no inbound web exposure.

## Bucket layout (`ndrchst-dl`)

```
client/latest.json                      ← updater manifest (CI)
client/ndrchst-client-<os>-<arch>[.exe]  ← client binaries (CI)
client/<sid>/config.json                ← per-server (admin push)
client/<sid>/manifest.json
client/<sid>/modpack.zip                ← heavy push only
client/<sid>/client.zip                  ← heavy push only
client/<sid>/mods/index.json            ← what the client mirrors
client/<sid>/mods/<substitution>.jar    ← origin-served jars (non-CDN)
index.html  play.html  servers.json    ← rendered pages (admin push)
```

## Box (admin service) env

Add to `ndrchst-admin.service` (and restart):

```
NDRCHST_R2_ACCOUNT_ID=<account id>
NDRCHST_R2_ACCESS_KEY_ID=<r2 token key id>
NDRCHST_R2_SECRET_ACCESS_KEY=<r2 token secret>
NDRCHST_R2_BUCKET=ndrchst-dl
# leave NDRCHST_R2_PREFIX UNSET — keys already carry client/<sid>/…
NDRCHST_EDGE_URL=https://play.ndrchst.com          # mod origin_urls resolve via the Worker
NDRCHST_CLIENT_DOWNLOADS_BASE=https://dl.ndrchst.com/client
```

The surface is one host now (`play.ndrchst.com`; apex + www 301 there), so the
page nav is same-host relative — there's no `NDRCHST_PLAY_URL`/`NDRCHST_HOME_URL`
to set anymore.

When R2 is configured, **rebuilding a server's mods index auto-publishes** the
light set (index + pages + substitution jars). Push the big blobs (modpack.zip,
client.zip) when the pack changes:

```
curl -X POST 'http://127.0.0.1:8080/servers/<sid>/r2-publish?heavy=true'
```

## Worker + domains

```bash
cd cf/worker
wrangler deploy            # binds bucket ndrchst-dl, routes play.* + www.*
```

- **`play.ndrchst.com` / `www.ndrchst.com`** → the Worker (pages + `/client/*` from R2).
- **`dl.ndrchst.com`** → a *direct* R2 custom domain on the bucket (serves the
  client binaries + per-server objects without a Worker). This is the
  `*_DOWNLOADS_BASE` / `UPDATE_BASE_URL` host.

Once the Worker is live and serving, the box's inbound `play.ndrchst.com`
Cloudflare-tunnel route to `:8081` can be retired — the box becomes
outbound-only (MC tunnel + R2 pushes). Only the MC game tunnel must stay.
