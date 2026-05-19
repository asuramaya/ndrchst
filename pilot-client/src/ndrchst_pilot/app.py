import threading
import tkinter as tk
from tkinter import ttk

from . import config
from .launcher import launch

APP_SLUG = "ndrchst-pilot"


def run() -> None:
    root = tk.Tk()
    root.title(config.APP_NAME)
    root.geometry("600x420")

    info = ttk.Frame(root, padding=12)
    info.pack(fill=tk.X)
    ttk.Label(info, text=config.APP_NAME, font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(info, text=f"Server: {config.SERVER_HOST}:{config.SERVER_PORT}").pack(anchor="w")
    ttk.Label(info, text=f"Minecraft version: {config.MC_VERSION}").pack(anchor="w")

    form = ttk.Frame(root, padding=(12, 4))
    form.pack(fill=tk.X)
    ttk.Label(form, text="In-game name:").pack(side=tk.LEFT)
    name_var = tk.StringVar(value=config.DEFAULT_USERNAME)
    name_entry = ttk.Entry(form, textvariable=name_var, width=24)
    name_entry.pack(side=tk.LEFT, padx=8)

    launch_btn = ttk.Button(form, text="Launch")
    launch_btn.pack(side=tk.LEFT, padx=8)

    log_box = tk.Text(root, height=14, wrap="word", state=tk.DISABLED)
    log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    def append_log(line: str) -> None:
        log_box.config(state=tk.NORMAL)
        log_box.insert(tk.END, line + "\n")
        log_box.see(tk.END)
        log_box.config(state=tk.DISABLED)

    def emit_from_worker(line: str) -> None:
        root.after(0, append_log, line)

    def on_launch() -> None:
        username = (name_var.get() or config.DEFAULT_USERNAME).strip()
        if not username:
            append_log("Username cannot be empty.")
            return
        launch_btn.config(state=tk.DISABLED, text="Running…")
        name_entry.config(state=tk.DISABLED)

        def worker() -> None:
            try:
                launch(
                    app_slug=APP_SLUG,
                    mc_version=config.MC_VERSION,
                    username=username,
                    server_host=config.SERVER_HOST,
                    server_port=config.SERVER_PORT,
                    on_log=emit_from_worker,
                )
                emit_from_worker("Minecraft exited.")
            except Exception as exc:
                emit_from_worker(f"Error: {exc!r}")
            finally:
                root.after(0, lambda: (launch_btn.config(state=tk.NORMAL, text="Launch"),
                                       name_entry.config(state=tk.NORMAL)))

        threading.Thread(target=worker, daemon=True).start()

    launch_btn.config(command=on_launch)
    root.mainloop()
