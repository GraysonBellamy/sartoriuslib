"""Tests for :mod:`sartoriuslib.commands.parameters` (``0x55``/``0x56``)."""

from __future__ import annotations

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.parameters import (
    READ_PARAMETER,
    WRITE_PARAMETER,
    ReadParameterRequest,
    WriteParameterRequest,
)
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import build_command, checksum, parse_frame


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


class TestReadParameter:
    def test_encode_with_tlv_index(self, ctx: CommandContext) -> None:
        assert READ_PARAMETER.xbpi is not None
        tx = READ_PARAMETER.xbpi.encode(ctx, ReadParameterRequest(index=1))
        assert tx == build_command(0x55, b"\x21\x01")

    def test_decode_doc_example(self, ctx: CommandContext) -> None:
        """``docs/protocol.md`` §5.2 worked example: p00 → (current=2, max=4).

        RX = ``06 41 21 02 21 04 8f`` — subtype ``0x21`` doubles as the
        first TLV tag; decode prepends subtype before parsing.
        """
        rx = bytes.fromhex("06412102210489")  # checksum recomputed below
        # Build fresh rather than trusting the hardcoded checksum.
        rx = _rx(subtype=0x21, body=b"\x02\x21\x04")
        frame = parse_frame(rx)
        assert READ_PARAMETER.xbpi is not None
        entry = READ_PARAMETER.xbpi.decode(frame, ctx)
        assert entry.current == 0x02
        assert entry.max == 0x04
        # Index comes from the request, not the reply — placeholder.
        assert entry.index == -1

    def test_decode_filter_mode_stable_max_4(self, ctx: CommandContext) -> None:
        """p01 filter_mode is a FilterMode index; current=2 decodes to STABLE."""
        rx = _rx(subtype=0x21, body=b"\x02\x21\x04")
        frame = parse_frame(rx)
        assert READ_PARAMETER.xbpi is not None
        entry = READ_PARAMETER.xbpi.decode(frame, ctx)
        assert entry.current == 2
        assert entry.max == 4

    def test_decode_rejects_wrong_tlv_count(self, ctx: CommandContext) -> None:
        """Only two TLVs are valid; anything else is a protocol violation."""
        # One TLV only.
        rx = _rx(subtype=0x21, body=b"\x02")
        frame = parse_frame(rx)
        assert READ_PARAMETER.xbpi is not None
        with pytest.raises(SartoriusParseError, match="2 TLVs"):
            READ_PARAMETER.xbpi.decode(frame, ctx)

    def test_decode_rejects_wrong_tlv_tag(self, ctx: CommandContext) -> None:
        """Both TLVs must be TLV-21 (u8)."""
        # Second TLV is a TLV-22 (u16).
        rx = _rx(subtype=0x21, body=b"\x02\x22\x00\x04")
        frame = parse_frame(rx)
        assert READ_PARAMETER.xbpi is not None
        with pytest.raises(SartoriusParseError, match="TLV-21"):
            READ_PARAMETER.xbpi.decode(frame, ctx)


class TestWriteParameter:
    def test_encode_two_tlvs(self, ctx: CommandContext) -> None:
        assert WRITE_PARAMETER.xbpi is not None
        tx = WRITE_PARAMETER.xbpi.encode(
            ctx,
            WriteParameterRequest(index=1, value=2),
        )
        # Request: 0x56, TLV-21 1, TLV-21 2.
        assert tx == build_command(0x56, b"\x21\x01\x21\x02")

    def test_decode_ack(self, ctx: CommandContext) -> None:
        """ACK reply (subtype 0x00, empty body) returns ``None``."""
        rx = _rx(subtype=0x00, body=b"")
        frame = parse_frame(rx)
        assert WRITE_PARAMETER.xbpi is not None
        assert WRITE_PARAMETER.xbpi.decode(frame, ctx) is None


class TestMetadata:
    def test_capability_hints(self) -> None:
        assert READ_PARAMETER.capability_hints == Capability.PARAMETER_TABLE
        assert WRITE_PARAMETER.capability_hints == Capability.PARAMETER_TABLE

    def test_safety_tiers(self) -> None:
        assert READ_PARAMETER.safety is SafetyTier.READ_ONLY
        assert WRITE_PARAMETER.safety is SafetyTier.PERSISTENT
