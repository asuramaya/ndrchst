"""Cloudflared sidecar — tunnels MC traffic through the public edge.

Pattern:
  - On first launch we download a `cloudflared` binary for the local
    platform and cache it in the pilot's data dir.
  - At launch time we spawn `cloudflared access tcp` listening on
    127.0.0.1:<auto-port>, which tunnels every accepted TCP connection
    to `wss://<hostname>` at the Cloudflare edge → through the tunnel
    to the origin (ATM10 server).
  - Minecraft is pointed at the local port. Origin IP never leaves
    the tunnel.

Cancellation: closing the Tunnel object terminates the cloudflared
subprocess and waits for it. We don't manage tunnel state on disk —
each pilot run starts a fresh listener and tears it down on exit.
"""
from __future__ import annotations

import platform as _platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Per-OS cloudflared binary URLs (always-latest). We pin to "latest"
# because cloudflared is widely deployed and the protocol with CF edge
# stays stable; a stale binary fails closed rather than corrupting
# anything.
_CFD_URLS = {
    ("Linux", "x86_64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("Linux", "aarch64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("Darwin", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("Darwin", "arm64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
    ("Windows", "AMD64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}


class TunnelError(RuntimeError):
    pass


def _cfd_binary_path(data_dir: Path) -> Path:
    suffix = ".exe" if _platform.system() == "Windows" else ""
    return data_dir / "bin" / f"cloudflared{suffix}"


def _platform_key() -> tuple[str, str]:
    return (_platform.system(), _platform.machine())


def ensure_cloudflared(data_dir: Path, *, on_log) -> Path:
    """Download cloudflared into <data_dir>/bin/ on first run; return the
    binary path. Idempotent — if the binary exists we just return it."""
    target = _cfd_binary_path(data_dir)
    if target.exists():
        return target
    key = _platform_key()
    if key not in _CFD_URLS:
        raise TunnelError(
            f"no cloudflared binary URL configured for {key}; "
            f"download manually to {target}"
        )
    url = _CFD_URLS[key]
    target.parent.mkdir(parents=True, exist_ok=True)
    on_log(f"Downloading cloudflared for {key[0]}/{key[1]}…")

    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise TunnelError(f"failed to fetch cloudflared from {url}: {e}") from e

    # macOS ships its binaries as tgz; everything else is the raw binary.
    if url.endswith(".tgz"):
        import tarfile
        on_log("Extracting cloudflared archive…")
        with tarfile.open(tmp) as tf:
            member = next(
                (m for m in tf.getmembers() if m.isfile() and m.name.endswith("cloudflared")),
                None,
            )
            if member is None:
                tmp.unlink(missing_ok=True)
                raise TunnelError("cloudflared.tgz had no cloudflared binary inside")
            with tf.extractfile(member) as src, target.open("wb") as dst:
                while True:
                    buf = src.read(256 * 1024)
                    if not buf:
                        break
                    dst.write(buf)
        tmp.unlink(missing_ok=True)
    else:
        tmp.rename(target)
    target.chmod(0o755)
    on_log(f"cloudflared cached at {target}")
    return target


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_free_port(preferred: int = 25565) -> int:
    """Prefer a fixed port (so servers.dat / Direct Connect to a known
    address works) and fall back to an OS-assigned ephemeral one if it's
    taken."""
    if _port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnel:
    """A live cloudflared access tcp listener. Use as a context manager."""

    def __init__(self, hostname: str, cfd_path: Path, *, on_log):
        self.hostname = hostname
        self._cfd_path = cfd_path
        self._on_log = on_log
        self._port: int | None = None
        self._proc: subprocess.Popen | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise TunnelError("tunnel not yet started")
        return self._port

    def start(self, *, wait_seconds: float = 8.0) -> None:
        self._port = _pick_free_port()
        cmd = [
            str(self._cfd_path),
            "access", "tcp",
            "--hostname", self.hostname,
            "--url", f"127.0.0.1:{self._port}",
        ]
        self._on_log(
            f"Starting cloudflared sidecar: {self.hostname} → 127.0.0.1:{self._port}"
        )
        # Pipe stderr+stdout so we can see errors if it crashes early.
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        # Wait for the local port to actually start accepting connections.
        # cloudflared logs "Start Websocket listener host=127.0.0.1:<port>"
        # once it's ready — but the easiest check is just "can we connect."
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise TunnelError(
                    f"cloudflared exited early with code {self._proc.returncode}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.2)
        # Timed out
        self.stop()
        raise TunnelError(
            f"cloudflared sidecar didn't open 127.0.0.1:{self._port} within "
            f"{wait_seconds}s — check your network connection to Cloudflare"
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._on_log("Cloudflared sidecar stopped")
        self._proc = None
        self._port = None

    def __enter__(self) -> "Tunnel":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
