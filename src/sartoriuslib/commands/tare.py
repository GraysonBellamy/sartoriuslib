"""Tare and zero commands — ``TARE`` (``0x14``) and ``ZERO`` (``0x18``).

Both return an xBPI ACK (subtype ``0x00``, empty body). Both are
:attr:`SafetyTier.STATEFUL` — transient state change, no EEPROM write,
runs freely without ``confirm=True`` (design §6.1). See
``docs/protocol.md`` §7.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, SbiVariant, XbpiVariant
from sartoriuslib.devices.capability import SafetyTier
from sartoriuslib.protocol.sbi import TOKEN_TARE, TOKEN_ZERO
from sartoriuslib.protocol.xbpi import build_command

if TYPE_CHECKING:
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "TARE",
    "ZERO",
    "TareRequest",
]


@dataclass(frozen=True, slots=True)
class TareRequest:
    """No-arg request; tare/zero take no parameters at the xBPI layer."""


@dataclass(frozen=True, slots=True)
class _AckOnlyVariant(XbpiVariant[TareRequest, None]):
    """Shared variant for opcodes whose reply is a bare ACK."""

    opcode: int

    def encode(self, ctx: CommandContext, request: TareRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> None:
        # Subtype is already validated by the protocol client; an ACK
        # reply (subtype 0x00) with an empty body is the expected shape.
        return None


@dataclass(frozen=True, slots=True)
class _SbiNoReplyVariant(SbiVariant[TareRequest, None]):
    """SBI key-emulation command with no reply line."""

    token: bytes
    expect_lines: int = 0

    def encode(self, ctx: CommandContext, request: TareRequest) -> bytes:
        return self.token

    def decode(self, reply: SbiReply, ctx: CommandContext) -> None:
        return None


TARE = Command[TareRequest, None](
    name="tare",
    xbpi=_AckOnlyVariant(opcode=0x14),
    sbi=_SbiNoReplyVariant(token=TOKEN_TARE),
    safety=SafetyTier.STATEFUL,
)

ZERO = Command[TareRequest, None](
    name="zero",
    xbpi=_AckOnlyVariant(opcode=0x18),
    sbi=_SbiNoReplyVariant(token=TOKEN_ZERO),
    safety=SafetyTier.STATEFUL,
)
