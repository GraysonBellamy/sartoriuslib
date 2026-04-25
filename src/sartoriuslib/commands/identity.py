"""Identity-read primitives composed by :meth:`Balance.identify`.

Each primitive decodes one opcode's reply into a typed value; the
balance composes them into a :class:`DeviceInfo`.

xBPI opcodes per ``docs/protocol.md`` §7.1, §7.10:

- ``0x00`` read_software_version  → subtype ``0x4A``, 10-byte packed blob
- ``0x01`` read_factory_number    → subtype ``0x45``, 5-byte blob
- ``0x02`` read_weigh_cell_model  → subtype ``0x54``, 20-byte ASCII (null-padded)
- ``0x05`` read_oem_text          → subtype ``0x50``, 16-byte ASCII
- ``0x07`` read_manufacturer      → subtype ``0x50``, 16-byte ASCII
- ``0x71`` read_sbn_address       → subtype ``0x21``, 1 byte
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, SbiVariant, XbpiVariant
from sartoriuslib.devices.capability import SafetyTier
from sartoriuslib.protocol.sbi import (
    TOKEN_SERIAL,
    TOKEN_SOFTWARE,
    TOKEN_TYPE,
    require_identity_text,
)
from sartoriuslib.protocol.xbpi import build_command

if TYPE_CHECKING:
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "READ_FACTORY_NUMBER",
    "READ_MANUFACTURER",
    "READ_MODEL",
    "READ_OEM_TEXT",
    "READ_SBN",
    "READ_SW_VERSION",
    "IdentityRequest",
]


@dataclass(frozen=True, slots=True)
class IdentityRequest:
    """No-arg request for identity primitives."""


def _decode_ascii_blob(body: bytes) -> str:
    r"""Null-pad-strip an ASCII blob into a clean Python str.

    Sartorius pads these fields with ``\x00``; some also have trailing
    spaces. Strip both. Replaces undecodable bytes with ``?`` rather
    than raising — forward-compatibility for firmware we have not
    captured.
    """
    return body.rstrip(b"\x00 ").decode("ascii", errors="replace").strip()


# ---------------------------------------------------------------------------
# String-valued identity reads.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AsciiBlobVariant(XbpiVariant[IdentityRequest, str]):
    opcode: int

    def encode(self, ctx: CommandContext, request: IdentityRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> str:
        return _decode_ascii_blob(reply.body)


@dataclass(frozen=True, slots=True)
class _SbiIdentityTextVariant(SbiVariant[IdentityRequest, str]):
    """SBI identity command that returns one printable text line."""

    token: bytes
    expect_lines: int = 1

    def encode(self, ctx: CommandContext, request: IdentityRequest) -> bytes:
        return self.token

    def decode(self, reply: SbiReply, ctx: CommandContext) -> str:
        return require_identity_text(reply, allow_weight_like=False)


@dataclass(frozen=True, slots=True)
class _SbiIdentityBytesVariant(SbiVariant[IdentityRequest, bytes]):
    """SBI identity command returned as ASCII bytes for existing call sites."""

    token: bytes
    expect_lines: int = 1

    def encode(self, ctx: CommandContext, request: IdentityRequest) -> bytes:
        return self.token

    def decode(self, reply: SbiReply, ctx: CommandContext) -> bytes:
        return require_identity_text(reply).encode("ascii", errors="replace")


READ_MODEL = Command[IdentityRequest, str](
    name="read_model",
    xbpi=_AsciiBlobVariant(opcode=0x02),
    sbi=_SbiIdentityTextVariant(token=TOKEN_TYPE),
    safety=SafetyTier.READ_ONLY,
)

READ_MANUFACTURER = Command[IdentityRequest, str](
    name="read_manufacturer",
    xbpi=_AsciiBlobVariant(opcode=0x07),
    safety=SafetyTier.READ_ONLY,
)

READ_OEM_TEXT = Command[IdentityRequest, str](
    name="read_oem_text",
    xbpi=_AsciiBlobVariant(opcode=0x05),
    safety=SafetyTier.READ_ONLY,
)


# ---------------------------------------------------------------------------
# Byte-blob reads where semantic decoding is not yet done (raw is fine).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RawBlobVariant(XbpiVariant[IdentityRequest, bytes]):
    opcode: int

    def encode(self, ctx: CommandContext, request: IdentityRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> bytes:
        return reply.body


READ_SW_VERSION = Command[IdentityRequest, bytes](
    name="read_software_version",
    xbpi=_RawBlobVariant(opcode=0x00),
    sbi=_SbiIdentityBytesVariant(token=TOKEN_SOFTWARE),
    safety=SafetyTier.READ_ONLY,
)

READ_FACTORY_NUMBER = Command[IdentityRequest, bytes](
    name="read_factory_number",
    xbpi=_RawBlobVariant(opcode=0x01),
    sbi=_SbiIdentityBytesVariant(token=TOKEN_SERIAL),
    safety=SafetyTier.READ_ONLY,
)


# ---------------------------------------------------------------------------
# SBN address — single u8 TLV body in a short-data subtype.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ReadSbnVariant(XbpiVariant[IdentityRequest, int]):
    opcode: int = 0x71

    def encode(self, ctx: CommandContext, request: IdentityRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> int:
        # Short_data subtype 0x21 / body = single u8.
        if len(reply.body) < 1:
            return 0
        return reply.body[0]


READ_SBN = Command[IdentityRequest, int](
    name="read_sbn",
    xbpi=_ReadSbnVariant(),
    safety=SafetyTier.READ_ONLY,
)
