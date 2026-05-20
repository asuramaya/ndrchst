"""Server lifecycle: compose platform install + docker container + rcon.

This is the only place that knows about all four subsystems
(platforms, runtime/docker, store, eventually runtime/geyser). Everything
else stays oblivious.
"""
from __future__ import annotations

import contextlib
import random
import re
import secrets
import shlex
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..domain import config as cfg_mod
from ..domain.models import Family, Server, ServerStatus
from ..platforms import REGISTRY as PLATFORMS
from ..store import servers as srv_store
from .docker import BEDROCK_IMAGE, ContainerSpec, Docker, java_image_for
from .eula import accept_eula
from .geyser import install_cross_play
from .pilot import PilotBuildError
from .pilot import build_bundle as build_pilot_bundle
from .pilot import remove_bundle as remove_pilot_bundle
from .ports import is_port_free

SERVERS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "servers"

_NAME_OK = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
_PORT_RANGE = range(1024, 65536)


class LifecycleError(Exception):
    pass


DEFAULT_BEDROCK_BRIDGE_PORT = 19132
RCON_PORT_RANGE = range(30000, 40000)


def _ensure_rcon_in_properties(data_dir: Path, password: str) -> None:
    """Force enable-rcon=true + rcon.password=<password> + rcon.port=25575
    in server.properties. Creates a minimal file if Paper hasn't generated
    one yet — Paper preserves existing values on first boot."""
    props_path = data_dir / "server.properties"
    required = {
        "enable-rcon": "true",
        "rcon.port": "25575",
        "rcon.password": password,
        "broadcast-rcon-to-ops": "false",
    }
    if props_path.exists():
        lines = props_path.read_text().splitlines()
        keys_seen: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in required:
                new_lines.append(f"{key}={required[key]}")
                keys_seen.add(key)
            else:
                new_lines.append(line)
        for key, value in required.items():
            if key not in keys_seen:
                new_lines.append(f"{key}={value}")
        props_path.write_text("\n".join(new_lines) + "\n")
    else:
        body = "# Pre-seeded by ndrchst for RCON.\n"
        for k, v in required.items():
            body += f"{k}={v}\n"
        props_path.write_text(body)


@dataclass(frozen=True, slots=True)
class CreateRequest:
    name: str
    platform_id: str
    version: str
    port: int
    memory_mb: int = 2048
    cross_play: bool = False  # Java-only flag; Bedrock is already bedrock
    # Geyser UDP listener port on the host. Only used when cross_play=True
    # for Java servers. Default 19132 (the Mojang Bedrock convention).
    bedrock_bridge_port: int = DEFAULT_BEDROCK_BRIDGE_PORT


def _new_server_id() -> str:
    # 12 hex chars — enough for collision-resistance under our scale
    return uuid.uuid4().hex[:12]


def _container_name(server_id: str) -> str:
    return f"ndrchst-{server_id}"


