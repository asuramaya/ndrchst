#!/usr/bin/env bash
# ndr-mc-forward — kernel-clean TCP forwarder for the cross-play Java server.
#
# WHY: the server is published on 127.0.0.1, which forces Docker's *userland*
# docker-proxy into the data path. On this box's constrained uplink (~1.6 Mbps
# up) the server→client chunk stream is in near-permanent TCP zero-window
# backpressure, and docker-proxy does not recover from it — it stalls, the
# keepalive packets head-of-line-block behind undeliverable chunk bytes, and the
# player is kicked at ~30s. A kernel TCP path (socat → the container IP over
# docker0) drains the same backpressure correctly.
#
# HOW: listen on 127.0.0.1:$LISTEN_PORT and forward to the container's bridge IP.
# playit points its Java tunnel at $LISTEN_PORT. We re-resolve the container IP
# every tick so a container restart/recreate (new IP) self-heals with no manual
# step. Runs as a --user unit (ndrchst-01 is in the docker group); $LISTEN_PORT
# differs from the docker publish (25566) so the two never fight for the port.
set -u

CONTAINER="${NDR_CONTAINER:-ndrchst-7b8382e9cc70}"
LISTEN_PORT="${NDR_LISTEN_PORT:-25599}"
TARGET_PORT="${NDR_TARGET_PORT:-25565}"

socat_pid=""
last_ip=""

cleanup() { [ -n "$socat_pid" ] && kill "$socat_pid" 2>/dev/null; exit 0; }
trap cleanup TERM INT

while true; do
  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
  alive=1
  { [ -n "$socat_pid" ] && kill -0 "$socat_pid" 2>/dev/null; } || alive=0
  if [ -n "$ip" ] && { [ "$ip" != "$last_ip" ] || [ "$alive" = 0 ]; }; then
    [ -n "$socat_pid" ] && kill "$socat_pid" 2>/dev/null
    socat "TCP-LISTEN:${LISTEN_PORT},bind=127.0.0.1,reuseaddr,fork" "TCP:${ip}:${TARGET_PORT}" &
    socat_pid=$!
    last_ip="$ip"
    logger -t ndr-mc-forward "forwarding 127.0.0.1:${LISTEN_PORT} -> ${ip}:${TARGET_PORT} (pid ${socat_pid})"
  fi
  sleep 10
done
