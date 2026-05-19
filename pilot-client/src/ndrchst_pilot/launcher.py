import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

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


def launch(
    *,
    app_slug: str,
    mc_version: str,
    username: str,
    server_host: str,
    server_port: int,
    on_log: Callable[[str], None],
) -> int:
    """Install + run MC in offline mode, auto-connecting to the given server.

    Blocks until MC exits. Returns 0 on clean exit.
    """
    data_dir = _data_dir(app_slug)
    ctx = Context(main_dir=data_dir)
    version = Version(mc_version, context=ctx)
    version.auth_session = OfflineAuthSession(username, uuid4().hex)
    version.quick_play = QuickPlayMultiplayer(server_host, server_port)

    on_log(f"Installing {mc_version} into {data_dir} …")
    env = version.install(watcher=_GuiWatcher(on_log))

    on_log(f"Starting Minecraft as {username}, connecting to {server_host}:{server_port}…")
    env.run()
    return 0
