"""Optional Microsoft (premium) sign-in for online-mode / vanilla servers.

The default launch path is offline mode (a plain username) — the right choice
for the offline-mode servers ndrchst usually runs. When a server runs in
online-mode, a player needs a real Mojang/Microsoft account; this module does
that login and hands :func:`launcher.launch` a ``MicrosoftAuthSession``.

The flow is the same one portablemc's own CLI uses: open the Microsoft login
page in the browser, catch the redirect on a localhost port, exchange the code
for a token. Tokens are cached in the client data dir (via portablemc's
``AuthDatabase``) and refreshed in place, so sign-in is a one-time step.

Reuses portablemc's hosted redirect (theorozier.fr/portablemc/auth) and Azure
app id — the same trust assumptions as `portablemc login`, which the client
already depends on.
"""
from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from portablemc.auth import AuthDatabase, MicrosoftAuthSession

# portablemc's public Azure app + hosted redirect. The redirect page bounces the
# OAuth response fragment back to http://127.0.0.1:<port>/ as a query string.
_APP_ID = "708e91b5-99f8-4a1d-80ec-e746cbb24771"
_REDIRECT_URI = "https://www.theorozier.fr/portablemc/auth"
_AUTH_DB_FILE = "ms-auth.json"


class OnlineAuthError(RuntimeError):
    """Microsoft sign-in failed or was cancelled."""


def _auth_url(email: str, nonce: str, state: str) -> str:
    return "https://login.live.com/oauth20_authorize.srf?" + urllib.parse.urlencode({
        "client_id": _APP_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code id_token",
        "scope": "xboxlive.signin offline_access openid email",
        "login_hint": email,
        "nonce": nonce,
        "state": state,
        "prompt": "login",
        "response_mode": "fragment",
    })


def _catch_redirect(on_log: Callable[[str], None], email: str, nonce: str) -> str:
    """Open the browser to the MS login page and block until the hosted redirect
    posts the OAuth response back to a localhost port. Returns the raw query."""
    import webbrowser

    class _Server(HTTPServer):
        def __init__(self) -> None:
            super().__init__(("127.0.0.1", 0), _Handler)
            self.timeout = 0.5
            self.auth_query: str | None = None

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("", "/"):
                self.server.auth_query = parsed.query  # type: ignore[attr-defined]
                self.send_response(200)
            else:
                self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.flush()

    with _Server() as server:
        url = _auth_url(email, nonce, f"port:{server.server_port}")
        if not webbrowser.open(url):
            raise OnlineAuthError(
                "couldn't open a browser — open this URL manually:\n" + url)
        on_log("Opened Microsoft sign-in in your browser; waiting…")
        while server.auth_query is None:
            server.handle_request()
        return server.auth_query


def sign_in(*, data_dir: Path, email: str,
            on_log: Callable[[str], None]) -> MicrosoftAuthSession:
    """Return a usable Microsoft auth session for ``email``, reusing a cached
    token when possible. Raises :class:`OnlineAuthError` on failure."""
    email = email.strip()
    if not email:
        raise OnlineAuthError("enter your Microsoft account email first")

    data_dir.mkdir(parents=True, exist_ok=True)
    db = AuthDatabase(data_dir / _AUTH_DB_FILE)
    db.load()

    cached = db.get(email, MicrosoftAuthSession)
    if cached is not None:
        try:
            if not cached.validate():
                cached.refresh()
            on_log(f"Signed in as {cached.username} (cached).")
            db.put(email, cached)
            db.save()
            return cached
        except Exception:
            on_log("Cached Microsoft token expired — signing in again.")

    query = _catch_redirect(on_log, email, nonce := uuid4().hex)
    qs = urllib.parse.parse_qs(query)
    if "code" not in qs or "id_token" not in qs:
        raise OnlineAuthError("Microsoft sign-in was cancelled or denied")
    if not MicrosoftAuthSession.check_token_id(qs["id_token"][0], email, nonce):
        raise OnlineAuthError("sign-in response didn't match that email")

    session = MicrosoftAuthSession.authenticate(
        db.get_client_id(), _APP_ID, qs["code"][0], _REDIRECT_URI)
    db.put(email, session)
    db.save()
    on_log(f"Signed in as {session.username}.")
    return session
