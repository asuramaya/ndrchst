# systemd user units for ndrchst-01

Two user-level services run the box:

| Unit | Port | Purpose |
|---|---|---|
| `ndrchst-admin.service` | 8080 | Management plane. Tailscale-only — DO NOT route through Cloudflare. |
| `ndrchst-public.service` | 8081 | Public surface — pilot bundle downloads + read-only server list. Cloudflare Tunnel fronts this at `play.ndrchst.com`. |

## Install

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/ndrchst-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ndrchst-admin.service ndrchst-public.service

# Survive logout / boot
sudo loginctl enable-linger "$USER"
```

## Group membership gotcha

`systemctl --user` services inherit groups from `user@<uid>.service`, which is
spawned once when lingering is enabled. If the user was added to the `docker`
group **after** that, the service won't see it and Docker calls fail with
`PermissionError`. Fix:

```bash
sudo systemctl restart user@$(id -u).service
```

## Env

`ndrchst-admin.service` bakes in two env vars:

  - `NDRCHST_PUBLIC_HOST` — hostname MC clients dial (e.g. `mc.ndrchst.com`).
    A DNS-only (grey-cloud) A record pointing at the server.
  - `NDRCHST_EDGE_URL` — HTTPS base of the public surface (e.g.
    `https://play.ndrchst.com`). Surfaced in the pilot bundle README.

`ndrchst-public.service` reads `~/.config/ndrchst/public.env` (optional, chmod
600 — secrets never live in the repo or the unit file). Set at least:

  - `NDRCHST_SESSION_SECRET` — HMAC key for wallet session cookies. **If unset,
    a random per-process secret is used and every restart logs out all wallets.**
    Generate once and persist:

    ```bash
    install -m 700 -d ~/.config/ndrchst
    printf 'NDRCHST_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" \
      > ~/.config/ndrchst/public.env
    chmod 600 ~/.config/ndrchst/public.env
    systemctl --user restart ndrchst-public.service
    ```

  Optional in the same file: `NDRCHST_SOLANA_RPC` (custom RPC endpoint),
  `NDRCHST_TOKEN_MINT` (override the $NDRCHST mint), `NDRCHST_RANK_CMD`
  (RCON rank template, e.g. `lp user {name} parent set {tier}`).

To change them: edit the unit, `daemon-reload`, then restart. Existing
pilot bundles can be regenerated without recreating the server:

```bash
curl -X POST http://127.0.0.1:8080/servers/<sid>/pilot/regenerate
```
