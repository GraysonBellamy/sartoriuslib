"""Tests for :mod:`sartoriuslib.commands.calibration` (``0xB9``/``0x28``)."""

from __future__ import annotations

import math
import struct
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.calibration import (
    INTERNAL_ADJUST,
    INTERNAL_ADJUST_CAL_TYPE,
    LAST_CAL_RECORD,
    InternalAdjustRequest,
    LastCalRecordRequest,
)
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import build_command, checksum, parse_frame

if TYPE_CHECKING:
    from sartoriuslib.devices.models import CalRecord


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


class TestLastCalRecordEncode:
    def test_no_args(self, ctx: CommandContext) -> None:
        """``0xB9`` ignores TLV args — encode sends none."""
        assert LAST_CAL_RECORD.xbpi is not None
        tx = LAST_CAL_RECORD.xbpi.encode(ctx, LastCalRecordRequest())
        assert tx == build_command(0xB9)


class TestLastCalRecordDecode:
    def test_decode_full_record(self, ctx: CommandContext) -> None:
        """17-byte body per ``docs/protocol.md`` §7.12.

        temp = 26.5 °C, signature = the MSE-standard pattern,
        counters = 05 06 07, padding = 00.
        """
        temp_bytes = struct.pack(">f", 26.5)
        signature = bytes.fromhex("010900040206000701")
        counters = bytes([0x05, 0x06, 0x07])
        padding = bytes([0x00])
        body = temp_bytes + signature + counters + padding
        rx = _rx(subtype=0x51, body=body)
        frame = parse_frame(rx)
        assert LAST_CAL_RECORD.xbpi is not None
        record = LAST_CAL_RECORD.xbpi.decode(frame, ctx)
        assert record.temperature_celsius is not None
        assert math.isclose(record.temperature_celsius, 26.5, abs_tol=1e-3)
        assert record.signature == signature
        assert record.counters == counters
        assert record.padding == 0
        assert record.has_metadata is True
        assert record.raw == body

    def test_decode_empty_record(self, ctx: CommandContext) -> None:
        """Post cold-boot: all 17 bytes read as zeros (§7.12 note)."""
        body = bytes(17)
        rx = _rx(subtype=0x51, body=body)
        frame = parse_frame(rx)
        assert LAST_CAL_RECORD.xbpi is not None
        record = LAST_CAL_RECORD.xbpi.decode(frame, ctx)
        # Temperature sentinel (all zeros) decodes to None.
        assert record.temperature_celsius is None
        assert record.has_metadata is False

    def test_decode_temperature_only(self, ctx: CommandContext) -> None:
        """Split persistence: temperature present but metadata cleared."""
        temp_bytes = struct.pack(">f", 20.85)
        body = temp_bytes + bytes(13)
        rx = _rx(subtype=0x51, body=body)
        frame = parse_frame(rx)
        assert LAST_CAL_RECORD.xbpi is not None
        record = LAST_CAL_RECORD.xbpi.decode(frame, ctx)
        assert record.temperature_celsius is not None
        assert math.isclose(record.temperature_celsius, 20.85, abs_tol=1e-3)
        assert record.has_metadata is False

    def test_decode_rejects_wrong_length(self, ctx: CommandContext) -> None:
        rx = _rx(subtype=0x51, body=bytes(16))
        frame = parse_frame(rx)
        assert LAST_CAL_RECORD.xbpi is not None
        with pytest.raises(SartoriusParseError, match="17 bytes"):
            LAST_CAL_RECORD.xbpi.decode(frame, ctx)


def _record(ctx: CommandContext, stamp: bytes, temp: float = 26.9) -> CalRecord:
    body = struct.pack(">f", temp) + stamp + b"\x00"
    assert LAST_CAL_RECORD.xbpi is not None
    return LAST_CAL_RECORD.xbpi.decode(parse_frame(_rx(0x51, body)), ctx)


class TestCalRecordTimestamp:
    """``0xB9`` bytes [4..15] = ``DDMMYYhhmmss``, one digit per byte (§7.12)."""

    @pytest.mark.parametrize(
        ("stamp_hex", "expected"),
        [
            # Bench record found on 2026-11-27 (written the day issue #55 was filed).
            ("020401010206010900050500", datetime(2026, 11, 24, 19, 5, 50)),
            # Two adjusts 3 min 31 s apart on the host clock, 3:31 apart here too.
            ("020701010206010505060409", datetime(2026, 11, 27, 15, 56, 49)),
            ("020701010206010600000200", datetime(2026, 11, 27, 16, 0, 20)),
        ],
    )
    def test_decodes_bench_records(
        self, ctx: CommandContext, stamp_hex: str, expected: datetime
    ) -> None:
        record = _record(ctx, bytes.fromhex(stamp_hex))
        assert record.timestamp == expected
        assert record.timestamp is not None
        assert record.timestamp.tzinfo is None  # balance-local clock, no zone

    def test_old_doc_signature_was_a_date(self, ctx: CommandContext) -> None:
        """The §7.12 "signature" ``01 09 00 04 02 06 00 07 01`` reads as 19.04.26 07:1x."""
        record = _record(ctx, bytes.fromhex("010900040206000701") + bytes([5, 3, 0]))
        assert record.timestamp == datetime(2026, 4, 19, 7, 15, 30)

    def test_empty_record_has_no_timestamp(self, ctx: CommandContext) -> None:
        assert _record(ctx, bytes(12)).timestamp is None

    @pytest.mark.parametrize(
        "stamp_hex",
        [
            "0a0401010206010900050500",  # byte > 9: not a digit string
            "020401030206010900050500",  # month 13
            "030201020206010900050500",  # day 32
            "020401010206020500050500",  # hour 25
        ],
    )
    def test_malformed_stamp_is_none(self, ctx: CommandContext, stamp_hex: str) -> None:
        assert _record(ctx, bytes.fromhex(stamp_hex)).timestamp is None


class TestInternalAdjust:
    def test_encode_default_cal_type(self, ctx: CommandContext) -> None:
        """Default cal_type is ``0x78`` — canonical internal adjust."""
        assert INTERNAL_ADJUST.xbpi is not None
        tx = INTERNAL_ADJUST.xbpi.encode(ctx, InternalAdjustRequest())
        assert tx == build_command(0x28, bytes([0x21, INTERNAL_ADJUST_CAL_TYPE]))
        assert INTERNAL_ADJUST_CAL_TYPE == 0x78

    def test_encode_explicit_cal_type(self, ctx: CommandContext) -> None:
        assert INTERNAL_ADJUST.xbpi is not None
        tx = INTERNAL_ADJUST.xbpi.encode(ctx, InternalAdjustRequest(cal_type=0x70))
        assert tx == build_command(0x28, b"\x21\x70")

    def test_decode_ack(self, ctx: CommandContext) -> None:
        rx = _rx(subtype=0x00, body=b"")
        frame = parse_frame(rx)
        assert INTERNAL_ADJUST.xbpi is not None
        assert INTERNAL_ADJUST.xbpi.decode(frame, ctx) is None


class TestMetadata:
    def test_capability_hints(self) -> None:
        assert LAST_CAL_RECORD.capability_hints == Capability.CAL_RECORD
        assert INTERNAL_ADJUST.capability_hints == Capability.INTERNAL_CAL

    def test_safety_tiers(self) -> None:
        assert LAST_CAL_RECORD.safety is SafetyTier.READ_ONLY
        assert INTERNAL_ADJUST.safety is SafetyTier.DANGEROUS
