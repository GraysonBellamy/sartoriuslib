"""Tests for :mod:`sartoriuslib.commands.weight`."""

from __future__ import annotations

import math

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.weight import (
    READ_GROSS,
    READ_GROSS_HIRES,
    READ_NET,
    READ_NET_HIRES,
    READ_TARE_VALUE,
    ReadWeightHiresRequest,
    ReadWeightRequest,
)
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi import parse_reply
from sartoriuslib.protocol.xbpi import build_command, parse_frame
from sartoriuslib.registry.units import Sign, Unit


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


class TestEncodeStdOpcodes:
    def test_read_net_opcode_0x1e_no_args(self, ctx: CommandContext) -> None:
        assert READ_NET.xbpi is not None
        tx = READ_NET.xbpi.encode(ctx, ReadWeightRequest())
        assert tx == build_command(0x1E)

    def test_read_gross_opcode_0x20(self, ctx: CommandContext) -> None:
        assert READ_GROSS.xbpi is not None
        tx = READ_GROSS.xbpi.encode(ctx, ReadWeightRequest())
        assert tx == build_command(0x20)

    def test_read_tare_value_opcode_0x22(self, ctx: CommandContext) -> None:
        assert READ_TARE_VALUE.xbpi is not None
        tx = READ_TARE_VALUE.xbpi.encode(ctx, ReadWeightRequest())
        assert tx == build_command(0x22)

    def test_read_net_sbi_uses_esc_p(self) -> None:
        assert READ_NET.sbi is not None
        tx = READ_NET.sbi.encode(
            CommandContext(protocol=ProtocolKind.SBI),
            ReadWeightRequest(),
        )
        assert tx == b"\x1bP"


class TestEncodeHiresOpcodes:
    def test_read_net_hires_10x(self, ctx: CommandContext) -> None:
        """hires=1 → 0x1F + TLV-21 0x01."""
        assert READ_NET_HIRES.xbpi is not None
        tx = READ_NET_HIRES.xbpi.encode(ctx, ReadWeightHiresRequest(resolution=1))
        assert tx == build_command(0x1F, b"\x21\x01")

    def test_read_net_hires_100x(self, ctx: CommandContext) -> None:
        assert READ_NET_HIRES.xbpi is not None
        tx = READ_NET_HIRES.xbpi.encode(ctx, ReadWeightHiresRequest(resolution=2))
        assert tx == build_command(0x1F, b"\x21\x02")

    def test_read_gross_hires_uses_opcode_0x21(self, ctx: CommandContext) -> None:
        assert READ_GROSS_HIRES.xbpi is not None
        tx = READ_GROSS_HIRES.xbpi.encode(ctx, ReadWeightHiresRequest(resolution=1))
        assert tx == build_command(0x21, b"\x21\x01")


class TestDecodeEmptyPanReading:
    def test_decode_doc_example_measurement(self, ctx: CommandContext) -> None:
        """docs/protocol.md §3.3 empty-pan frame → -0.005 g stable negative."""
        rx = bytes.fromhex("0b4148bba3d70a3d30824507")
        frame = parse_frame(rx)
        assert READ_NET.xbpi is not None
        reading = READ_NET.xbpi.decode(frame, ctx)
        assert reading.value is not None
        assert math.isclose(reading.value, -0.005, abs_tol=1e-6)
        assert reading.unit is Unit.G
        assert reading.sign is Sign.NEGATIVE
        assert reading.stable is True
        assert reading.overload is False
        assert reading.underload is False
        assert reading.protocol is ProtocolKind.XBPI
        assert reading.status_flags["stable"] is True
        assert reading.status_flags["off_scale"] is False
        assert reading.raw == rx

    def test_decode_sbi_print_line(self) -> None:
        assert READ_NET.sbi is not None
        reading = READ_NET.sbi.decode(
            parse_reply(b"+     0.00 g  \r\n"),
            CommandContext(protocol=ProtocolKind.SBI),
        )
        assert reading.value == 0.0
        assert reading.unit is Unit.G
        assert reading.protocol is ProtocolKind.SBI


class TestCapabilityHints:
    def test_hires_commands_hint_capability(self) -> None:
        assert READ_NET_HIRES.capability_hints == Capability.HIRES_WEIGHT
        assert READ_GROSS_HIRES.capability_hints == Capability.HIRES_WEIGHT

    def test_std_commands_no_capability_hint(self) -> None:
        assert READ_NET.capability_hints == Capability(0)


class TestSafetyTier:
    def test_all_weight_reads_are_read_only(self) -> None:
        for cmd in (READ_NET, READ_NET_HIRES, READ_GROSS, READ_GROSS_HIRES, READ_TARE_VALUE):
            assert cmd.safety is SafetyTier.READ_ONLY
