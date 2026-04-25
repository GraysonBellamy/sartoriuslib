"""End-to-end codec tests: parse_frame + subtype dispatch.

These verify the full real-world flow: raw wire bytes → ``XbpiFrame`` →
decoded body. Fixtures come from ``docs/protocol.md`` worked examples
and §7.11 captures.
"""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.xbpi import (
    SubtypeFamily,
    checksum,
    decode_error_body,
    decode_long_measurement_body,
    decode_measurement_body,
    decode_status_block_body,
    decode_typed_float_body,
    is_status_block_body,
    parse_frame,
    subtype_family,
)
from sartoriuslib.registry.units import Sign, Unit


def _make_rx(subtype: int, body: bytes) -> bytes:
    """Build a well-formed balance→host frame for testing."""
    length = 1 + 1 + len(body) + 1  # marker + subtype + body + chk
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


class TestParseThenDecode:
    def test_read_sbn_reply_end_to_end(self) -> None:
        """Frame → XbpiFrame → short_data TLV body."""
        rx = bytes.fromhex("04 41 21 00 66".replace(" ", ""))
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.SHORT_DATA
        assert frame.body == b"\x00"  # SBN = 0x00 per protocol.md §2.2

    def test_read_net_weight_end_to_end(self) -> None:
        """Measurement reply: frame → XbpiFrame → MeasurementBody."""
        body = bytes.fromhex("bb a3 d7 0a 3d 30 82 45".replace(" ", ""))
        rx = _make_rx(0x48, body)
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.MEASUREMENT
        assert is_status_block_body(frame.body) is False
        m = decode_measurement_body(frame.body)
        assert m.unit is Unit.G
        assert m.sign is Sign.NEGATIVE
        assert m.stable is True

    def test_status_block_end_to_end(self) -> None:
        body = bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42])
        rx = _make_rx(0x48, body)
        frame = parse_frame(rx)
        assert is_status_block_body(frame.body) is True
        s = decode_status_block_body(frame.body)
        assert s.stable is True
        assert s.sequence == 0x42

    def test_tare_ack_end_to_end(self) -> None:
        """docs/protocol.md §11.1 — tare returns ACK (subtype 0x00, no body)."""
        rx = bytes.fromhex("03 41 00 44".replace(" ", ""))
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.ACK
        assert frame.body == b""

    def test_long_measurement_end_to_end(self) -> None:
        """17-byte streaming measurement from 0x1E 09 30 (§8.3)."""
        import struct

        short = struct.pack(">f", 199.995) + b"\x00\x30\x42\x40"
        status = bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x55])
        body = short + b"\x48" + status
        rx = _make_rx(0x48, body)
        frame = parse_frame(rx)
        assert frame.length == 0x14
        long = decode_long_measurement_body(frame.body)
        assert long.measurement.stable is True
        assert long.status.sequence == 0x55

    def test_typed_float_end_to_end(self) -> None:
        """Typed-float body from a temperature read."""
        import struct

        body = struct.pack(">f", 25.5) + b"\x00"
        rx = _make_rx(0x35, body)
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.TYPED_FLOAT
        tf = decode_typed_float_body(frame.body)
        assert abs(tf.value - 25.5) < 1e-3

    @pytest.mark.parametrize("code", [0x03, 0x04, 0x06, 0x07, 0x10])
    def test_error_subtypes_end_to_end(self, code: int) -> None:
        """Every documented error code maps through cleanly."""
        rx = _make_rx(0x01, bytes([code]))
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.ERROR
        err = decode_error_body(frame.body)
        assert err.code == code

    def test_string_blob_subtype_0x54(self) -> None:
        """Opcode 0x02 returns subtype 0x54 with 20-byte ASCII model string."""
        model = b"MSE1203S-100-DR"
        body = model + b"\x00" * (20 - len(model))
        rx = _make_rx(0x54, body)
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.LONG_DATA
        assert frame.body == body
        # Decoding to ASCII is a command-layer concern; the codec just
        # delivers raw bytes.
        assert frame.body.rstrip(b"\x00").decode("ascii") == "MSE1203S-100-DR"

    def test_string_blob_subtype_0x50(self) -> None:
        """Opcode 0x07 (manufacturer) returns subtype 0x50 with 16-byte ASCII."""
        manuf = b"Sartorius"
        body = manuf + b"\x00" * (16 - len(manuf))
        rx = _make_rx(0x50, body)
        frame = parse_frame(rx)
        assert subtype_family(frame.subtype) is SubtypeFamily.LONG_DATA
        assert frame.body == body
        assert frame.body.rstrip(b"\x00").decode("ascii") == "Sartorius"
