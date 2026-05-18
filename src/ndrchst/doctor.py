"""Pre-flight environment check.

Runs a fixed list of checks and prints a coloured pass/fail report. Each
check returns (status, detail). Aggregated exit code is 0 if all pass,
1 if any fail.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .domain.models import Family
from .runtime.lifecycle import SERVERS_ROOT_DEFAULT
from .runtime.ports import is_port_free
from .store.db import DEFAULT_DB_PATH


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_python() -> CheckResult:
    ok = sys.version_info >= (3, 12)
    detail = f"{sys.version.split()[0]}"
    return CheckResult("Python ≥ 3.12", ok, detail)


def check_docker_module() -> CheckResult:
    try:
        import docker  # noqa: F401
        return CheckResult("docker-py importable", True, "ok")
    except ImportError as e:
        return CheckResult("docker-py importable", False, str(e))


def check_docker_daemon() -> CheckResult:
    try:
        import docker
        client = docker.from_env()
        client.ping()
        v = client.version().get("Version", "?")
        return CheckResult("Docker daemon reachable", True, f"engine {v}")
    except Exception as e:
        return CheckResult("Docker daemon reachable", False, f"{type(e).__name__}: {e}")


def check_docker_group() -> CheckResult:
    # Best-effort: read /etc/group; on macOS or non-standard setups this just
    # returns "unknown" rather than failing.
    if not Path("/etc/group").exists():
        return CheckResult("User in docker group", True, "not applicable on this OS")
    try:
        import grp
        members = grp.getgrnam("docker").gr_mem
        user = os.environ.get("USER", "")
        if user in members or os.geteuid() == 0:
            return CheckResult("User in docker group", True, f"{user} ∈ docker")
        return CheckResult(
            "User in docker group", False,
            f"{user} not in 'docker' group — run: sudo usermod -aG docker {user}",
        )
    except KeyError:
        return CheckResult("User in docker group", False, "'docker' group not present")


def check_disk_space(min_gb: int = 5) -> CheckResult:
    root = SERVERS_ROOT_DEFAULT
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    free_gb = usage.free / (1024**3)
    ok = free_gb >= min_gb
    return CheckResult(
        f"Free disk at {root}", ok,
        f"{free_gb:.1f} GB free (need ≥ {min_gb})",
    )


def check_port_free(port: int = 8080) -> CheckResult:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return CheckResult(f"Port {port} free", True, "available")
    except OSError as e:
        return CheckResult(f"Port {port} free", False, str(e))
    finally:
        s.close()


def check_registered_server_ports() -> list[CheckResult]:
    """For each server in the DB, probe its bound port with the right protocol.

    Bedrock servers need UDP; Java needs TCP. Doctor used to only verify the
    web-UI TCP port — silent UDP collisions caused first-boot failures users
    blamed on Mojang.
    """
    if not DEFAULT_DB_PATH.exists():
        return [CheckResult("MC ports for registered servers", True, "no DB yet — skipped")]

    import sqlite3
    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, family, port FROM servers ORDER BY port"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return [CheckResult("MC ports for registered servers", False, f"DB read failed: {e}")]

    if not rows:
        return [CheckResult("MC ports for registered servers", True, "no servers registered")]

    results: list[CheckResult] = []
    for r in rows:
        try:
            fam = Family(r["family"])
        except ValueError:
            results.append(CheckResult(
                f"Port {r['port']} for '{r['name']}'", False,
                f"unknown family {r['family']!r} in DB",
            ))
            continue
        proto = "UDP" if fam is Family.BEDROCK else "TCP"
        free = is_port_free(r["port"], fam)
        # "free" is what we want when the server is stopped; doctor doesn't
        # know whether each server should be running, so we report status
        # informationally rather than as a hard fail.
        ok = True
        detail = f"{proto} {r['port']} ({fam.value}): {'free' if free else 'in use (server may be running)'}"
        results.append(CheckResult(f"Port for '{r['name']}'", ok, detail))
    return results


CHECKS = [
    check_python,
    check_docker_module,
    check_docker_daemon,
    check_docker_group,
    check_disk_space,
    check_port_free,
]


def run() -> int:
    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    fails = 0
    for check in CHECKS:
        r = check()
        status = "[green]✓ pass[/]" if r.ok else "[red]✗ fail[/]"
        table.add_row(r.name, status, r.detail)
        if not r.ok:
            fails += 1

    for r in check_registered_server_ports():
        status = "[green]✓ pass[/]" if r.ok else "[red]✗ fail[/]"
        table.add_row(r.name, status, r.detail)
        if not r.ok:
            fails += 1

    console.print(table)
    if fails:
        console.print(f"\n[red]{fails} check(s) failed.[/]")
        return 1
    console.print("\n[green]All checks passed.[/]")
    return 0
