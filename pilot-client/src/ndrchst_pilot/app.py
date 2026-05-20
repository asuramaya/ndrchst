"""Minimal tkinter front-end for the pilot.

Kept deliberately small — the value is in the backend (install, mod
sync, tunnel, launch). One window: server info, the launch controls
(name, RAM, GPU), a Play button, a phase label + progress bar, and a
log pane. The same binary serves any server via settings.load().
"""
from __future__ import annotations

import platform
import threading
import tkinter as tk
from tkinter import ttk

from pathlib import Path

from . import __version__, desktop, updater
from .launcher import launch
from .settings import load as load_config

APP_SLUG = "ndrchst-pilot"

# Dark palette so the launcher matches the ndrchst web surfaces.
_BG = "#0f1115"
_PANEL = "#1a1d24"
_FG = "#e4e6eb"
_MUTED = "#97a0b0"
_ACCENT = "#4d7cfe"

# Phases the backend walks through, in order, for the progress bar's
# coarse position. The backend emits free-text logs; we match a few
# keywords to advance the phase indicator.
_PHASES = [
    ("modpack", "Installing modpack"),
    ("Syncing assets", "Syncing assets from server"),
    ("Applying override", "Applying configs"),
    ("NeoForge", "Installing NeoForge"),
    ("Downloading", "Downloading Minecraft"),
    ("pre-warmed", "Securing tunnel"),
    ("cloudflared", "Connecting via secure tunnel"),
    ("Starting Minecraft", "Launching Minecraft"),
]

# Label → value passed to launch(gpu=...).
_GPU_CHOICES = [
    ("Auto", "auto"),
    ("Integrated GPU", "integrated"),
    ("Discrete GPU (NVIDIA)", "discrete"),
]


