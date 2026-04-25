"""``Command[Req, Resp]`` + ``XbpiVariant`` + ``SbiVariant`` + ``CommandContext``.

One :class:`Command` carries at most one variant per protocol; either or
both may be ``None``. The session picks the variant matching its active
protocol and dispatches through ``variant.encode`` / ``variant.decode``.

Per design doc §4.2, per-protocol work lives on *variant objects*, not
as methods bolted onto :class:`Command`. That keeps the opcode or SBI
token co-located with the logic that uses it, makes "not implemented
for this protocol" trivially expressible as ``None``, and keeps the
command dataclass pure metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.kind import BalanceFamily

if TYPE_CHECKING:
    from sartoriuslib.firmware import FirmwareVersion
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "NO_CAPABILITY",
    "Command",
    "CommandContext",
    "SbiVariant",
    "XbpiVariant",
]


#: Module-level :class:`Capability` zero-bitmap singleton.
#:
#: Ruff (``B008`` / ``RUF009``) flags constructing ``Capability(0)`` in
#: dataclass / function defaults — the call would happen at class-definition
#: time. Use this constant instead.
NO_CAPABILITY: Capability = Capability(0)

#: Empty :class:`frozenset` default for ``family_hints``. Frozen so sharing
#: the same instance across :class:`Command` specs is safe.
_NO_FAMILIES: frozenset[BalanceFamily] = frozenset()


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Context threaded through command encode/decode.

    Variants stay pure functions of ``(ctx, request) -> bytes`` /
    ``(reply, ctx) -> Resp`` by receiving the small amount of session
    state they need through this struct: the active protocol, SBN
    addressing, and (when known) firmware and family.
    """

    protocol: ProtocolKind
    src_sbn: int = 0x01
    dst_sbn: int = 0x09
    firmware: FirmwareVersion | None = None
    family: BalanceFamily = BalanceFamily.UNKNOWN


class XbpiVariant[Req, Resp](ABC):
    """xBPI variant of a command.

    Subclasses should set ``opcode`` as a class attribute and override
    :meth:`encode` and :meth:`decode`. Keep subclasses frozen
    (``@dataclass(frozen=True, slots=True)``) to preserve the
    "specs are immutable" invariant at the command layer.
    """

    opcode: int

    @abstractmethod
    def encode(self, ctx: CommandContext, request: Req) -> bytes:
        """Encode ``request`` into full TX frame bytes (length-prefixed)."""

    @abstractmethod
    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> Resp:
        """Decode an already-validated reply frame into the typed response."""


class SbiVariant[Req, Resp](ABC):
    """SBI variant of a command.

    Declared alongside :class:`XbpiVariant` so :class:`Command` can carry
    both variant slots and the session can refuse pre-I/O when the active
    protocol has no variant.

    ``expect_lines`` is the number of newline-terminated reply lines the
    variant expects. Control tokens like ``ESC T`` / ``ESC V`` set this
    to ``0`` because the device acknowledges silently.
    """

    token: bytes
    expect_lines: int = 1

    @abstractmethod
    def encode(self, ctx: CommandContext, request: Req) -> bytes:
        """Encode ``request`` into the ASCII line(s) to send."""

    @abstractmethod
    def decode(self, reply: SbiReply, ctx: CommandContext) -> Resp:
        """Decode the SBI reply into the typed response."""


@dataclass(frozen=True, slots=True)
class Command[Req, Resp]:
    """Declarative command spec.

    Protocol mismatch (active protocol's variant is ``None``) is the
    *only* hard pre-I/O gate that comes from this spec. ``family_hints``
    and ``capability_hints`` are advisory priors consulted by the session
    — they upgrade to hard refusals only under ``strict=True`` (design
    doc §6.1).

    ``parameterized`` flags commands whose request carries an
    application-level argument that selects a sub-resource (a sensor
    index, a parameter-table row, an area number, ...). Some firmwares
    answer xBPI ``0x04`` ("unsupported/unknown opcode") for an
    out-of-range argument value when they should answer ``0x10`` (index
    out of range) — the wire is technically incorrect but the device is
    what we have. The session translates ``0x04`` on a parameterized
    command into :class:`SartoriusIndexOutOfRangeError` (the semantic
    intent) and skips the
    :attr:`Availability.UNSUPPORTED`-sticky cache update so an
    out-of-range index for sensor 4 doesn't lock out sensors 0-3 for
    the rest of the session. Discovered on hardware day when probing
    ``temperature(4)`` poisoned ``temperature(0..3)``.
    """

    name: str
    xbpi: XbpiVariant[Req, Resp] | None = None
    sbi: SbiVariant[Req, Resp] | None = None
    family_hints: frozenset[BalanceFamily] = _NO_FAMILIES
    capability_hints: Capability = NO_CAPABILITY
    safety: SafetyTier = SafetyTier.READ_ONLY
    min_firmware: FirmwareVersion | None = None
    max_firmware: FirmwareVersion | None = None
    parameterized: bool = False
