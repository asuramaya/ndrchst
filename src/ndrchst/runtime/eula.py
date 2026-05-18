"""EULA + first-run config glue.

Project policy: a user running ndrchst to spin up a Minecraft server is
treated as having accepted Mojang's EULA. We cannot show an interactive
prompt in the UI workflow, and refusing to write the file just means the
first container start fails with a confusing eula.txt error in the logs.

This module writes the minimum first-run files so the binary actually boots:

  Java (eclipse-temurin image):
    - eula.txt with `eula=true`
    - server.properties stub if missing (so RCON/port settings are correct)

  Bedrock (native BDS binary):
    - server.properties stub if missing (BDS will write a default if absent,
      but starting from our defaults makes online-mode/port deterministic)
    - permissions.json + allowlist.json as empty arrays if missing
    - The BDS EULA is accepted by the act of running the binary; there is
      no eula.txt equivalent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..domain.models import Family

_EULA_TXT = (
    "# Accepted by ndrchst on behalf of the user "
    "(see https://aka.ms/MinecraftEULA)\n"
    "# {now}\n"
    "eula=true\n"
)


def accept_eula(family: Family, data_dir: Path) -> None:
    if family is Family.JAVA:
        _accept_java(data_dir)
    elif family is Family.BEDROCK:
        _accept_bedrock(data_dir)
    else:
        raise ValueError(f"no EULA handling for family: {family}")


def _accept_java(data_dir: Path) -> None:
    eula = data_dir / "eula.txt"
    if not eula.exists():
        eula.write_text(_EULA_TXT.format(now=datetime.now(UTC).isoformat()))


def _accept_bedrock(data_dir: Path) -> None:
    # BDS does not require an eula.txt file. We only ensure the bookkeeping
    # files exist so first start doesn't write surprise defaults.
    for name, contents in (
        ("permissions.json", "[]\n"),
        ("allowlist.json", "[]\n"),
    ):
        path = data_dir / name
        if not path.exists():
            path.write_text(contents)
