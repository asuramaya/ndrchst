"""Minimal tkinter front-end for the client.

Kept deliberately small — the value is in the backend (install, mod
sync, tunnel, launch). One window: server info, the launch controls
(name, RAM, GPU), a Play button, a phase label + progress bar, and a
log pane. The same binary serves any server via settings.load().
"""
from __future__ import annotations

import contextlib
import platform
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import __version__, deeplink, desktop, updater, wallet_auth
from .launcher import launch
from .settings import load as load_config

APP_SLUG = "ndrchst-client"

# End x Solana palette — matches the web surfaces (void purple + ender green).
_BG = "#0a0613"
_PANEL = "#161029"
_FG = "#f4f0ff"
_MUTED = "#a99fc7"
_ACCENT = "#14f195"  # ender green / Solana green
_PURPLE = "#9945ff"  # Solana purple
_INK = "#04130c"     # dark text on the green accent

# Themed UI assets (banner GIF + brand glyph + app icon), bundled into the zip.
_ASSETS = Path(__file__).resolve().parent / "assets"

# Remembered launch prefs (RAM / GPU) so the player sets them once. Lives next
# to the client's data dir; best-effort — a missing/corrupt file just resets.
_PREFS_PATH = Path.home() / f".{APP_SLUG}" / "prefs.json"


def _load_prefs() -> dict:
    import json
    try:
        return json.loads(_PREFS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    import json
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(prefs))
    except OSError:
        pass


def _header_text(cfg: object) -> tuple[str, str]:
    """The two identity lines under the title — server target + version line.
    Pure so it can be recomputed after a deep link swaps in a new server."""
    host = cfg.tunnel_hostname or (
        f"{cfg.server_host}:{cfg.server_port}" if cfg.server_host else "")
    target = f"Server  {host}" if host else "Server  — open a server from the website —"
    sub = f"Minecraft {cfg.mc_version}"
    if cfg.neoforge_version:
        sub += f"  ·  NeoForge {cfg.neoforge_version}"
    if cfg.modpack_url:
        sub += "  ·  modded"
    sub += f"  ·  client {__version__}"
    return target, sub


