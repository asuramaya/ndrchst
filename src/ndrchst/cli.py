from __future__ import annotations

import typer
import uvicorn

from .logging_setup import configure as configure_logging

app = typer.Typer(no_args_is_help=True, help="ndrchst — Minecraft server control plane")


@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Bind address (default: localhost only)"),
    port: int = typer.Option(8080, help="HTTP port for the admin surface"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev)"),
) -> None:
    """Boot the ndrchst admin surface (web UI + JSON API)."""
    configure_logging()
    uvicorn.run("ndrchst.api.main:app", host=host, port=port, reload=reload)


@app.command()
def public(
    host: str = typer.Option("127.0.0.1", help="Bind address (default: localhost only)"),
    port: int = typer.Option(8081, help="HTTP port for the public surface"),
) -> None:
    """Boot the ndrchst public surface (client downloads + server list).

    Read-only; safe to expose. Run alongside `ndrchst run` (separate port,
    separate process), then front each with a different hostname via your
    reverse proxy (Cloudflare Tunnel, nginx, etc.).
    """
    configure_logging()
    uvicorn.run("ndrchst.public:app", host=host, port=port)


@app.command()
def doctor() -> None:
    """Sanity-check the environment (Docker, ports, disk)."""
    from . import doctor as doctor_mod
    raise typer.Exit(doctor_mod.run())


if __name__ == "__main__":
    app()
