"""Tests for xBPI subtype decoders — ``docs/protocol.md`` §8, §7.12."""

from __future__ import annotations

import math
import struct

import pytest

from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.xbpi import (
    OFF_SCALE_SENTINEL,
    STABLE_FLAG,
    decode_error_body,
    decode_long_measurement_body,
    decode_measurement_body,
    decode_status_block_body,
    decode_typed_float_body,
    is_status_block_body,
)
from sartoriuslib.registry.units import Sign, Unit

# ---------------------------------------------------------------------------
# Short measurement — docs/protocol.md §8.1.
# ---------------------------------------------------------------------------


class TestDecodeMeasurementBody:
    def test_empty_pan_negative_drift(self) -> None:
        """docs/protocol.md §3.3 empty-pan measurement body.

        ``bb a3 d7 0a`` = float32 BE ≈ -0.005. ``byte[5]=0x30`` →
        3 decimals. ``byte[6]=0x82`` → negative sign, base unit g.
        ``byte[7]=0x45`` → flags 0x45, stable (0x40 bit set).
        """
        body = bytes.fromhex("bb a3 d7 0a 3d 30 82 45".replace(" ", ""))
        m = decode_measurement_body(body)
        assert m.value is not None
        assert math.isclose(m.value, -0.005, abs_tol=1e-6)
        assert m.aux == 0x3D
        assert m.decimals == 3
        assert m.sign is Sign.NEGATIVE
        assert m.unit is Unit.G
        assert m.stable is True
        assert m.off_scale is False
        assert m.flags == 0x45
        assert m.unit_raw == 0x82
        assert m.raw == body

    def test_positive_stable_reading_in_grams(self) -> None:
        """Synthetic: +199.995 g, 3 decimals, stable."""
        value_bytes = struct.pack(">f", 199.995)
        body = value_bytes + b"\x00" + b"\x30\x42\x40"  # byte5=0x30, byte6=0x42, byte7=0x40
        m = decode_measurement_body(body)
        assert m.value is not None
        assert math.isclose(m.value, 199.995, abs_tol=1e-3)
        assert m.decimals == 3
        assert m.sign is Sign.POSITIVE
        assert m.unit is Unit.G
        assert m.stable is True

    def test_unstable_has_stable_flag_clear(self) -> None:
        body = struct.pack(">f", 0.0) + b"\x00\x30\x42\x00"
        m = decode_measurement_body(body)
        assert m.stable is False
        assert m.flags & STABLE_FLAG == 0

    def test_off_scale_sentinel(self) -> None:
        """Off-scale: bytes[0..4] == 7f ff ff ff ff — value must be None."""
        body = OFF_SCALE_SENTINEL + b"\x30\x82\x00"
        m = decode_measurement_body(body)
        assert m.value is None
        assert m.off_scale is True

    def test_unknown_unit_decodes_to_unknown(self) -> None:
        # byte6 low 6 = 0x05 (not in the table)
        body = struct.pack(">f", 1.0) + b"\x00\x00\x05\x00"
        m = decode_measurement_body(body)
        assert m.unit is Unit.UNKNOWN

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="8 bytes"):
            decode_measurement_body(b"\x00" * 7)
        with pytest.raises(SartoriusParseError, match="8 bytes"):
            decode_measurement_body(b"\x00" * 9)

    def test_kg_unit(self) -> None:
        body = struct.pack(">f", 0.2) + b"\x00\x30\x43\x40"
        m = decode_measurement_body(body)
        assert m.unit is Unit.KG
        assert m.sign is Sign.POSITIVE

    def test_mg_unit(self) -> None:
        body = struct.pack(">f", 5.0) + b"\x00\x00\x4d\x40"
        m = decode_measurement_body(body)
        # byte6 low 6 of 0x4D = 0x0D (mg), top 2 = 0x40 (positive)
        assert m.unit is Unit.MG
        assert m.sign is Sign.POSITIVE


# ---------------------------------------------------------------------------
# Status block — docs/protocol.md §8.2.
# ---------------------------------------------------------------------------