def _load_gif_frames(root: tk.Tk, path: Path) -> list:
    """Read every frame of an animated GIF as a PhotoImage (Tk has no native
    animation — we cycle frames ourselves)."""
    frames = []
    i = 0
    while True:
        try:
            frames.append(tk.PhotoImage(master=root, file=str(path),
                                        format=f"gif -index {i}"))
            i += 1
        except tk.TclError:
            break
    return frames

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
    # If the OS launched us to handle a ndrchst:// deep link and a window is
    # already open, hand the URL to it and exit — don't open a second window.
    initial_url = deeplink.url_from_argv(sys.argv[1:])
    if initial_url and deeplink.try_forward(initial_url):
        return

    cfg = load_config()

    prefs = _load_prefs()

    # className groups the window under our icon in the Linux taskbar (matches
    # StartupWMClass in the .desktop shortcut).
    root = tk.Tk(className="ndrchst")
    root.title(cfg.app_name)
    root.geometry("640x460")
    root.minsize(560, 420)
    root.configure(bg=_BG)
    # Window + taskbar icon (the same glyph the desktop shortcut uses). Best-effort.
    icon_path = _ASSETS / "icon.png"
    if icon_path.exists():
        try:
            root._icon_img = tk.PhotoImage(master=root, file=str(icon_path))
            root.iconphoto(True, root._icon_img)
        except tk.TclError:
            pass

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
    style.configure("Brand.TLabel", background=_BG)
    # Default buttons: purpur panel; accent button: ender green.
    style.configure("TButton", background=_PANEL, foreground=_FG, borderwidth=0,
                    focuscolor=_PANEL, padding=(10, 5))
    style.map("TButton", background=[("active", _BG3 := "#241a40"), ("disabled", _PANEL)],
              foreground=[("disabled", _MUTED)])
    style.configure("Accent.TButton", font=("", 11, "bold"), background=_ACCENT,
                    foreground=_INK, borderwidth=0, padding=(14, 6))
    style.map("Accent.TButton", background=[("active", "#2bf7a3"), ("disabled", _PANEL)],
              foreground=[("disabled", _MUTED)])
    style.configure("TProgressbar", background=_ACCENT, troughcolor=_PANEL,
                    borderwidth=0)
    style.configure("TSpinbox", fieldbackground=_PANEL, foreground=_FG,
                    background=_PANEL, arrowcolor=_FG, borderwidth=0)
    style.configure("TCombobox", fieldbackground=_PANEL, foreground=_FG,
                    background=_PANEL, arrowcolor=_FG, borderwidth=0)

    # ---- Animated End-void banner -------------------------------------
    banner_path = _ASSETS / "end_banner.gif"
    if banner_path.exists():
        banner_frames = _load_gif_frames(root, banner_path)
        if banner_frames:
            banner = tk.Label(root, bg=_BG, bd=0)
            banner.pack(fill=tk.X)

            def _cycle(i: int = 0) -> None:
                banner.configure(image=banner_frames[i])
                root.after(110, _cycle, (i + 1) % len(banner_frames))
            _cycle()

    # ---- Header: server identity --------------------------------------
    info = ttk.Frame(root, padding=(16, 14, 16, 8))
    info.pack(fill=tk.X)
    titlebar = ttk.Frame(info)
    titlebar.pack(anchor="w")
    brand_path = _ASSETS / "brand.png"
    if brand_path.exists():
        root._brand_img = tk.PhotoImage(master=root, file=str(brand_path))  # keep ref
        ttk.Label(titlebar, image=root._brand_img, style="Brand.TLabel").pack(
            side=tk.LEFT, padx=(0, 8))
    title_lbl = ttk.Label(titlebar, text=cfg.app_name, style="Title.TLabel")
    title_lbl.pack(side=tk.LEFT)
    target_lbl = ttk.Label(info, style="Muted.TLabel")
    target_lbl.pack(anchor="w")
    sub_lbl = ttk.Label(info, style="Muted.TLabel")
    sub_lbl.pack(anchor="w")

    def _refresh_header() -> None:
        tgt, sub = _header_text(cfg)
        target_lbl.config(text=tgt)
        sub_lbl.config(text=sub)
    _refresh_header()

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

    # Identity is your wallet — no manual username. The in-game name is the
    # wallet-derived handle, shown read-only once signed in.
    ttk.Label(form, text="Playing as").grid(row=0, column=0, sticky="w", pady=4)
    name_var = tk.StringVar(value="— sign in with your wallet —")
    ttk.Label(form, textvariable=name_var).grid(
        row=0, column=1, sticky="w", padx=(10, 0), pady=4)

    ttk.Label(form, text="Memory (GB)").grid(row=1, column=0, sticky="w", pady=4)
    ram_var = tk.StringVar(value=str(prefs.get("ram_gb", 8)))
    ram_spin = ttk.Spinbox(form, from_=2, to=32, increment=1, width=6,
                           textvariable=ram_var)
    ram_spin.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=4)

    ttk.Label(form, text="Graphics").grid(row=2, column=0, sticky="w", pady=4)
    _gpu_labels = [label for label, _ in _GPU_CHOICES]
    gpu_var = tk.StringVar(
        value=prefs["gpu_label"] if prefs.get("gpu_label") in _gpu_labels
        else _GPU_CHOICES[0][0])
    gpu_combo = ttk.Combobox(
        form, textvariable=gpu_var, state="readonly", width=24,
        values=[label for label, _ in _GPU_CHOICES],
    )
    gpu_combo.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=4)
    # The GPU picker only changes behaviour on Linux hybrid laptops.
    if platform.system() != "Linux":
        gpu_combo.configure(state="disabled")

    # ---- Wallet sign-in -----------------------------------------------
    wallet_row = ttk.Frame(root, padding=(16, 4))
    wallet_row.pack(fill=tk.X)
    wallet_btn = ttk.Button(wallet_row, text="Sign in with wallet")
    wallet_btn.pack(side=tk.LEFT)
    wallet_status = tk.StringVar(value="Not signed in")
    ttk.Label(wallet_row, textvariable=wallet_status, style="Muted.TLabel").pack(
        side=tk.LEFT, padx=12)

    # ---- Play + progress ----------------------------------------------
    action = ttk.Frame(root, padding=(16, 8))
    action.pack(fill=tk.X)
    launch_btn = ttk.Button(action, text="Play", style="Accent.TButton",
                            state=tk.DISABLED)
    launch_btn.pack(side=tk.LEFT)
    phase_var = tk.StringVar(value="Sign in with your wallet to play")
    ttk.Label(action, textvariable=phase_var, style="Muted.TLabel").pack(
        side=tk.LEFT, padx=12)

    # Wallet sign-in is the mandated auth path: no wallet, no Play. mc_name is
    # the wallet-derived in-game identity; join_token is the credential the
    # ndrchst-auth mod presents to the server at connect time.
    wallet_id: dict[str, str | None] = {
        "mc_name": None, "join_token": None, "device_token": None}

    bar = ttk.Progressbar(root, mode="determinate", maximum=len(_PHASES))
    bar.pack(fill=tk.X, padx=16, pady=(0, 6))

    # ---- Details (log) — collapsed by default for a clean, braindead window.
    details_row = ttk.Frame(root, padding=(16, 0))
    details_row.pack(fill=tk.X)
    details_btn = ttk.Button(details_row, text="Show details ▾")
    details_btn.pack(side=tk.LEFT)

    log_box = tk.Text(root, height=10, wrap="word", state=tk.DISABLED,
                      bg=_PANEL, fg=_MUTED, insertbackground=_FG,
                      relief="flat", font=("monospace", 9), padx=10, pady=8)
    _log_shown = {"v": False}

    def toggle_log(show: bool | None = None) -> None:
        show = (not _log_shown["v"]) if show is None else show
        if show == _log_shown["v"]:
            return
        _log_shown["v"] = show
        if show:
            log_box.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 16))
            details_btn.config(text="Hide details ▴")
            root.geometry("640x600")
        else:
            log_box.pack_forget()
            details_btn.config(text="Show details ▾")
            root.geometry("640x460")
    details_btn.config(command=toggle_log)

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
        ram_spin.config(state=widget_state)
        if platform.system() == "Linux":
            gpu_combo.config(state="readonly" if enabled else tk.DISABLED)
        # Play is only available once a wallet is linked (mandated auth path).
        signed_in = wallet_id["mc_name"] is not None
        launch_btn.config(
            state=tk.NORMAL if (enabled and signed_in) else tk.DISABLED,
            text="Play" if enabled else "Running…")

    def apply_identity(ident: object, dev: str | None = None) -> None:
        """Reflect a signed-in wallet identity in the UI. Main-thread only.
        Shared by the manual sign-in, the device-token auto sign-in, and the
        deep-link handoff so they can't drift apart."""
        wallet_id["mc_name"] = ident.mc_name
        wallet_id["join_token"] = ident.join_token
        if dev:
            wallet_id["device_token"] = dev
        name_var.set(ident.mc_name)
        tier = f"  ·  {ident.tier_name}" if ident.tier_name else "  ·  no rank"
        wallet_status.set(f"{ident.display}{tier}")
        wallet_btn.config(text="Signed in", state=tk.DISABLED)
        launch_btn.config(state=tk.NORMAL)
        phase_var.set("Ready — press Play")

    def reload_config() -> None:
        """Re-read config after a deep link swapped in a new server, and refresh
        the header to match. Main-thread only."""
        nonlocal cfg
        cfg = load_config()
        with contextlib.suppress(tk.TclError):
            root.title(cfg.app_name)
            title_lbl.config(text=cfg.app_name)
        _refresh_header()

    def apply_deeplink(url: str) -> None:
        """Act on a ndrchst:// URL: pin the named server (fetch its config) and
        redeem any one-time handoff code for an instant sign-in."""
        dl = deeplink.parse(url)
        if dl is None or dl.action != "launch":
            return
        with contextlib.suppress(tk.TclError):
            root.deiconify()
            root.lift()
            root.focus_force()
        sid = dl.params.get("sid")
        code = dl.params.get("code")
        base = cfg.auth_base_url or wallet_auth.DEFAULT_BASE

        def worker() -> None:
            if sid:
                try:
                    deeplink.fetch_server_config(base, sid)
                    emit_from_worker(f"Linked to server {sid}.")
                    root.after(0, reload_config)
                except (OSError, ValueError) as exc:
                    # URLError is an OSError and JSONDecodeError a ValueError, so
                    # this is any network/parse failure. Best-effort; logged only.
                    emit_from_worker(f"Couldn't load that server's config: {exc}")
            if code:
                try:
                    ident = wallet_auth.redeem_handoff(base, code)
                except wallet_auth.WalletAuthError as exc:
                    emit_from_worker(f"Sign-in handoff failed: {exc}")
                    return
                dev = ident.device_token or None
                if dev:
                    wallet_auth.write_device_token(dev)
                root.after(0, lambda: apply_identity(ident, dev))

        threading.Thread(target=worker, daemon=True).start()

    def on_launch() -> None:
        username = wallet_id["mc_name"]
        if not username:
            append_log("Sign in with your wallet first.")
            return
        try:
            ram_gb = max(2, int(float(ram_var.get())))
        except ValueError:
            ram_gb = 8
        gpu = dict(_GPU_CHOICES).get(gpu_var.get(), "auto")
        _save_prefs({"ram_gb": ram_gb, "gpu_label": gpu_var.get()})  # remember for next time
        set_controls(False)
        phase_var.set("Starting…")

        def worker() -> None:
            # Fetch a FRESH join token right before connecting (the device
            # token is durable; the join token is short-lived) so a long first
            # install can't let it expire. Falls back to the cached one.
            jt = wallet_id["join_token"]
            dev = wallet_id.get("device_token")
            if dev:
                try:
                    jt = wallet_auth.exchange_device_token(
                        cfg.auth_base_url or wallet_auth.DEFAULT_BASE, dev).join_token
                except wallet_auth.WalletAuthError as exc:
                    emit_from_worker(f"(couldn't refresh join token: {exc}; using cached)")
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
                    join_token=jt,
                    client_ram_mb=ram_gb * 1024,
                    gpu=gpu,
                )
                emit_from_worker("Minecraft exited.")
                root.after(0, lambda: phase_var.set("Minecraft exited"))
            except Exception as exc:
                emit_from_worker(f"Error: {exc!r}")
                root.after(0, lambda: (phase_var.set("Error — see details below"),
                                       toggle_log(True)))
            finally:
                root.after(0, lambda: set_controls(True))

        threading.Thread(target=worker, daemon=True).start()

    # ---- Wallet sign-in handler --------------------------------------
    def on_wallet_signin() -> None:
        wallet_btn.config(state=tk.DISABLED, text="Check your browser…")
        base = cfg.auth_base_url or wallet_auth.DEFAULT_BASE

        def worker() -> None:
            try:
                ident = wallet_auth.begin(base, on_log=emit_from_worker)
            except wallet_auth.WalletAuthError as exc:
                emit_from_worker(f"Wallet sign-in failed: {exc}")
                root.after(0, lambda: wallet_btn.config(
                    state=tk.NORMAL, text="Sign in with wallet"))
                return

            root.after(0, lambda: apply_identity(ident))

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
        update_msg.set(f"Update available — client {info.version}   ")
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
    wallet_btn.config(command=on_wallet_signin)

    # Keyboard shortcuts — Enter plays (when ready), Esc minimises, Ctrl+Q quits.
    def _play_if_ready(_e: object = None) -> None:
        if str(launch_btn["state"]) == tk.NORMAL:
            on_launch()
    root.bind("<Return>", _play_if_ready)
    root.bind("<KP_Enter>", _play_if_ready)
    root.bind("<Escape>", lambda _e: root.iconify())
    root.bind("<Control-q>", lambda _e: root.destroy())

    # Auth-first: if this bundle was downloaded after signing in on the play
    # page, it carries a device token — auto-sign-in, no button press needed.
    def _try_device_signin() -> None:
        dev = wallet_auth.read_device_token()
        if not dev:
            return
        base = cfg.auth_base_url or wallet_auth.DEFAULT_BASE
        try:
            ident = wallet_auth.exchange_device_token(base, dev)
        except wallet_auth.WalletAuthError as exc:
            emit_from_worker(f"Device sign-in unavailable ({exc}) — use 'Sign in with wallet'.")
            return

        root.after(0, lambda: apply_identity(ident, dev))

    threading.Thread(target=_try_device_signin, daemon=True).start()

    # Single-instance + deep links: become the URL handler, and if the OS
    # launched us WITH a ndrchst:// URL (and no other instance grabbed it
    # above), act on it once the window is up.
    deeplink.start_listener(lambda u: root.after(0, lambda: apply_deeplink(u)))
    if initial_url:
        root.after(400, lambda: apply_deeplink(initial_url))

    root.mainloop()