def run() -> None:
    cfg = load_config()

    root = tk.Tk()
    root.title(cfg.app_name)
    root.geometry("640x500")
    root.minsize(560, 440)
    root.configure(bg=_BG)

    style = ttk.Style(root)
    with_theme = "clam" in style.theme_names()
    if with_theme:
        style.theme_use("clam")
    style.configure("TFrame", background=_BG)
    style.configure("Panel.TFrame", background=_PANEL)
    style.configure("TLabel", background=_BG, foreground=_FG)
    style.configure("Muted.TLabel", background=_BG, foreground=_MUTED)
    style.configure("Title.TLabel", background=_BG, foreground=_FG,
                    font=("", 16, "bold"))
    style.configure("Accent.TButton", font=("", 11, "bold"))
    style.configure("TProgressbar", background=_ACCENT, troughcolor=_PANEL)

    # ---- Header: server identity --------------------------------------
    info = ttk.Frame(root, padding=(16, 14, 16, 8))
    info.pack(fill=tk.X)
    ttk.Label(info, text=cfg.app_name, style="Title.TLabel").pack(anchor="w")
    target = cfg.tunnel_hostname or f"{cfg.server_host}:{cfg.server_port}"
    ttk.Label(info, text=f"Server  {target}", style="Muted.TLabel").pack(anchor="w")
    sub = f"Minecraft {cfg.mc_version}"
    if cfg.neoforge_version:
        sub += f"  ·  NeoForge {cfg.neoforge_version}"
    if cfg.modpack_url:
        sub += "  ·  modded"
    sub += f"  ·  pilot {__version__}"
    ttk.Label(info, text=sub, style="Muted.TLabel").pack(anchor="w")

    # Update banner — hidden until a newer build is found on the CDN.
    style.configure("Update.TFrame", background="#13351f")
    update_bar = ttk.Frame(root, padding=(16, 8), style="Update.TFrame")
    update_msg = tk.StringVar()
    update_lbl = ttk.Label(update_bar, textvariable=update_msg,
                           background="#13351f", foreground="#c7f9d8")
    update_lbl.pack(side=tk.LEFT)
    update_btn = ttk.Button(update_bar, text="Update & restart")
    update_btn.pack(side=tk.RIGHT)

    ttk.Separator(root).pack(fill=tk.X, padx=16, pady=4)

    # ---- Controls: name / RAM / GPU on a grid -------------------------
    form = ttk.Frame(root, padding=(16, 6))
    form.pack(fill=tk.X)
    form.columnconfigure(1, weight=1)

    ttk.Label(form, text="In-game name").grid(row=0, column=0, sticky="w", pady=4)
    name_var = tk.StringVar(value=cfg.default_username)
    name_entry = ttk.Entry(form, textvariable=name_var)
    name_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)

    ttk.Label(form, text="Memory (GB)").grid(row=1, column=0, sticky="w", pady=4)
    ram_var = tk.StringVar(value="8")
    ram_spin = ttk.Spinbox(form, from_=2, to=32, increment=1, width=6,
                           textvariable=ram_var)
    ram_spin.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=4)

    ttk.Label(form, text="Graphics").grid(row=2, column=0, sticky="w", pady=4)
    gpu_var = tk.StringVar(value=_GPU_CHOICES[0][0])
    gpu_combo = ttk.Combobox(
        form, textvariable=gpu_var, state="readonly", width=24,
        values=[label for label, _ in _GPU_CHOICES],
    )
    gpu_combo.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=4)
    # The GPU picker only changes behaviour on Linux hybrid laptops.
    if platform.system() != "Linux":
        gpu_combo.configure(state="disabled")

    # ---- Play + progress ----------------------------------------------
    action = ttk.Frame(root, padding=(16, 8))
    action.pack(fill=tk.X)
    launch_btn = ttk.Button(action, text="Play", style="Accent.TButton")
    launch_btn.pack(side=tk.LEFT)
    phase_var = tk.StringVar(value="Ready")
    ttk.Label(action, textvariable=phase_var, style="Muted.TLabel").pack(
        side=tk.LEFT, padx=12)

    bar = ttk.Progressbar(root, mode="determinate", maximum=len(_PHASES))
    bar.pack(fill=tk.X, padx=16, pady=(0, 6))

    # ---- Log pane ------------------------------------------------------
    log_box = tk.Text(root, height=12, wrap="word", state=tk.DISABLED,
                      bg=_PANEL, fg=_MUTED, insertbackground=_FG,
                      relief="flat", font=("monospace", 9), padx=10, pady=8)
    log_box.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

    def append_log(line: str) -> None:
        log_box.config(state=tk.NORMAL)
        log_box.insert(tk.END, line + "\n")
        log_box.see(tk.END)
        log_box.config(state=tk.DISABLED)
        for i, (kw, label) in enumerate(_PHASES):
            if kw in line:
                phase_var.set(label)
                bar["value"] = i + 1
                break

    def emit_from_worker(line: str) -> None:
        root.after(0, append_log, line)

    def set_controls(enabled: bool) -> None:
        widget_state = tk.NORMAL if enabled else tk.DISABLED
        name_entry.config(state=widget_state)
        ram_spin.config(state=widget_state)
        if platform.system() == "Linux":
            gpu_combo.config(state="readonly" if enabled else tk.DISABLED)
        launch_btn.config(
            state=widget_state, text="Play" if enabled else "Running…")

    def on_launch() -> None:
        username = (name_var.get() or cfg.default_username).strip()
        if not username:
            append_log("Username cannot be empty.")
            return
        try:
            ram_gb = max(2, int(float(ram_var.get())))
        except ValueError:
            ram_gb = 8
        gpu = dict(_GPU_CHOICES).get(gpu_var.get(), "auto")
        set_controls(False)
        phase_var.set("Starting…")

        def worker() -> None:
            try:
                launch(
                    app_slug=APP_SLUG,
                    mc_version=cfg.mc_version,
                    username=username,
                    server_host=cfg.server_host,
                    server_port=cfg.server_port,
                    on_log=emit_from_worker,
                    neoforge_version=cfg.neoforge_version,
                    modpack_url=cfg.modpack_url,
                    mods_sync_url=cfg.mods_sync_url,
                    tunnel_hostname=cfg.tunnel_hostname,
                    client_ram_mb=ram_gb * 1024,
                    gpu=gpu,
                )
                emit_from_worker("Minecraft exited.")
                root.after(0, lambda: phase_var.set("Minecraft exited"))
            except Exception as exc:
                emit_from_worker(f"Error: {exc!r}")
                root.after(0, lambda: phase_var.set("Error — see log"))
            finally:
                root.after(0, lambda: set_controls(True))

        threading.Thread(target=worker, daemon=True).start()

    # ---- Self-update -------------------------------------------------
    def do_update(info) -> None:
        update_btn.config(state=tk.DISABLED, text="Updating…")

        def worker() -> None:
            ok = updater.self_update(info, on_log=emit_from_worker)
            # On success the process re-execs (POSIX) or exits (Windows);
            # if we're still here it didn't apply — re-enable the button.
            if not ok:
                root.after(0, lambda: update_btn.config(
                    state=tk.NORMAL, text="Update & restart"))

        threading.Thread(target=worker, daemon=True).start()

    def show_update(info) -> None:
        update_msg.set(f"Update available — pilot {info.version}   ")
        update_btn.config(command=lambda: do_update(info))
        update_bar.pack(fill=tk.X, after=info)
        append_log(
            f"Update {info.version} available"
            + (f" — {info.notes}" if info.notes else "")
        )

    def check_updates() -> None:
        found = updater.check(cfg.update_base_url or "")
        if found is not None:
            root.after(0, show_update, found)

    if cfg.update_base_url:
        threading.Thread(target=check_updates, daemon=True).start()

    # First-run desktop integration (frozen builds only; idempotent).
    threading.Thread(
        target=lambda: desktop.ensure_installed_once(
            app_name=cfg.app_name,
            data_dir=Path.home() / f".{APP_SLUG}",
            on_log=emit_from_worker,
        ),
        daemon=True,
    ).start()

    launch_btn.config(command=on_launch)
    root.mainloop()
