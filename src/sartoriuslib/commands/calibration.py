"""Calibration commands — last cal record (``0xB9``) and internal adjust (``0x28``).

Per ``docs/protocol.md`` §7.7 and §7.12:

- ``0xB9`` read_last_cal_record: subtype ``0x51``, 17-byte body.
  Layout in §7.12. Ignores TLV args. ``READ_ONLY``.
- ``0x28`` start_adjustment: TLV-21 arg selects cal type (e.g.
  ``0x78`` / ``120`` = internal adjust). Side-effecting, observable
  physically — ``DANGEROUS``.

The ``CalRecord`` dataclass preserves the three-tier storage
distinction from §7.12: :attr:`CalRecord.temperature_celsius` can be
present while :attr:`signature` + :attr:`counters` are all-zero (the
RAM metadata buffer was cleared by a cold boot but the temperature
field lives in a separate backing location).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.models import CalRecord
from sartoriuslib.errors import ErrorContext, SartoriusParseError
from sartoriuslib.protocol.xbpi import build_command, encode_tlv

if TYPE_CHECKING:
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "INTERNAL_ADJUST",
    "INTERNAL_ADJUST_CAL_TYPE",
    "LAST_CAL_RECORD",
    "InternalAdjustRequest",
    "LastCalRecordRequest",
]


#: ``0x28`` TLV-21 arg for an internal adjustment. Per ``docs/protocol.md``
#: §7.7 the accepted range is 112..123 (``0x70..0x7B``); ``0x78`` (120)
#: is the canonical internal-adjust selector.
INTERNAL_ADJUST_CAL_TYPE: int = 0x78


#: Length of the ``0xB9`` cal-record body (subtype ``0x51``).
_CAL_RECORD_LEN: int = 17

#: Byte offsets for the §7.12 cal-record layout.
_TEMP_SLICE: slice = slice(0, 4)
_SIGNATURE_SLICE: slice = slice(4, 13)
_COUNTERS_SLICE: slice = slice(13, 16)
_PADDING_OFFSET: int = 16

#: Bytes [0..3] when the cal-temperature field has not been written.
_TEMP_SENTINEL: bytes = b"\x00\x00\x00\x00"


@dataclass(frozen=True, slots=True)
class LastCalRecordRequest:
    """No-arg request for ``0xB9``."""


@dataclass(frozen=True, slots=True)
class InternalAdjustRequest:
    """Start-adjustment request.

    ``cal_type`` is the TLV-21 arg; defaults to
    :data:`INTERNAL_ADJUST_CAL_TYPE`.
    """

    cal_type: int = INTERNAL_ADJUST_CAL_TYPE


@dataclass(frozen=True, slots=True)
class _LastCalRecordVariant(XbpiVariant[LastCalRecordRequest, CalRecord]):
    opcode: int = 0xB9

    def encode(self, ctx: CommandContext, request: LastCalRecordRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> CalRecord:
        body = bytes(reply.body)
        if len(body) != _CAL_RECORD_LEN:
            raise SartoriusParseError(
                f"cal-record body must be {_CAL_RECORD_LEN} bytes, got {len(body)}",
                context=ErrorContext(
                    command_name="last_cal_record",
                    opcode=self.opcode,
                    raw_response=reply.raw,
                ),
            )
        temp_bytes = body[_TEMP_SLICE]
        if temp_bytes == _TEMP_SENTINEL:
            temperature: float | None = None
        else:
            temperature = struct.unpack(">f", temp_bytes)[0]
        return CalRecord(
            temperature_celsius=temperature,
            signature=bytes(body[_SIGNATURE_SLICE]),
            counters=bytes(body[_COUNTERS_SLICE]),
            padding=body[_PADDING_OFFSET],
            raw=body,
        )


@dataclass(frozen=True, slots=True)
class _InternalAdjustVariant(XbpiVariant[InternalAdjustRequest, None]):
    opcode: int = 0x28

    def encode(self, ctx: CommandContext, request: InternalAdjustRequest) -> bytes:
        return build_command(
            self.opcode,
            encode_tlv(0x21, request.cal_type),
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> None:
        # ACK reply — protocol client validated subtype.
        return None


LAST_CAL_RECORD = Command[LastCalRecordRequest, CalRecord](
    name="last_cal_record",
    xbpi=_LastCalRecordVariant(),
    capability_hints=Capability.CAL_RECORD,
    safety=SafetyTier.READ_ONLY,
)

INTERNAL_ADJUST = Command[InternalAdjustRequest, None](
    name="internal_adjust",
    xbpi=_InternalAdjustVariant(),
    capability_hints=Capability.INTERNAL_CAL,
    safety=SafetyTier.DANGEROUS,
)