def _build_spec(server: Server, data_dir: Path) -> ContainerSpec:
    if server.family is Family.JAVA:
        image = java_image_for(server.version)
        xmx = server.memory_mb
        xms = max(server.memory_mb // 2, 512)
        # NeoForge (and modpacks layered on top of NeoForge) produce a
        # `run.sh` that exec's java with its own @-args files (libraries,
        # modlist, etc.). We can't replicate that arg list ourselves —
        # invoke run.sh and inject memory via JAVA_TOOL_OPTIONS which the
        # JVM picks up regardless of how it's launched.
        is_neoforge = server.platform_id in ("neoforge", "modpack")
        if is_neoforge:
            cmd = ["bash", "run.sh", "nogui"]
        else:
            cmd = ["java", f"-Xms{xms}m", f"-Xmx{xmx}m"]
            # User-supplied JVM flags slotted in *before* -jar so they apply to
            # the JVM, not to the server jar's arguments.
            if server.extra_jvm_flags:
                cmd.extend(shlex.split(server.extra_jvm_flags))
            cmd.extend(["-jar", "server.jar", "nogui"])
        env: dict[str, str] = {"EULA": "TRUE"}
        if is_neoforge:
            # JAVA_TOOL_OPTIONS gets read by every JVM start within the
            # container — covers both run.sh's `java` invocation and any
            # mod-bootstrap re-exec. Memory + user flags ride here.
            jto = f"-Xms{xms}m -Xmx{xmx}m"
            if server.extra_jvm_flags:
                jto = f"{jto} {server.extra_jvm_flags}"
            env["JAVA_TOOL_OPTIONS"] = jto
        # User env merged after the runtime-required keys so reserved-keys
        # validation (in domain/config) is the only guard we need.
        if server.env_vars:
            env.update(cfg_mod.parse_env_vars(server.env_vars))
        ports: dict[str, int | tuple[str, int]] = {"25565/tcp": server.port}
        # Cross-play servers also need Geyser's UDP listener exposed so
        # Bedrock clients can reach it from outside the container.
        if server.cross_play and server.bedrock_bridge_port is not None:
            ports["19132/udp"] = server.bedrock_bridge_port
        # RCON: bind container:25575 → 127.0.0.1:rcon_port (localhost-only).
        # The admin console is the only intended consumer, and there's no
        # reason to expose RCON publicly even with a password.
        if server.rcon_port is not None:
            ports["25575/tcp"] = ("127.0.0.1", server.rcon_port)
    elif server.family is Family.BEDROCK:
        image = BEDROCK_IMAGE
        # BDS needs LD_LIBRARY_PATH because its libs live alongside the binary.
        # BDS 1.21+ links against libcurl4 which is NOT in ubuntu:24.04 by
        # default — we install it on first boot. apt-get install is idempotent
        # and fast (~1s) on subsequent boots after the package is cached.
        cmd = [
            "sh", "-c",
            "command -v curl >/dev/null 2>&1 || "
            "(apt-get update -qq && apt-get install -y -qq libcurl4 >/dev/null) "
            "&& exec ./bedrock_server",
        ]
        env = {"LD_LIBRARY_PATH": "."}
        if server.env_vars:
            env.update(cfg_mod.parse_env_vars(server.env_vars))
        ports = {"19132/udp": server.port}
    else:
        raise LifecycleError(f"unsupported family: {server.family}")

    return ContainerSpec(
        name=_container_name(server.id),
        image=image,
        cmd=cmd,
        workdir="/data",
        data_dir=data_dir,
        ports=ports,
        memory_mb=server.memory_mb,
        server_id=server.id,
        family=server.family,
        env=env,
    )


def _validate(req: CreateRequest, conn: sqlite3.Connection, *, check_host_port: bool = True) -> None:
    if not _NAME_OK.match(req.name):
        raise LifecycleError(
            f"server name must be 1-64 chars of [A-Za-z0-9 _-], got: {req.name!r}"
        )
    if req.platform_id not in PLATFORMS:
        raise LifecycleError(f"unknown platform: {req.platform_id}")
    if req.port not in _PORT_RANGE:
        raise LifecycleError(f"port must be 1024-65535, got {req.port}")
    if req.memory_mb < 512:
        raise LifecycleError(f"memory must be >= 512 MB, got {req.memory_mb}")
    if srv_store.port_in_use(conn, req.port):
        raise LifecycleError(f"port {req.port} already used by another server")

    platform_family = PLATFORMS[req.platform_id].family
    if check_host_port and not is_port_free(req.port, platform_family):
        proto = "UDP" if platform_family.value == "bedrock" else "TCP"
        raise LifecycleError(
            f"port {req.port}/{proto} is already bound by another process on the host"
        )

    if req.cross_play:
        if platform_family is not Family.JAVA:
            raise LifecycleError("cross_play is only meaningful for Java servers")
        if req.bedrock_bridge_port not in _PORT_RANGE:
            raise LifecycleError(
                f"bedrock_bridge_port must be 1024-65535, got {req.bedrock_bridge_port}"
            )
        if req.bedrock_bridge_port == req.port:
            raise LifecycleError(
                "bedrock_bridge_port must differ from the Java port"
            )
        if srv_store.port_in_use(conn, req.bedrock_bridge_port):
            raise LifecycleError(
                f"bedrock_bridge_port {req.bedrock_bridge_port} already used by another server"
            )
        if check_host_port and not is_port_free(req.bedrock_bridge_port, Family.BEDROCK):
            raise LifecycleError(
                f"bedrock_bridge_port {req.bedrock_bridge_port}/UDP is already bound on the host"
            )


class Lifecycle:
    """Composes the subsystems. Callers inject Docker + conn for testability."""

    def __init__(
        self,
        docker: Docker,
        conn: sqlite3.Connection,
        *,
        servers_root: Path = SERVERS_ROOT_DEFAULT,
        public_host: str | None = None,
        edge_url: str | None = None,
        tunnel_hostname: str | None = None,
    ):
        self._docker = docker
        self._conn = conn
        self._root = servers_root
        # Address MC clients should dial. Set via `NDRCHST_PUBLIC_HOST` env;
        # falls back to a placeholder users have to edit by hand.
        self._public_host = public_host or ""
        # HTTPS base URL where the public surface lives — used in the pilot
        # bundle README so end-users know where to fetch fresh copies.
        # Set via `NDRCHST_EDGE_URL` (e.g. "https://play.ndrchst.com").
        self._edge_url = edge_url or ""
        # Cloudflare Tunnel hostname for cloudflared access tcp — when set,
        # the pilot dials this through CF edge instead of the raw
        # public_host:port. Set via `NDRCHST_TUNNEL_HOSTNAME`.
        self._tunnel_hostname = tunnel_hostname or ""

    async def create(self, req: CreateRequest) -> Server:
        _validate(req, self._conn)

        platform = PLATFORMS[req.platform_id]
        server_id = _new_server_id()
        data_dir = self._root / server_id

        # Resolve "latest" magic-string to the platform's actual current
        # version so the Server record stores a concrete pin (good for the
        # UI, easier to debug, makes Geyser version-matching deterministic).
        version = req.version
        if version == "latest":
            try:
                versions_list = await platform.versions()
            except NotImplementedError as e:
                raise LifecycleError(
                    f"platform '{req.platform_id}' is not yet implemented; "
                    f"choose paper or bedrock"
                ) from e
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                raise LifecycleError(
                    f"failed to resolve latest version for {req.platform_id}: {e}"
                ) from e
            if not versions_list:
                raise LifecycleError(f"no versions available for {req.platform_id}")
            version = versions_list[0].version

        # Install the platform binary to the data dir. This is the only
        # step that touches upstream APIs and disk before we record state.
        # Wrap upstream + stub errors in LifecycleError so the API/web
        # layers can surface a clean 4xx instead of bubbling a 500.
        try:
            await platform.install(version, data_dir)
        except NotImplementedError as e:
            shutil.rmtree(data_dir, ignore_errors=True)
            raise LifecycleError(
                f"platform '{req.platform_id}' is not yet implemented; "
                f"choose paper or bedrock"
            ) from e
        except httpx.HTTPStatusError as e:
            shutil.rmtree(data_dir, ignore_errors=True)
            if e.response.status_code == 404:
                raise LifecycleError(
                    f"version '{version}' not found for {req.platform_id}"
                ) from e
            raise LifecycleError(
                f"{req.platform_id} upstream returned HTTP {e.response.status_code}: {e}"
            ) from e
        except (ValueError, RuntimeError) as e:
            # ValueError: Paper "no builds for X"; RuntimeError: Bedrock schema drift / zip slip
            shutil.rmtree(data_dir, ignore_errors=True)
            raise LifecycleError(f"install failed: {e}") from e

        # Accept EULA on the user's behalf. Using ndrchst to create a Minecraft
        # server is treated as agreement (per project policy); we cannot present
        # an interactive EULA prompt in a UI workflow, and refusing to write
        # the file just means the first start fails.
        accept_eula(platform.family, data_dir)

        # Cross-play is Java-only. Bedrock servers ARE bedrock.
        # _validate already enforced family + non-collision; just install + wire.
        # Geyser binds to the *internal* container port (19132); Docker maps
        # host:bedrock_bridge_port → container:19132. If we passed the host
        # port to install_cross_play, Geyser would bind 19150 inside while
        # Docker forwards to 19132 — traffic vanishes.
        if req.cross_play:
            await install_cross_play(data_dir, java_port=25565)

        # RCON setup for Java: pick a host port + password, write them into
        # server.properties (which the platform install just produced or
        # which Paper will generate on first boot). Localhost-only binding
        # in _build_spec keeps RCON off the public surface.
        rcon_port: int | None = None
        rcon_password: str | None = None
        if platform.family is Family.JAVA:
            rcon_port = self._pick_rcon_port()
            rcon_password = secrets.token_urlsafe(18)
            _ensure_rcon_in_properties(data_dir, rcon_password)

        server = Server(
            id=server_id,
            name=req.name,
            platform_id=req.platform_id,
            family=platform.family,
            version=version,
            port=req.port,
            memory_mb=req.memory_mb,
            cross_play=req.cross_play,
            bedrock_bridge_port=req.bedrock_bridge_port if req.cross_play else None,
            rcon_port=rcon_port,
            rcon_password=rcon_password,
        )

        spec = _build_spec(server, data_dir)
        container_id = await self._docker.create_container(spec)
        server.container_id = container_id

        srv_store.insert(self._conn, server)

        # Generate the per-server pilot bundle for Java servers. Cheap (no
        # compile step), version-locked, so the public surface can serve a
        # client guaranteed to match this exact server.
        if server.family is Family.JAVA:
            try:
                build_pilot_bundle(
                    server,
                    public_host=self._public_host,
                    edge_url=self._edge_url,
                    tunnel_hostname=self._tunnel_hostname,
                )
            except PilotBuildError:
                # Pilot is best-effort — log but don't fail server create
                import logging
                logging.getLogger("ndrchst").warning(
                    "pilot bundle build failed for %s; server created anyway", server.id,
                    exc_info=True,
                )

        return server

    async def start(self, server_id: str) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            raise LifecycleError(f"server {server_id} has no container")
        srv_store.update_status(self._conn, server_id, ServerStatus.STARTING)
        await self._docker.start(server.container_id)
        srv_store.update_status(self._conn, server_id, ServerStatus.RUNNING)

    async def stop(self, server_id: str, *, timeout: int = 30) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            return
        srv_store.update_status(self._conn, server_id, ServerStatus.STOPPING)
        await self._docker.stop(server.container_id, timeout=timeout)
        srv_store.update_status(self._conn, server_id, ServerStatus.STOPPED)

    async def restart(self, server_id: str, *, timeout: int = 30) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            raise LifecycleError(f"server {server_id} has no container")
        await self._docker.restart(server.container_id, timeout=timeout)

    async def engine_info(self) -> dict:
        """Docker engine summary for the System page."""
        return await self._docker.engine_info()

    async def publish_to_r2(self, server_id: str) -> dict:
        """Push this server's pilot artifacts (incl. the small pilot.zip) + the
        public pages to R2 so Cloudflare's edge serves them instead of the box.
        No-op (and says so) when R2 isn't configured. The big modpack is never
        pushed — the pilot pulls it from CurseForge's CDN."""
        import asyncio
        import os

        from . import r2
        from .pilot import PILOTS_ROOT_DEFAULT
        from .publish import publish_server

        cfg = r2.config_from_env()
        if cfg is None:
            return {"published": False, "reason": "R2 not configured (NDRCHST_R2_* unset)"}
        server = self._must_get(server_id)
        java = [s for s in srv_store.list_all(self._conn) if s.family is Family.JAVA]
        summary = await asyncio.to_thread(
            publish_server,
            cfg=cfg, server=server, servers_root=self._root,
            pilots_root=PILOTS_ROOT_DEFAULT, java_servers=java,
            play_url=os.environ.get("NDRCHST_PLAY_URL"),
            downloads_base=os.environ.get("NDRCHST_PILOT_DOWNLOADS_BASE", ""),
        )
        return {"published": True, **summary}

    async def build_mods_index(self, server_id: str) -> int:
        """Build a cached `mods-index.json` in the server's data dir. Each
        mod gets {filename, size, sha1, url}. The URL points at CurseForge's
        CDN (edge.forgecdn.net) when the staged client manifest resolves the
        file, and at our own /pilot/<sid>/mods/<file> endpoint otherwise
        (substitutions like the cc-tweaked version bump).

        This is what makes the pilot scale to hundreds of users: the heavy
        ~1.25 GB of mod jars streams from CF's global CDN, not through the
        operator's residential uplink (which caps the tunnel at ~200 KB/s).
        Returns the number of mods indexed."""
        import hashlib
        import json
        import urllib.parse

        import httpx

        from . import curseforge as cf
        from .pilot import PILOTS_ROOT_DEFAULT

        self._must_get(server_id)
        mods_dir = self._root / server_id / "mods"
        if not mods_dir.exists():
            raise LifecycleError(f"server {server_id} has no mods/ dir")

        # Resolve CF CDN URLs + client target dirs from the staged client
        # manifest, if present. resolved maps filename → {"url", "target"}
        # where target is "mods" for jars and shaderpacks/resourcepacks/saves
        # for non-jar CF assets.
        resolved: dict[str, dict] = {}
        modpack_zip = PILOTS_ROOT_DEFAULT / server_id / "modpack.zip"
        if modpack_zip.exists():
            try:
                manifest = cf.read_manifest(modpack_zip)
                async with httpx.AsyncClient(
                    timeout=60.0, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (ndrchst)"},
                ) as client:
                    resolved = await cf.resolve_manifest_targets(
                        client, manifest.files,
                    )
            except cf.CurseForgeError:
                # No manifest / not a CF pack — everything served from origin.
                pass

        edge = self._edge_url.rstrip("/")
        entries = []
        for p in sorted(mods_dir.iterdir()):
            if not p.is_file() or not p.name.endswith(".jar"):
                continue
            h = hashlib.sha1()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    h.update(chunk)
            url = (resolved.get(p.name) or {}).get("url")
            origin_url = (
                f"{edge}/pilot/{server_id}/mods/{urllib.parse.quote(p.name, safe='')}"
                if edge else None
            )
            entries.append({
                "filename": p.name,
                "size": p.stat().st_size,
                "sha1": h.hexdigest(),
                # CF CDN if resolvable, else our origin (small substitution set).
                "url": url or origin_url,
                # Always provide the origin fallback so the pilot can retry
                # if a CDN URL 403s (third-party-downloads-disabled mods).
                "origin_url": origin_url,
                "from_cdn": url is not None,
                "client_only": False,
                "target": "mods",  # server mods are jars → mods/
            })

        # Add the manifest's CLIENT-ONLY assets: entries the full client pack
        # has that the server's mods/ doesn't — client mods (rendering, UI,
        # minimaps, JEI addons) AND non-jar assets (shaderpacks, resourcepacks)
        # routed to their own client dirs. The dedicated server can't run the
        # client mods, but the client needs them to render the world AND to
        # match the modpack's expected mod count (Crash Assistant nags
        # otherwise). They come straight from CF's CDN; we have no local copy
        # to hash, so sha1 is null and the pilot trusts the CDN bytes.
        server_filenames = {e["filename"] for e in entries}
        for filename, info in resolved.items():
            if filename in server_filenames:
                continue
            entries.append({
                "filename": filename,
                "size": None,
                "sha1": None,
                "url": info["url"],
                "origin_url": None,
                "from_cdn": True,
                "client_only": True,
                "target": info["target"],
            })

        index = {"server_id": server_id, "mods": entries}
        out = self._root / server_id / "mods-index.json"
        out.write_text(json.dumps(index, indent=2))
        cdn_count = sum(1 for e in entries if e["from_cdn"])
        client_only = sum(1 for e in entries if e["client_only"])
        return len(entries), cdn_count, client_only

    async def update_config(
        self,
        server_id: str,
        *,
        memory_mb: int,
        extra_jvm_flags: str | None,
        env_vars: str | None,
    ) -> Server:
        """Update the recreate-time knobs (memory + JVM args + env). Validates
        first, then if the values would change the container spec we stop +
        remove + recreate the container. Data dir is preserved (the world,
        plugins, etc. all survive — only the container is rebuilt)."""
        server = self._must_get(server_id)
        memory_mb = cfg_mod.validate_memory(memory_mb)
        flags = cfg_mod.normalize_jvm_flags(extra_jvm_flags)
        env_norm = cfg_mod.normalize_env_vars(env_vars)

        unchanged = (
            server.memory_mb == memory_mb
            and (server.extra_jvm_flags or None) == flags
            and (server.env_vars or None) == env_norm
        )
        if unchanged:
            return server

        # Persist first so the new spec we build sees the new values.
        srv_store.update_config(
            self._conn, server_id,
            memory_mb=memory_mb, extra_jvm_flags=flags, env_vars=env_norm,
        )
        server.memory_mb = memory_mb
        server.extra_jvm_flags = flags
        server.env_vars = env_norm

        # Container exists → rebuild it. Stop + remove + create. Status is
        # set back to STOPPED so the user has to explicitly Start to apply.
        if server.container_id is not None:
            # Container may already be stopped; suppress and proceed to remove.
            with contextlib.suppress(Exception):
                await self._docker.stop(server.container_id, timeout=15)
            await self._docker.remove(server.container_id, force=True)
            spec = _build_spec(server, self._root / server_id)
            new_container_id = await self._docker.create_container(spec)
            server.container_id = new_container_id
            srv_store.set_container_id(self._conn, server_id, new_container_id)
            srv_store.update_status(self._conn, server_id, ServerStatus.STOPPED)
            server.status = ServerStatus.STOPPED

        return server

    async def delete(self, server_id: str, *, remove_files: bool = False) -> None:
        server = self._must_get(server_id)
        if server.container_id is not None:
            await self._docker.remove(server.container_id, force=True)
        if remove_files:
            data_dir = self._root / server_id
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
        # Always drop the pilot bundle — once the server is gone, the bundle
        # points at a dead address and serves no one.
        remove_pilot_bundle(server_id)
        srv_store.delete(self._conn, server_id)

    def _pick_rcon_port(self) -> int:
        """Pick a free RCON host port (not collision with any reserved
        server port). Random in RCON_PORT_RANGE so multiple ndrchst
        instances on the same box don't pick the same one deterministically."""
        for _ in range(200):
            candidate = random.choice(list(RCON_PORT_RANGE))
            if not srv_store.port_in_use(self._conn, candidate) and is_port_free(candidate, Family.JAVA):
                return candidate
        raise LifecycleError("could not find a free RCON host port; range exhausted")

    async def recreate_container(self, server_id: str) -> Server:
        """Stop + remove the existing container and create a fresh one from
        the current spec. Data dir is preserved (world, mods, configs, all
        survive). Used when the container's baked-in cmd or env drifts from
        what the current ndrchst version would build — e.g. after a server
        was created with an older release."""
        server = self._must_get(server_id)
        if server.container_id is not None:
            with contextlib.suppress(Exception):
                await self._docker.stop(server.container_id, timeout=15)
            await self._docker.remove(server.container_id, force=True)
        spec = _build_spec(server, self._root / server_id)
        new_id = await self._docker.create_container(spec)
        server.container_id = new_id
        srv_store.set_container_id(self._conn, server_id, new_id)
        srv_store.update_status(self._conn, server_id, ServerStatus.STOPPED)
        server.status = ServerStatus.STOPPED
        return server

    def set_modpack_pack(
        self, server_id: str, project_id: int | None, file_id: int | None,
    ) -> None:
        """Pin (or clear) this server's CurseForge client-pack coordinates.
        The pilot build resolves these to a CF CDN URL so the box never
        re-hosts the ~200MB pack zip."""
        self._must_get(server_id)
        srv_store.set_cf_pack(self._conn, server_id, project_id, file_id)

    def set_neoforge_version(self, server_id: str, version: str | None) -> None:
        """Pin (or clear) the NeoForge version the pilot installs. Persisted so
        a later bare regenerate keeps the modloader instead of reverting to
        vanilla."""
        self._must_get(server_id)
        srv_store.set_neoforge_version(self._conn, server_id, version)

    async def modpack_cdn_url(self, server_id: str) -> str | None:
        """Resolve the CF CDN URL for this server's pinned client pack, or
        None if no pack is pinned. The filename is fetched live from CF so
        the URL tracks the pack — re-resolve (rebuild the pilot) to stay
        synced when the operator bumps the pinned fileId."""
        import httpx

        from . import curseforge as cf

        server = self._must_get(server_id)
        if server.cf_project_id is None or server.cf_file_id is None:
            return None
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (ndrchst)"},
        ) as client:
            return await cf.pack_cdn_url(
                client, server.cf_project_id, server.cf_file_id,
            )

    def regenerate_pilot(
        self,
        server_id: str,
        *,
        modpack_url: str = "",
        neoforge_version: str = "",
    ):
        """Rebuild the pilot bundle from the current server record + lifespan
        env. Cheap (no compile step) — exists so the user can pick up a new
        public_host / edge_url / tunnel without recreating the server.

        ``modpack_url`` and ``neoforge_version`` let callers pin a
        client-side modpack and modloader version for the pilot that may
        not be derivable from the server record alone (server holds the
        server-pack URL; client wants the client-pack URL). When
        ``neoforge_version`` is empty it falls back to the value persisted on
        the server, so a bare regenerate keeps installing the modloader."""
        server = self._must_get(server_id)
        if server.family is not Family.JAVA:
            raise LifecycleError("pilot bundles are Java-only")
        return build_pilot_bundle(
            server,
            public_host=self._public_host,
            edge_url=self._edge_url,
            tunnel_hostname=self._tunnel_hostname,
            modpack_url=modpack_url,
            neoforge_version=neoforge_version or (server.neoforge_version or ""),
        )

    async def stats(self, server_id: str):
        """Return Docker container stats for the server, or None if no
        container exists (e.g. server was never started)."""
        server = self._must_get(server_id)
        if server.container_id is None:
            return None
        return await self._docker.stats(server.container_id)

    async def logs(self, server_id: str, *, lines: int = 100) -> str:
        server = self._must_get(server_id)
        if server.container_id is None:
            return ""
        return await self._docker.logs(server.container_id, lines=lines)

    def follow_logs(self, server_id: str, *, tail: int = 200):
        """Return an async iterator over live log chunks. Empty iterator if
        the server has no container yet."""
        server = self._must_get(server_id)
        if server.container_id is None:
            async def _empty():
                if False:
                    yield ""
                return
            return _empty()
        return self._docker.follow_logs(server.container_id, tail=tail)

    def _must_get(self, server_id: str) -> Server:
        s = srv_store.get(self._conn, server_id)
        if s is None:
            raise LifecycleError(f"server {server_id!r} not found")
        return s
