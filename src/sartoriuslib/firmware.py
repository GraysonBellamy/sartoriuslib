"""Firmware version model (stub).

Concrete parsing rules per firmware family land in the xBPI IDENTIFY decoder
(see design doc §5). This module exposes the typed value object that the
rest of the library reasons about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class FirmwareVersion:
    """Immutable firmware version. Ordering is tuple-lexicographic.

    Exact numbering conventions differ per family; see design doc §16 Q5.
    """

    major: int
    minor: int = 0
    patch: int = 0
    raw: str | None = None


__all__ = ["FirmwareVersion"]
