"""Runtime config knobs that travel with the Server record.

Two user-editable fields beyond memory_mb:
  * extra_jvm_flags — free-form Java args appended after -Xmx; Java only
  * env_vars — KEY=VALUE lines that go into the container env

Both fields are stored as plain strings in SQLite (no JSON, no separate table)
because they're round-tripped to a textarea unchanged. Validation lives here so
the route, lifecycle, and tests share one truth.
"""
from __future__ import annotations

import re
import shlex

# Hard caps: protect against accidental copy/paste explosions in a textarea.
MAX_JVM_FLAGS_LEN = 2048
MAX_ENV_VARS_LEN = 4096
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 1024 * 256  # 256 GiB — far above any realistic single host

# Reserved keys we own — users can't shadow these via the env_vars textarea
# because doing so silently breaks the runtime contract (EULA acceptance,
# library resolution for BDS).
_RESERVED_ENV = frozenset({"EULA", "LD_LIBRARY_PATH"})

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# JVM flags that would conflict with what _build_spec already injects.
_RESERVED_JVM_PREFIXES = ("-Xmx", "-Xms", "-jar")


class ConfigError(ValueError):
    """Surfaces as 400 in the route. Message is shown to the user."""


def validate_memory(mb: int) -> int:
    if mb < MIN_MEMORY_MB:
        raise ConfigError(f"memory must be >= {MIN_MEMORY_MB} MB (got {mb})")
    if mb > MAX_MEMORY_MB:
        raise ConfigError(f"memory must be <= {MAX_MEMORY_MB} MB (got {mb})")
    return mb


def normalize_jvm_flags(raw: str | None) -> str | None:
    """Strip, validate, and return None for empty. Splits with shlex so we
    catch unbalanced quotes before they hit the container."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) > MAX_JVM_FLAGS_LEN:
        raise ConfigError(
            f"JVM flags exceed {MAX_JVM_FLAGS_LEN} chars; shorten them"
        )
    try:
        parts = shlex.split(raw)
    except ValueError as e:
        raise ConfigError(f"JVM flags don't parse: {e}") from e
    for part in parts:
        for reserved in _RESERVED_JVM_PREFIXES:
            if part.startswith(reserved):
                raise ConfigError(
                    f"{part!r} conflicts with a flag ndrchst sets automatically "
                    f"(memory_mb owns -Xmx/-Xms; -jar is fixed)"
                )
    return raw


def parse_env_vars(raw: str | None) -> dict[str, str]:
    """Parse "KEY=VALUE" lines into a dict. Strips, ignores blanks/comments.

    Raises ConfigError on malformed lines so the UI can show the user where
    they went wrong instead of silently dropping a typo.
    """
    if not raw:
        return {}
    if len(raw) > MAX_ENV_VARS_LEN:
        raise ConfigError(
            f"env vars exceed {MAX_ENV_VARS_LEN} chars; shorten them"
        )
    env: dict[str, str] = {}
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ConfigError(
                f"line {lineno}: expected KEY=VALUE, got {stripped!r}"
            )
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.match(key):
            raise ConfigError(
                f"line {lineno}: {key!r} is not a valid env var name"
            )
        if key in _RESERVED_ENV:
            raise ConfigError(
                f"line {lineno}: {key!r} is reserved by ndrchst"
            )
        env[key] = value
    return env


def normalize_env_vars(raw: str | None) -> str | None:
    """Round-trip env_vars through parse_env_vars to validate, returning
    None for empty (so we don't store an empty string in SQLite)."""
    if raw is None:
        return None
    parsed = parse_env_vars(raw)
    # Re-serialise so what we store matches what we'd render back to the user.
    if not parsed:
        return None
    return "\n".join(f"{k}={v}" for k, v in parsed.items())
