"""Parameter-table raw access — ``0x55`` read, ``0x56`` write.

Per ``docs/protocol.md`` §7.8 and §5.2 / §5.3:

- ``0x55`` read_parameter: TX ``[0x55][TLV-21 idx]``, RX body contains
  two u8 TLVs ``(current, max)``. The subtype byte ``0x21`` doubles as
  the first TLV's tag, so decode prepends the subtype before parsing.
- ``0x56`` write_parameter: TX ``[0x56][TLV-21 idx][TLV-21 val]``,
  RX is an xBPI ACK.

Writing is :attr:`SafetyTier.PERSISTENT` — the session refuses the
call without ``confirm=True`` (design §6.1).

Typed accessors (``Balance.get_filter_mode()`` / ``set_filter_mode()``)
build on these two primitives plus the
:class:`sartoriuslib.registry.parameters.ParameterSpec` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.models import ParameterEntry
from sartoriuslib.errors import ErrorContext, SartoriusParseError
from sartoriuslib.protocol.xbpi import (
    build_command,
    encode_tlv,
    parse_tlv_sequence,
)

if TYPE_CHECKING:
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "READ_PARAMETER",
    "WRITE_PARAMETER",
    "ReadParameterRequest",
    "WriteParameterRequest",
]

#: TLV tag for a u8 request/response arg — the only tag valid in a
#: parameter-table reply (``docs/protocol.md`` §10).
_TLV_U8_TAG: int = 0x21
#: Number of TLVs in a valid ``0x55`` reply: ``(current, max)``.
_EXPECTED_TLV_COUNT: int = 2


@dataclass(frozen=True, slots=True)
class ReadParameterRequest:
    """One parameter-table read at ``index``."""

    index: int


@dataclass(frozen=True, slots=True)
class WriteParameterRequest:
    """One parameter-table write: set index ``index`` to ``value``."""

    index: int
    value: int


@dataclass(frozen=True, slots=True)
class _ReadParameterVariant(XbpiVariant[ReadParameterRequest, ParameterEntry]):
    opcode: int = 0x55

    def encode(self, ctx: CommandContext, request: ReadParameterRequest) -> bytes:
        return build_command(
            self.opcode,
            encode_tlv(_TLV_U8_TAG, request.index),
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> ParameterEntry:
        # Subtype doubles as the first TLV tag — prepend it before
        # walking the body (``docs/protocol.md`` §5.3).
        combined = bytes([reply.subtype]) + bytes(reply.body)
        tlvs = parse_tlv_sequence(combined)
        if len(tlvs) != _EXPECTED_TLV_COUNT:
            raise SartoriusParseError(
                f"read_parameter reply should have {_EXPECTED_TLV_COUNT} TLVs, got {len(tlvs)}",
                context=ErrorContext(
                    command_name="read_parameter",
                    opcode=self.opcode,
                    raw_response=reply.raw,
                    extra={"tlv_count": len(tlvs)},
                ),
            )
        current_tag, current_value = tlvs[0]
        max_tag, max_value = tlvs[1]
        if current_tag != _TLV_U8_TAG or max_tag != _TLV_U8_TAG:
            raise SartoriusParseError(
                f"read_parameter expects two TLV-21 records, got tags "
                f"0x{current_tag:02x}, 0x{max_tag:02x}",
                context=ErrorContext(
                    command_name="read_parameter",
                    opcode=self.opcode,
                    raw_response=reply.raw,
                ),
            )
        # We don't know the request index here — the balance doesn't
        # echo it. The Balance facade carries the index through and
        # overwrites via :func:`dataclasses.replace` so the returned
        # entry can always be round-tripped.
        return ParameterEntry(
            index=-1,
            current=current_value[0],
            max=max_value[0],
            raw=bytes(reply.body),
        )


@dataclass(frozen=True, slots=True)
class _WriteParameterVariant(XbpiVariant[WriteParameterRequest, None]):
    opcode: int = 0x56

    def encode(self, ctx: CommandContext, request: WriteParameterRequest) -> bytes:
        args = encode_tlv(_TLV_U8_TAG, request.index) + encode_tlv(_TLV_U8_TAG, request.value)
        return build_command(
            self.opcode,
            args,
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> None:
        # ACK reply — protocol client has already validated the subtype.
        return None


READ_PARAMETER = Command[ReadParameterRequest, ParameterEntry](
    name="read_parameter",
    xbpi=_ReadParameterVariant(),
    capability_hints=Capability.PARAMETER_TABLE,
    safety=SafetyTier.READ_ONLY,
    parameterized=True,
)

WRITE_PARAMETER = Command[WriteParameterRequest, None](
    name="write_parameter",
    xbpi=_WriteParameterVariant(),
    capability_hints=Capability.PARAMETER_TABLE,
    safety=SafetyTier.PERSISTENT,
    parameterized=True,
)
