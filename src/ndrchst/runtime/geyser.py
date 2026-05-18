"""Geyser + Floodgate auto-install for Java servers (Bedrock cross-play).

When a Java server (Paper/Purpur/Spigot) is created with `cross_play=True`,
this module drops the appropriate Geyser jar into plugins/ and Floodgate
alongside it, then writes the default configs. Bedrock clients can then
connect on UDP/19132.
"""
from __future__ import annotations
