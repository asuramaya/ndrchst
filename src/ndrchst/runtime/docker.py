"""Docker runtime — the only supported runtime in v0.

Wraps the docker-py SDK. Java servers use an eclipse-temurin base; Bedrock
runs the official `itzg/minecraft-bedrock-server` image (or an in-house
Mojang-binary image — TBD during v0 implementation).
"""
from __future__ import annotations
