"""Pilot launch glue — vanilla MC or NeoForge + modpack, dialled
through a Cloudflare tunnel if configured.

Three operating modes, picked from config flags:

  - vanilla: portablemc installs the given MC version, joins the
    configured server.
  - modded (no modpack): installs NeoForge for the given MC version.
  - modded + modpack: installs NeoForge, then fetches a CurseForge
    client pack URL → resolves all mods → applies overrides.

When TUNNEL_HOSTNAME is set, a cloudflared sidecar is started before
the MC client launches and MC dials the local cloudflared port instead
of SERVER_HOST:SERVER_PORT. Origin IP stays hidden behind CF edge.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from portablemc.forge import _NeoForgeVersion
from portablemc.standard import (
    AssetsResolveEvent,
    Context,
    DownloadCompleteEvent,
    DownloadProgressEvent,
    DownloadStartEvent,
    JarFoundEvent,
    JvmLoadedEvent,
    JvmLoadingEvent,
    LibrariesResolvedEvent,
    OfflineAuthSession,
    QuickPlayMultiplayer,
    Version,
    VersionLoadedEvent,
    VersionLoadingEvent,
    Watcher,
)


def _data_dir(app_slug: str) -> Path:
    return Path.home() / f".{app_slug}"


class _GuiWatcher(Watcher):
    """Translates portablemc events into one-line strings for the GUI."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._dl_total_bytes: int = 0
        self._dl_entries: int = 0
        self._dl_done: int = 0
        self._last_progress_emit: float = 0.0

    def handle(self, event) -> None:
        match event:
            case VersionLoadingEvent():
                self._emit(f"Loading Minecraft {event.version}…")
            case VersionLoadedEvent():
                self._emit(f"Loaded Minecraft {event.version}")
            case JvmLoadingEvent():
                self._emit("Loading Java runtime…")
            case JvmLoadedEvent():
                self._emit("Loaded Java runtime")
            case JarFoundEvent():
                self._emit("Found client jar")
            case LibrariesResolvedEvent():
                total = event.class_libs_count + event.native_libs_count
                self._emit(f"Resolved {total} libraries")
            case AssetsResolveEvent():
                if event.count is not None:
                    self._emit(f"Resolved {event.count} assets (index {event.index_version})")
            case DownloadStartEvent():
                self._dl_total_bytes = event.size
                self._dl_entries = event.entries_count
                self._dl_done = 0
                self._emit(f"Downloading {event.entries_count} files ({event.size/1e6:.1f} MB)…")
            case DownloadProgressEvent():
                if event.done:
                    self._dl_done += 1
                now = time.monotonic()
                if now - self._last_progress_emit >= 1.0:
                    self._last_progress_emit = now
                    pct = (self._dl_done / self._dl_entries * 100) if self._dl_entries else 0
                    speed = event.speed / 1e6
                    self._emit(f"  {self._dl_done}/{self._dl_entries} files ({pct:.0f}%) — {speed:.1f} MB/s")
            case DownloadCompleteEvent():
                self._emit("Downloads complete")


def _make_version(
    ctx: Context, mc_version: str, neoforge_version: Optional[str],
):
    """Pick the right portablemc Version subclass: NeoForge if a
    neoforge version is pinned, otherwise vanilla."""
    if neoforge_version:
        return _NeoForgeVersion(neoforge_version, context=ctx)
    return Version(mc_version, context=ctx)


def launch(
    *,
    app_slug: str,
    mc_version: str,
    username: str,
    server_host: str,
    server_port: int,
    on_log: Callable[[str], None],
    neoforge_version: Optional[str] = None,
    modpack_url: Optional[str] = None,
    mods_sync_url: Optional[str] = None,
    tunnel_hostname: Optional[str] = None,
    client_ram_mb: int = 8192,
) -> int:
    """Install + run MC in offline mode, auto-connecting to the server.

    Blocks until MC exits. Returns 0 on clean exit.
    """
    data_dir = _data_dir(app_slug)
    ctx = Context(main_dir=data_dir)

    version = _make_version(ctx, mc_version, neoforge_version)
    if neoforge_version:
        on_log(f"Installing NeoForge {neoforge_version} (MC {mc_version})…")
    else:
        on_log(f"Installing Minecraft {mc_version}…")

    # Modpack install runs BEFORE portablemc install because the
    # modpack drops mods into data_dir/mods/ which NeoForge then loads
    # at launch time. Order matters only for failure surfacing — if the
    # modpack install fails, we want to bail before downloading
    # gigabytes of MC assets.
    if modpack_url:
        on_log(f"Installing modpack from {modpack_url}…")
        from .modpack import install_client_pack
        try:
            n_mods, n_overrides = install_client_pack(
                url=modpack_url,
                profile_dir=data_dir,
                on_log=on_log,
                sync_base_url=mods_sync_url,
            )
            on_log(
                f"Modpack ready: {n_mods} mods synced, "
                f"{n_overrides} override files applied"
            )
        except Exception as e:
            on_log(f"Modpack install failed: {e}")
            raise

    # Tunnel sidecar. If configured, MC dials the local cloudflared
    # listener instead of the raw server host.
    dial_host = server_host
    dial_port = server_port
    tunnel = None
    if tunnel_hostname:
        from .tunnel import Tunnel, ensure_cloudflared
        cfd_path = ensure_cloudflared(data_dir, on_log=on_log)
        tunnel = Tunnel(tunnel_hostname, cfd_path, on_log=on_log)
        tunnel.start()
        dial_host = "127.0.0.1"
        dial_port = tunnel.port

    # Seed servers.dat so the target is pre-listed in the Multiplayer menu
    # (one-click join). Belt-and-suspenders alongside quick-play: heavily
    # modded title screens sometimes swallow the quick-play auto-connect,
    # but the server-list entry is always there.
    try:
        from .servers_dat import write_servers_dat
        server_name = app_slug.replace("-", " ").title()
        write_servers_dat(
            data_dir / "servers.dat",
            [(server_name, f"{dial_host}:{dial_port}")],
        )
        on_log(f"Server listed in Multiplayer menu as {dial_host}:{dial_port}")
    except Exception as e:
        on_log(f"(couldn't seed servers.dat: {e})")

    version.auth_session = OfflineAuthSession(username, uuid4().hex)
    version.quick_play = QuickPlayMultiplayer(dial_host, dial_port)
    env = version.install(watcher=_GuiWatcher(on_log))

    # Client JVM tuning. portablemc launches with no -Xmx (JVM default)
    # and SerialGC — fine for vanilla, fatal for a 400+ mod pack: it
    # thrashes and dies during client-side world load. Allocate a real
    # modded heap + G1GC.
    ram_mb = max(client_ram_mb, 2048)
    xms_mb = min(2048, ram_mb)
    env.jvm_args[:0] = [
        f"-Xmx{ram_mb}m",
        f"-Xms{xms_mb}m",
        "-XX:+UseG1GC",
        "-XX:+ParallelRefProcEnabled",
        "-XX:MaxGCPauseMillis=200",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:G1NewSizePercent=30",
        "-XX:G1MaxNewSizePercent=40",
        "-XX:G1HeapRegionSize=8M",
        "-XX:G1ReservePercent=20",
    ]
    on_log(f"Client heap: -Xmx{ram_mb}m (G1GC)")

    on_log(
        f"Starting Minecraft as {username}, connecting to "
        f"{dial_host}:{dial_port}"
        + (f" (via {tunnel_hostname})" if tunnel_hostname else "")
        + "…",
    )
    try:
        env.run()
        return 0
    finally:
        if tunnel is not None:
            tunnel.stop()
