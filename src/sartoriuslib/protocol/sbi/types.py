r"""Immutable SBI wire-level types.

SBI is line-oriented ASCII. The transport reads complete ``\r\n``-terminated
records; the parser turns each record into an :class:`SbiLine` and collects
them into an :class:`SbiReply` for command variants to decode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sartoriuslib.devices.models import Reading

__all__ = ["SbiLine", "SbiLineKind", "SbiReply"]


class SbiLineKind(StrEnum):
    """Classifier for one decoded SBI line."""

    EMPTY = "empty"
    IDENTITY = "identity"
    REFUSAL = "refusal"
    WEIGHT = "weight"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SbiLine:
    """One parsed SBI line.

    ``raw`` includes the on-wire line terminator if it was present.
    ``text`` is ASCII-decoded with the terminator stripped. ``reading`` is
    populated only for weight/autoprint lines.
    """

    raw: bytes
    text: str
    kind: SbiLineKind
    reading: Reading | None = None


@dataclass(frozen=True, slots=True)
class SbiReply:
    """One SBI reply.

    ``lines`` holds parsed records; ``raw`` is the concatenated on-wire
    payload that produced them. No-response commands such as front-panel
    key emulation use an empty ``lines`` tuple and ``raw=b""``.
    """

    lines: tuple[SbiLine, ...]
    raw: bytes

    @property
    def first_line(self) -> SbiLine | None:
        """First parsed line, or ``None`` for no-response commands."""
        return self.lines[0] if self.lines else None
