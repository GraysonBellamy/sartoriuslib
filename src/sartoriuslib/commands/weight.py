"""Weight-read commands — net, gross, stored tare (std + hires variants).

xBPI opcodes per ``docs/protocol.md`` §7.4. All decode to the
protocol-neutral :class:`Reading` dataclass via the shared
:func:`_measurement_reply_to_reading` helper so the SBI variants
produce semantically equivalent output.

``hires`` variants (``0x1F``, ``0x21``) require a TLV-21 arg (``1`` =
10×, ``2`` = 100×). Standard-resolution reads (``0x1E``, ``0x20``,
``0x22``) take no args.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, SbiVariant, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.models import Reading
from sartoriuslib.protocol.sbi import TOKEN_PRINT, require_reading
from sartoriuslib.protocol.xbpi import (
    build_command,
    decode_measurement_body,
    encode_tlv,
)

if TYPE_CHECKING:
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "READ_GROSS",
    "READ_GROSS_HIRES",
    "READ_NET",
    "READ_NET_HIRES",
    "READ_TARE_VALUE",
    "ReadWeightHiresRequest",
    "ReadWeightRequest",
]


@dataclass(frozen=True, slots=True)
class ReadWeightRequest:
    """No-arg request for standard-resolution weight reads."""


@dataclass(frozen=True, slots=True)
class ReadWeightHiresRequest:
    """High-resolution weight-read request.

    ``resolution = 1`` → 10× resolution (0.1 mg on a 1 mg balance).
    ``resolution = 2`` → 100× resolution (0.01 mg).
    """

    resolution: int = 1


def _measurement_reply_to_reading(reply: XbpiFrame, ctx: CommandContext) -> Reading:
    """Turn a measurement-body reply into a protocol-neutral :class:`Reading`.

    ``value`` is ``None`` on the off-scale sentinel; measurement-frame
    bytes alone can't disambiguate overload from underload, so the
    ``overload`` / ``underload`` flags stay ``False`` here. Callers that
    need the distinction invoke :meth:`Balance.status`.
    """
    body = decode_measurement_body(reply.body)
    status_flags: dict[str, bool] = {
        "stable": body.stable,
        "off_scale": body.off_scale,
    }
    return Reading(
        value=body.value,
        unit=body.unit,
        sign=body.sign,
        stable=body.stable,
        overload=False,
        underload=False,
        decimals=body.decimals,
        sequence=None,
        status_flags=status_flags,
        protocol=ctx.protocol,
        received_at=datetime.now(UTC),
        monotonic_ns=time.monotonic_ns(),
        raw=reply.raw,
    )


# ---------------------------------------------------------------------------
# Standard-resolution reads (no-arg opcodes).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ReadWeightStdVariant(XbpiVariant[ReadWeightRequest, Reading]):
    """Base variant for standard-resolution weight reads.

    Subclasses set ``opcode`` to 0x1E / 0x20 / 0x22 and the shared
    encode/decode produces the :class:`Reading`.
    """

    opcode: int

    def encode(self, ctx: CommandContext, request: ReadWeightRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> Reading:
        return _measurement_reply_to_reading(reply, ctx)


@dataclass(frozen=True, slots=True)
class _SbiPrintWeightVariant(SbiVariant[ReadWeightRequest, Reading]):
    """SBI ``ESC P`` print command — returns the current display value."""

    token: bytes = TOKEN_PRINT
    expect_lines: int = 1

    def encode(self, ctx: CommandContext, request: ReadWeightRequest) -> bytes:
        return self.token

    def decode(self, reply: SbiReply, ctx: CommandContext) -> Reading:
        return require_reading(reply)


READ_NET = Command[ReadWeightRequest, Reading](
    name="read_net",
    xbpi=_ReadWeightStdVariant(opcode=0x1E),
    sbi=_SbiPrintWeightVariant(),
    safety=SafetyTier.READ_ONLY,
)

READ_GROSS = Command[ReadWeightRequest, Reading](
    name="read_gross",
    xbpi=_ReadWeightStdVariant(opcode=0x20),
    safety=SafetyTier.READ_ONLY,
)

READ_TARE_VALUE = Command[ReadWeightRequest, Reading](
    name="read_tare_value",
    xbpi=_ReadWeightStdVariant(opcode=0x22),
    safety=SafetyTier.READ_ONLY,
)


# ---------------------------------------------------------------------------
# High-resolution reads (TLV-21 resolution arg).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ReadWeightHiresVariant(XbpiVariant[ReadWeightHiresRequest, Reading]):
    opcode: int

    def encode(self, ctx: CommandContext, request: ReadWeightHiresRequest) -> bytes:
        return build_command(
            self.opcode,
            encode_tlv(0x21, request.resolution),
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> Reading:
        return _measurement_reply_to_reading(reply, ctx)


READ_NET_HIRES = Command[ReadWeightHiresRequest, Reading](
    name="read_net_hires",
    xbpi=_ReadWeightHiresVariant(opcode=0x1F),
    capability_hints=Capability.HIRES_WEIGHT,
    safety=SafetyTier.READ_ONLY,
)

READ_GROSS_HIRES = Command[ReadWeightHiresRequest, Reading](
    name="read_gross_hires",
    xbpi=_ReadWeightHiresVariant(opcode=0x21),
    capability_hints=Capability.HIRES_WEIGHT,
    safety=SafetyTier.READ_ONLY,
)
