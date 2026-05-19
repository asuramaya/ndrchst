"""Minimal tkinter front-end for the pilot.

Kept deliberately small — the value is in the backend (install, mod
sync, tunnel, launch). This is a one-window launcher: server info, a
name field, a Play button, a phase label + progress bar, and a
collapsible log. The same binary serves any server via settings.load().
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from .launcher import launch
from .settings import load as load_config

APP_SLUG = "ndrchst-pilot"

# Phases the backend walks through, in order, for the progress bar's
# coarse position. The backend emits free-text logs; we match a few
# keywords to advance the phase indicator.
_PHASES = [
    ("modpack", "Installing modpack"),
    ("Syncing mods", "Syncing mods from server"),
    ("NeoForge", "Installing NeoForge"),
    ("Downloading", "Downloading Minecraft"),
    ("cloudflared", "Connecting via secure tunnel"),
    ("Starting Minecraft", "Launching Minecraft"),
]


def run() -> None:
    cfg = load_config()

    root = tk.Tk()
    root.title(cfg.app_name)
    root.geometry("620x460")
    root.minsize(520, 380)

    info = ttk.Frame(root, padding=12)
    info.pack(fill=tk.X)
    ttk.Label(info, text=cfg.app_name, font=("", 15, "bold")).pack(anchor="w")
    target = cfg.tunnel_hostname or f"{cfg.server_host}:{cfg.server_port}"
    ttk.Label(info, text=f"Server: {target}").pack(anchor="w")
    sub = f"Minecraft {cfg.mc_version}"
    if cfg.neoforge_version:
        sub += f" · NeoForge {cfg.neoforge_version}"
    if cfg.modpack_url:
        sub += " · modded"
    ttk.Label(info, text=sub, foreground="#888").pack(anchor="w")

    form = ttk.Frame(root, padding=(12, 4))
    form.pack(fill=tk.X)
    ttk.Label(form, text="In-game name:").pack(side=tk.LEFT)
    name_var = tk.StringVar(value=cfg.default_username)
    name_entry = ttk.Entry(form, textvariable=name_var, width=20)
    name_entry.pack(side=tk.LEFT, padx=8)

    ttk.Label(form, text="RAM (GB):").pack(side=tk.LEFT)
    ram_var = tk.StringVar(value="8")
    ram_spin = ttk.Spinbox(form, from_=2, to=32, increment=1, width=4,
                           textvariable=ram_var)
    ram_spin.pack(side=tk.LEFT, padx=8)

    launch_btn = ttk.Button(form, text="Play")
    launch_btn.pack(side=tk.LEFT, padx=8)

    # Phase + progress
    prog_frame = ttk.Frame(root, padding=(12, 4))
    prog_frame.pack(fill=tk.X)
    phase_var = tk.StringVar(value="Ready")
    ttk.Label(prog_frame, textvariable=phase_var).pack(anchor="w")
    bar = ttk.Progressbar(prog_frame, mode="determinate", maximum=len(_PHASES))
    bar.pack(fill=tk.X, pady=4)

    # Collapsible log
    log_box = tk.Text(root, height=12, wrap="word", state=tk.DISABLED,
                      font=("monospace", 9))
    log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

    def append_log(line: str) -> None:
        log_box.config(state=tk.NORMAL)
        log_box.insert(tk.END, line + "\n")
        log_box.see(tk.END)
        log_box.config(state=tk.DISABLED)
        # Advance phase indicator on keyword match.
        for i, (kw, label) in enumerate(_PHASES):
            if kw in line:
                phase_var.set(label)
                bar["value"] = i + 1
                break

    def emit_from_worker(line: str) -> None:
        root.after(0, append_log, line)

    def on_launch() -> None:
        username = (name_var.get() or cfg.default_username).strip()
        if not username:
            append_log("Username cannot be empty.")
            return
        try:
            ram_gb = max(2, int(float(ram_var.get())))
        except ValueError:
            ram_gb = 8
        launch_btn.config(state=tk.DISABLED, text="Running…")
        name_entry.config(state=tk.DISABLED)
        ram_spin.config(state=tk.DISABLED)
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
                )
                emit_from_worker("Minecraft exited.")
                root.after(0, lambda: phase_var.set("Minecraft exited"))
            except Exception as exc:
                emit_from_worker(f"Error: {exc!r}")
                root.after(0, lambda: phase_var.set("Error — see log"))
            finally:
                root.after(0, lambda: (
                    launch_btn.config(state=tk.NORMAL, text="Play"),
                    name_entry.config(state=tk.NORMAL),
                    ram_spin.config(state=tk.NORMAL),
                ))

        threading.Thread(target=worker, daemon=True).start()

    launch_btn.config(command=on_launch)
    root.mainloop()
