# Security & the OSS surface

This repository is **open source**. It contains only code, templates, and
docs — **no credentials, keys, or per-deployment secrets**, and CI is set up
so it stays that way.

## What is NOT in this repo (and must never be)

| Secret | Where it actually lives |
|---|---|
| R2 / S3 access key id + secret | `~/.config/ndrchst/r2.env` on the host (chmod 600), referenced by the systemd unit via `EnvironmentFile=`; or GitHub Actions secrets for CI |
| Cloudflare API tokens | the operator's machine / CI only |
| RCON passwords | generated at runtime, stored in the SQLite DB under `~/.ndrchst/` |
| Server data, worlds, mod jars | `~/.ndrchst/` (outside the repo) |

`.gitignore` blocks `*.env`, `r2.env`, `*.secret`, `*.pem`, `*.key`, `*.db`,
and the data/build dirs as a backstop.

## Configuration is environment-driven

Everything deployment-specific is read from env vars at boot, never hardcoded:
`NDRCHST_R2_*`, `NDRCHST_EDGE_URL`, `NDRCHST_PUBLIC_HOST`,
`NDRCHST_TUNNEL_HOSTNAME`, `NDRCHST_CLIENT_DOWNLOADS_BASE`.
See [docs/distribution.md](docs/distribution.md). The `cf/worker/wrangler.toml`
carries a bucket name + route hostnames — those are non-secret deployment
identifiers; fork-and-edit them for your own deployment.

## Distribution trust

- Client binaries published to R2 carry a **SHA-256** in `latest.json`; the
  self-updater verifies it before swapping the running binary.
- The admin plane (`:8080`) is intended to be reachable on a private network
  only (e.g. Tailscale) — do **not** route it through a public tunnel. Only the
  read-only public surface (`:8081`) / the edge Worker faces the internet.

## Reporting

Found something that looks sensitive committed here, or a vulnerability? Open a
private security advisory on the GitHub repo rather than a public issue.
