# ndrchst

Open-source, single-machine Minecraft server control plane.

- Java platforms: Paper, Purpur, Vanilla, Fabric, Forge, NeoForge
- Bedrock: first-class (native dedicated server + Geyser/Floodgate cross-play on Java)
- Docker-only runtime
- Localhost web UI on `:8080`
- Modrinth-backed mod / plugin / world / resource pack install
- MIT licensed

## Status

Alpha. v0 milestone: create + run a Paper or Bedrock server, install one mod, RCON, console, properties editor.

## Requirements

- Linux (macOS soon, Windows via WSL)
- Docker
- Python 3.12+

## Install

```bash
uv sync
uv run ndrchst run
```

Open <http://localhost:8080>.

## Layout

```
src/ndrchst/
  platforms/   per-platform install + version logic
  mods/        Modrinth (Spiget, Hangar later)
  runtime/     Docker, RCON, lifecycle
  domain/      players, worlds, files, properties
  api/         FastAPI routers
  web/         htmx + Jinja templates
  store/       SQLite persistence
  cli.py       `ndrchst run`
```

## License

MIT
