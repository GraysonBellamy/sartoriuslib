"""Tests for :mod:`sartoriuslib.commands.metrology`."""

from __future__ import annotations

import math
import struct

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.metrology import (
    READ_CAPACITY,
    READ_INCREMENT,
    READ_TEMPERATURE,
    TEMPERATURE_SENTINEL,
    MetrologyRequest,
    TemperatureRequest,
)
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import build_command, checksum, parse_frame
from sartoriuslib.registry.units import Unit


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


def _typed_float_body(value: float, aux: int = 0x00) -> bytes:
    """5-byte typed_float: float32 BE + 1-byte aux."""
    return struct.pack(">f", value) + bytes([aux])


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


class TestEncodeCapacityIncrement:
    """``0x0C`` / ``0x0D`` must wrap the area arg in TLV-21 on Cubis."""

    def test_read_capacity_default_area(self, ctx: CommandContext) -> None:
        assert READ_CAPACITY.xbpi is not None
        tx = READ_CAPACITY.xbpi.encode(ctx, MetrologyRequest())
        assert tx == build_command(0x0C, b"\x21\x00")

    def test_read_capacity_non_default_area(self, ctx: CommandContext) -> None:
        assert READ_CAPACITY.xbpi is not None
        tx = READ_CAPACITY.xbpi.encode(ctx, MetrologyRequest(area=2))
        assert tx == build_command(0x0C, b"\x21\x02")

    def test_read_increment_opcode_0x0d(self, ctx: CommandContext) -> None:
        assert READ_INCREMENT.xbpi is not None
        tx = READ_INCREMENT.xbpi.encode(ctx, MetrologyRequest())
        assert tx == build_command(0x0D, b"\x21\x00")


class TestDecodeCapacityIncrement:
    def test_capacity_1200_g(self, ctx: CommandContext) -> None:
        """MSE1203S reports 1200.0 g capacity (``docs/protocol.md`` §7.2)."""
        rx = _rx(subtype=0x35, body=_typed_float_body(1200.0))
        frame = parse_frame(rx)
        assert READ_CAPACITY.xbpi is not None
        q = READ_CAPACITY.xbpi.decode(frame, ctx)
        assert math.isclose(q.value, 1200.0, abs_tol=1e-6)
        # Opcode reply carries no unit byte; we report UNKNOWN.
        assert q.unit is Unit.UNKNOWN

    def test_increment_1_mg(self, ctx: CommandContext) -> None:
        rx = _rx(subtype=0x35, body=_typed_float_body(0.001))
        frame = parse_frame(rx)
        assert READ_INCREMENT.xbpi is not None
        q = READ_INCREMENT.xbpi.decode(frame, ctx)
        assert math.isclose(q.value, 0.001, abs_tol=1e-9)
        assert q.unit is Unit.UNKNOWN


class TestTemperature:
    def test_encode_includes_tlv_sensor_index(self, ctx: CommandContext) -> None:
        assert READ_TEMPERATURE.xbpi is not None
        tx = READ_TEMPERATURE.xbpi.encode(ctx, TemperatureRequest(sensor=3))
        assert tx == build_command(0x76, b"\x21\x03")

    def test_decode_real_temperature(self, ctx: CommandContext) -> None:
        """WZA sensor 0 reads ~20.85 °C at room temperature."""
        rx = _rx(subtype=0x35, body=_typed_float_body(20.85))
        frame = parse_frame(rx)
        assert READ_TEMPERATURE.xbpi is not None
        reading = READ_TEMPERATURE.xbpi.decode(frame, ctx)
        assert reading.celsius is not None
        assert math.isclose(reading.celsius, 20.85, abs_tol=1e-3)

    def test_decode_sentinel_for_uninstalled_sensor(self, ctx: CommandContext) -> None:
        """Bytes ``7f ff ff ff`` → ``celsius=None`` (``docs/protocol.md`` §9)."""
        body = TEMPERATURE_SENTINEL + b"\x00"  # sentinel + aux
        rx = _rx(subtype=0x35, body=body)
        frame = parse_frame(rx)
        assert READ_TEMPERATURE.xbpi is not None
        reading = READ_TEMPERATURE.xbpi.decode(frame, ctx)
        assert reading.celsius is None

    def test_sensor_field_placeholder(self, ctx: CommandContext) -> None:
        """Variant decode can't see the request; Balance fills sensor later."""
        rx = _rx(subtype=0x35, body=_typed_float_body(25.0))
        frame = parse_frame(rx)
        assert READ_TEMPERATURE.xbpi is not None
        reading = READ_TEMPERATURE.xbpi.decode(frame, ctx)
        # Variant returns -1; Balance wrapper is expected to dataclasses.replace(sensor=…).
        assert reading.sensor == -1


class TestMetadata:
    def test_capability_hints(self) -> None:
        assert READ_TEMPERATURE.capability_hints == Capability.TEMPERATURE_SENSORS
        # Capacity/increment are on every balance family — no hint.
        assert READ_CAPACITY.capability_hints == Capability(0)
        assert READ_INCREMENT.capability_hints == Capability(0)

    def test_safety_tier(self) -> None:
        assert READ_CAPACITY.safety is SafetyTier.READ_ONLY
        assert READ_INCREMENT.safety is SafetyTier.READ_ONLY
        assert READ_TEMPERATURE.safety is SafetyTier.READ_ONLY
