"""One-shot JDK container runner — used by platforms whose install step
itself runs Java code (NeoForge's installer.jar, Forge's, Fabric's loader).

Wraps docker-py's ``containers.run(remove=True)`` with the conventions
we want: mount a host dir at /work, set cwd to /work, capture combined
stdout+stderr as a single decoded string for the caller to inspect on
failure. Synchronous to keep the implementation small; callers wrap in
``asyncio.to_thread`` if they need to await.
"""
from __future__ import annotations

import logging
from pathlib import Path

import docker
from docker.errors import ContainerError, ImageNotFound

log = logging.getLogger("ndrchst.jvm_installer")

JDK_IMAGE = "eclipse-temurin:21-jdk"


class JvmInstallError(RuntimeError):
    pass


def run_jdk_jar(
    *, image: str = JDK_IMAGE,
    workdir: Path,
    args: list[str],
    client: docker.DockerClient | None = None,
    timeout: int = 600,
) -> str:
    """Run ``java <args>`` inside a one-shot ``image`` container with
    ``workdir`` mounted at /work. Returns the container's stdout. Raises
    ``JvmInstallError`` on non-zero exit, capturing the engine's error
    output so the caller can surface a useful message.

    ``timeout`` is a docker engine kwarg; the container is reaped on exit
    regardless of how it terminated. Default 10min covers NeoForge's
    installer which is normally well under 2min but can be slow on cold
    library caches.
    """
    cli = client or docker.from_env()
    # Auto-pull on miss — same convention as runtime/docker.Docker
    try:
        cli.images.get(image)
    except ImageNotFound:
        log.info("Pulling %s for JVM install step", image)
        cli.images.pull(image)

    cmd = ["java", *args]
    try:
        raw = cli.containers.run(
            image=image,
            command=cmd,
            working_dir="/work",
            volumes={str(workdir.resolve()): {"bind": "/work", "mode": "rw"}},
            remove=True,
            stdout=True,
            stderr=True,
            stream=False,
        )
    except ContainerError as e:
        # ContainerError surfaces non-zero exit. .stderr carries combined
        # output from the doomed container.
        detail = (e.stderr or b"").decode("utf-8", errors="replace")
        raise JvmInstallError(
            f"JVM install step exited {e.exit_status}: "
            f"{cmd!r} in {workdir}\n{detail}"
        ) from e
    # docker-py returns bytes when not streaming; decode for the caller.
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
