"""Status command — xBPI ``0x30`` (full 8-byte status block).

Decodes to the protocol-neutral :class:`BalanceStatus` dataclass.
Derives :class:`BalanceState` from the state byte per
``docs/protocol.md`` §8.2: ``0x82`` overload, ``0x84`` underload, and
bit ``0x08`` marks stable on both Cubis and non-Cubis families (just in
different bytes — the parser already normalises).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, SbiVariant, XbpiVariant
from sartoriuslib.devices.capability import SafetyTier
from sartoriuslib.devices.models import BalanceState, BalanceStatus
from sartoriuslib.protocol.sbi import TOKEN_PRINT, require_reading
from sartoriuslib.protocol.xbpi import build_command, decode_status_block_body

if TYPE_CHECKING:
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "STATUS_BLOCK",
    "StatusRequest",
]


@dataclass(frozen=True, slots=True)
class StatusRequest:
    """No-arg request for ``0x30`` read_balance_status_block."""


def _state_from_status_block(*, overload: bool, underload: bool, stable: bool) -> BalanceState:
    """Promote the 4 boolean signals to the :class:`BalanceState` enum."""
    if overload:
        return BalanceState.OVERLOAD
    if underload:
        return BalanceState.UNDERLOAD
    if stable:
        return BalanceState.STABLE
    return BalanceState.UNSTABLE


@dataclass(frozen=True, slots=True)
class _StatusBlockVariant(XbpiVariant[StatusRequest, BalanceStatus]):
    """xBPI ``0x30`` read_balance_status_block variant."""

    opcode: int = 0x30

    def encode(self, ctx: CommandContext, request: StatusRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> BalanceStatus:
        block = decode_status_block_body(reply.body)
        state = _state_from_status_block(
            overload=block.overload,
            underload=block.underload,
            stable=block.stable,
        )
        return BalanceStatus(
            stable=block.stable,
            state=state,
            isocal_due=block.isocal_due,
            adc_trusted=block.adc_trusted,
            sequence=block.sequence,
            raw_state=block.state,
            raw_status=block.status,
            raw=reply.raw,
        )


@dataclass(frozen=True, slots=True)
class _SbiStatusVariant(SbiVariant[StatusRequest, BalanceStatus]):
    """SBI status approximation from an ``ESC P`` weight line."""

    token: bytes = TOKEN_PRINT
    expect_lines: int = 1

    def encode(self, ctx: CommandContext, request: StatusRequest) -> bytes:
        return self.token

    def decode(self, reply: SbiReply, ctx: CommandContext) -> BalanceStatus:
        reading = require_reading(reply)
        state = _state_from_status_block(
            overload=reading.overload,
            underload=reading.underload,
            stable=reading.stable,
        )
        return BalanceStatus(
            stable=reading.stable,
            state=state,
            isocal_due=None,
            adc_trusted=None,
            sequence=None,
            raw_state=None,
            raw_status=None,
            raw=reply.raw,
        )


STATUS_BLOCK = Command[StatusRequest, BalanceStatus](
    name="status_block",
    xbpi=_StatusBlockVariant(),
    sbi=_SbiStatusVariant(),
    safety=SafetyTier.READ_ONLY,
)
