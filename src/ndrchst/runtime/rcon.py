"""RCON client.

Java: standard Mojang RCON protocol over TCP.
Bedrock: no RCON; commands flow through the BDS stdin pipe instead. The
caller dispatches based on platform family.
"""
from __future__ import annotations
