"""High-level server lifecycle: create / start / stop / restart / delete.

Composes platforms (install artifact) + docker (container) + rcon (commands).
The pure functions from the old core/server.py belong here, freed of globals.
"""
from __future__ import annotations
