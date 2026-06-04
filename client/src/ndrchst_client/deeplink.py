"""``ndrchst://`` deep links — open the installed client straight from the web.

The website builds links like::

    ndrchst://launch?sid=<server_id>

Once the client is installed it registers itself as the handler for the
``ndrchst`` URL scheme (see desktop.py), so the OS launches this binary with the
URL as an argument. :func:`parse` turns that into a :class:`DeepLink` the app
acts on:

  * ``sid``  — the server to play; the app fetches that server's ``config.json``
    and writes it as ``client-config.json`` (via ``NDRCHST_CLIENT_CONFIG``) so a
    generic build becomes pinned to that server.

Single-instance: the OS spawns a NEW process for each clicked link. The first
instance binds a localhost port and records it; later instances forward their
URL to it and exit, so a click drops into the already-open window instead of
opening a second one. Everything here is best-effort — any failure falls back to
"just run normally".
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

SCHEME = "ndrchst"
_DATA_DIR = os.path.join(os.path.expanduser("~"), ".ndrchst-client")
_INSTANCE_FILE = os.path.join(_DATA_DIR, "instance.json")
_UA = "ndrchst-client-deeplink"


@dataclass(frozen=True, slots=True)
class DeepLink:
    action: str             # e.g. "launch"
    params: dict[str, str]  # query params (sid, code, …)


def parse(raw: str | None) -> DeepLink | None:
    """Parse a ``ndrchst://`` URL into a :class:`DeepLink`, or None if it isn't
    one. Tolerates surrounding quotes (Windows passes ``"%1"``)."""
    if not raw:
        return None
    raw = raw.strip().strip('"')
    try:
        u = urllib.parse.urlsplit(raw)
    except ValueError:
        return None
    if u.scheme != SCHEME:
        return None
    # ndrchst://launch?…  → netloc="launch";  ndrchst:///launch?…  → path
    action = (u.netloc or u.path.lstrip("/")).strip().lower()
    if not action:
        return None
    params = dict(urllib.parse.parse_qsl(u.query))
    return DeepLink(action=action, params=params)


def url_from_argv(argv: list[str]) -> str | None:
    """The first ``ndrchst://`` argument in ``argv``, if any."""
    for a in argv:
        if a.startswith(SCHEME + "://"):
            return a
    return None


def list_servers(base_url: str) -> list[dict]:
    """Fetch the public server catalog — the static ``servers.json`` the edge
    serves from R2 — so a generic build can discover and pin a server WITHOUT a
    deep link. Catalog metadata only (id, name, status, config_url). Raises on
    network/parse failure."""
    url = f"{base_url.rstrip('/')}/servers.json"
    req = urllib.request.Request(url, headers={"user-agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data if isinstance(data, list) else []


def fetch_server_config(base_url: str, server_id: str) -> str:
    """Pull a server's published ``config.json`` and write it where settings.load
    will pick it up (via ``NDRCHST_CLIENT_CONFIG``), pinning a generic build to
    that server. Returns the path written. Raises on network/parse failure."""
    url = f"{base_url.rstrip('/')}/client/{urllib.parse.quote(server_id)}/config.json"
    req = urllib.request.Request(url, headers={"user-agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
    json.loads(data)  # validate before persisting
    os.makedirs(_DATA_DIR, exist_ok=True)
    dest = os.path.join(_DATA_DIR, "client-config.json")
    with open(dest, "wb") as f:
        f.write(data)
    # Highest-priority config source for settings.load() — beats the baked
    # defaults and any stale file next to the exe.
    os.environ["NDRCHST_CLIENT_CONFIG"] = dest
    return dest


# ---- single-instance forwarding ---------------------------------------------

def _write_instance(port: int, token: str) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_INSTANCE_FILE, "w") as f:
            json.dump({"port": port, "token": token}, f)
    except OSError:
        pass


def _read_instance() -> tuple[int, str] | None:
    try:
        with open(_INSTANCE_FILE) as f:
            d = json.load(f)
        return int(d["port"]), str(d["token"])
    except (OSError, ValueError, KeyError):
        return None


def try_forward(url: str) -> bool:
    """If another instance is listening, hand it the URL and return True. False
    means there's no live primary (the caller should become primary)."""
    info = _read_instance()
    if not info:
        return False
    port, token = info
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as s:
            s.sendall((token + "\n" + url + "\n").encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
        return True
    except OSError:
        return False


def start_listener(on_url: Callable[[str], None]) -> bool:
    """Become the primary instance: bind a localhost port, record it, and route
    any URL pushed by a later instance to ``on_url``. Best-effort; True if the
    listener started. The shared token (in the user-only instance file) keeps a
    stray local process from injecting URLs into the handler."""
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
    except OSError:
        return False
    token = secrets.token_urlsafe(16)
    _write_instance(srv.getsockname()[1], token)

    def _serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2.0)
                chunks: list[bytes] = []
                try:
                    while True:
                        b = conn.recv(4096)
                        if not b:
                            break
                        chunks.append(b)
                except OSError:
                    continue
                msg = b"".join(chunks).decode("utf-8", "replace")
                got, _, url = msg.partition("\n")
                if got.strip() == token and url.strip():
                    on_url(url.strip())

    threading.Thread(target=_serve, daemon=True).start()
    return True
