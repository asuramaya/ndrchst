"""Worlds: seed, spawn, gamerules, time/weather, reset.

Java reads level.dat (NBT). Bedrock reads level.dat in a different format —
keep the parsing behind a family check.
"""
from __future__ import annotations
