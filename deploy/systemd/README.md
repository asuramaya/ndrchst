# systemd user units (the box)

User-level services run the box:

| Unit | Port | Purpose |
|---|---|---|
| `ndrchst-admin.service` | 8080 | Management plane. Tailscale-only — DO NOT route through Cloudflare. |
| `ndr-mc-forward.service` | 25567 | Kernel-clean TCP forwarder for the cross-play Java server, bypassing Docker's userland docker-proxy (which stalls on this box's constrained ~1.6 Mbps uplink's TCP backpressure → keepalive kicks). playit's Java tunnel local address must point at `127.0.0.1:25567`. |

Client downloads + the server list are served **statically** from R2 by the
Cloudflare Worker (`cf/worker/`) at `play.ndrchst.com` — there's no public box
service to run.

## ndr-mc-forward (docker-proxy bypass)

`ndr-mc-forward.sh` runs `socat 127.0.0.1:25567 → <container-ip>:25565`, re-resolving
the container IP every 10s so it self-heals across container restart/recreate and
box reboot. It listens on **25567, deliberately NOT the docker-publish port (25566)**:
a forwarder squatting 25566 makes docker-proxy's bind fail when the container
restarts → the restart fails and orphans the container's network
(`NetworkSettings.Networks: {}`, DNS dead inside). If that ever happens, recover with
`docker network connect bridge <container>` then a clean `docker restart`.

```bash
cp deploy/systemd/ndr-mc-forward.sh   ~/.local/bin/ && chmod +x ~/.local/bin/ndr-mc-forward.sh
cp deploy/systemd/ndr-mc-forward.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ndr-mc-forward.service
```

Override the container/ports via the unit's `Environment=` (`NDR_CONTAINER`,
`NDR_LISTEN_PORT`, `NDR_TARGET_PORT`). Root cause + the uplink ceiling are
documented in the cross-play keepalive memory.

## Install

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/ndrchst-admin.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ndrchst-admin.service

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
  - `NDRCHST_EDGE_URL` — HTTPS base of the edge (e.g. `https://play.ndrchst.com`)
    where clients self-update and fetch artifacts. Surfaced in the client bundle.

To change them: edit the unit, `daemon-reload`, then restart. Existing
client bundles can be regenerated without recreating the server:

```bash
curl -X POST http://127.0.0.1:8080/servers/<sid>/client/regenerate
```
