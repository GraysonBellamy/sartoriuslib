"""Semantic command specs with per-protocol variants.

See design doc §4.2 (shape) and §6 (facade surface).
"""

from __future__ import annotations

from sartoriuslib.commands.base import (
    Command,
    CommandContext,
    SbiVariant,
    XbpiVariant,
)

__all__ = [
    "Command",
    "CommandContext",
    "SbiVariant",
    "XbpiVariant",
]
