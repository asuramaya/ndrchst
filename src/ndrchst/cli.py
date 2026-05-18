from __future__ import annotations

import typer
import uvicorn

app = typer.Typer(no_args_is_help=True, help="ndrchst — Minecraft server control plane")


@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Bind address (default: localhost only)"),
    port: int = typer.Option(8080, help="HTTP port"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev)"),
) -> None:
    """Boot the ndrchst web UI + API."""
    uvicorn.run("ndrchst.api.main:app", host=host, port=port, reload=reload)


@app.command()
def doctor() -> None:
    """Sanity-check the environment (Docker, ports, disk)."""
    from . import doctor as doctor_mod
    raise typer.Exit(doctor_mod.run())


if __name__ == "__main__":
    app()