class TestDecodeStatusBlock:
    def test_cubis_stable(self) -> None:
        """MSE stable: state=0x88, status=0x18 (§8.2)."""
        body = bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42])
        s = decode_status_block_body(body)
        assert s.state == 0x88
        assert s.status == 0x18
        assert s.sequence == 0x42
        assert s.stable is True
        assert s.overload is False
        assert s.underload is False
        # Cubis-shape: 0x80 set in state
        assert s.adc_trusted is True  # status & 0x08
        assert s.isocal_due is True  # status & 0x10

    def test_cubis_unstable(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x80, 0x08, 0x10, 0x00, 0x10])
        s = decode_status_block_body(body)
        assert s.stable is False
        assert s.adc_trusted is True
        assert s.isocal_due is False

    def test_cubis_overload(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x82, 0x00, 0x10, 0x00, 0x01])
        s = decode_status_block_body(body)
        assert s.overload is True
        assert s.underload is False

    def test_cubis_underload(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x84, 0x00, 0x10, 0x00, 0x01])
        s = decode_status_block_body(body)
        assert s.underload is True
        assert s.overload is False

    def test_non_cubis_stable(self) -> None:
        """WZA/BCE stable: state=0x08, status=0x20; no Cubis base bit."""
        body = bytes([0x00, 0x00, 0x81, 0x08, 0x20, 0x10, 0x00, 0xAA])
        s = decode_status_block_body(body)
        assert s.stable is True
        # Non-Cubis: MSE-specific signals unavailable.
        assert s.adc_trusted is None
        assert s.isocal_due is None

    def test_non_cubis_unstable(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x00, 0x00, 0x10, 0x00, 0xFF])
        s = decode_status_block_body(body)
        assert s.stable is False
        assert s.adc_trusted is None
        assert s.isocal_due is None

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="8 bytes"):
            decode_status_block_body(b"\x00" * 7)


class TestIsStatusBlockBody:
    def test_status_block_shape(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42])
        assert is_status_block_body(body) is True

    def test_short_measurement_shape(self) -> None:
        body = bytes.fromhex("bb a3 d7 0a 3d 30 82 45".replace(" ", ""))
        assert is_status_block_body(body) is False

    def test_wrong_length_never_matches(self) -> None:
        assert is_status_block_body(b"\x00" * 7) is False


# ---------------------------------------------------------------------------
# Long measurement — docs/protocol.md §8.3.
# ---------------------------------------------------------------------------


class TestDecodeLongMeasurementBody:
    def test_composite(self) -> None:
        short = struct.pack(">f", 199.995) + b"\x00\x30\x42\x40"
        delim = b"\x48"
        status = bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42])
        body = short + delim + status
        long = decode_long_measurement_body(body)
        assert long.measurement.unit is Unit.G
        assert long.measurement.stable is True
        assert long.delimiter == 0x48
        assert long.status.stable is True
        assert long.status.sequence == 0x42

    def test_wrong_delimiter_raises(self) -> None:
        short = b"\x00" * 8
        bad_delim = b"\xff"
        status = bytes([0x00, 0x00, 0x81, 0x08, 0x20, 0x10, 0x00, 0x00])
        body = short + bad_delim + status
        with pytest.raises(SartoriusParseError, match="delimiter"):
            decode_long_measurement_body(body)

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="17 bytes"):
            decode_long_measurement_body(b"\x00" * 16)


# ---------------------------------------------------------------------------
# Typed float — subtype 0x35.
# ---------------------------------------------------------------------------


class TestDecodeTypedFloat:
    def test_temperature_sensor_0(self) -> None:
        """Real WZA8202-N sensor 0: 20.85 °C per docs/protocol.md §9."""
        body = struct.pack(">f", 20.85) + b"\x00"
        tf = decode_typed_float_body(body)
        assert math.isclose(tf.value, 20.85, abs_tol=1e-3)
        assert tf.aux == 0x00

    def test_max_1200_g(self) -> None:
        """MSE1203S capacity: 1200.0 g per docs/protocol.md §7.2."""
        body = struct.pack(">f", 1200.0) + b"\x42"
        tf = decode_typed_float_body(body)
        assert math.isclose(tf.value, 1200.0, abs_tol=1e-3)
        assert tf.aux == 0x42

    def test_sensor_not_installed_sentinel(self) -> None:
        """WZA sensor 1 returns the float32 NaN sentinel 7f ff ff ff."""
        body = b"\x7f\xff\xff\xff\x00"
        tf = decode_typed_float_body(body)
        assert math.isnan(tf.value)

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="5 bytes"):
            decode_typed_float_body(b"\x00" * 4)


# ---------------------------------------------------------------------------
# Error body — subtype 0x01.
# ---------------------------------------------------------------------------


class TestDecodeErrorBody:
    @pytest.mark.parametrize("code", [0x03, 0x04, 0x06, 0x07, 0x10, 0x11])
    def test_documented_codes(self, code: int) -> None:
        err = decode_error_body(bytes([code]))
        assert err.code == code

    def test_unknown_code_preserved(self) -> None:
        """Unknown error codes decode without raising — classification is
        the session layer's job."""
        err = decode_error_body(b"\x99")
        assert err.code == 0x99

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="1 byte"):
            decode_error_body(b"")
        with pytest.raises(SartoriusParseError, match="1 byte"):
            decode_error_body(b"\x00\x00")
