"""Tests for the xBPI TLV codec — ``docs/protocol.md`` §5."""

from __future__ import annotations

import pytest

from sartoriuslib.errors import SartoriusFrameError
from sartoriuslib.protocol.xbpi import (
    TLV_TAG_SIZES,
    decode_tlv,
    encode_tlv,
    parse_tlv_sequence,
    tlv_value_as_int,
)

# ---------------------------------------------------------------------------
# Tag size table.
# ---------------------------------------------------------------------------


class TestTagSizes:
    @pytest.mark.parametrize(
        ("tag", "size"),
        [
            (0x11, 1),
            (0x12, 2),
            (0x14, 4),
            (0x21, 1),
            (0x22, 2),
            (0x24, 4),
        ],
    )
    def test_size_table(self, tag: int, size: int) -> None:
        assert TLV_TAG_SIZES[tag] == size


# ---------------------------------------------------------------------------
# encode_tlv — integers auto-encode big-endian.
# ---------------------------------------------------------------------------


class TestEncodeTlv:
    def test_encode_u8_tlv21(self) -> None:
        # docs/protocol.md §5.1: Cubis form `0x0C 21 00` for read_max area 0.
        assert encode_tlv(0x21, 0x00) == b"\x21\x00"
        assert encode_tlv(0x21, 0xFF) == b"\x21\xff"

    def test_encode_u16_tlv22(self) -> None:
        assert encode_tlv(0x22, 0x0102) == b"\x22\x01\x02"

    def test_encode_u32_tlv24(self) -> None:
        assert encode_tlv(0x24, 0x12345678) == b"\x24\x12\x34\x56\x78"

    def test_encode_raw_bytes(self) -> None:
        assert encode_tlv(0x21, b"\x07") == b"\x21\x07"

    def test_reject_unknown_tag(self) -> None:
        with pytest.raises(SartoriusFrameError, match="unknown TLV tag"):
            encode_tlv(0x99, 0x00)

    def test_reject_negative_int(self) -> None:
        with pytest.raises(SartoriusFrameError, match="non-negative"):
            encode_tlv(0x21, -1)

    def test_reject_overflow(self) -> None:
        with pytest.raises(SartoriusFrameError, match="does not fit"):
            encode_tlv(0x21, 256)
        with pytest.raises(SartoriusFrameError, match="does not fit"):
            encode_tlv(0x22, 0x10000)

    def test_reject_wrong_size_bytes(self) -> None:
        with pytest.raises(SartoriusFrameError, match="must be 1 byte"):
            encode_tlv(0x21, b"\x00\x00")
        with pytest.raises(SartoriusFrameError, match="must be 4"):
            encode_tlv(0x24, b"\x00\x00\x00")


# ---------------------------------------------------------------------------
# decode_tlv — single-record walk.
# ---------------------------------------------------------------------------


class TestDecodeTlv:
    def test_tlv21_single(self) -> None:
        tag, value, offset = decode_tlv(b"\x21\x02")
        assert tag == 0x21
        assert value == b"\x02"
        assert offset == 2

    def test_tlv22_single(self) -> None:
        tag, value, offset = decode_tlv(b"\x22\x01\x02")
        assert tag == 0x22
        assert value == b"\x01\x02"
        assert offset == 3

    def test_offset_forwards(self) -> None:
        data = b"\xff\xff\x21\x02"
        tag, value, offset = decode_tlv(data, 2)
        assert tag == 0x21
        assert value == b"\x02"
        assert offset == 4

    def test_reject_unknown_tag(self) -> None:
        with pytest.raises(SartoriusFrameError, match="unknown TLV tag"):
            decode_tlv(b"\x99\x00")

    def test_reject_truncated_value(self) -> None:
        with pytest.raises(SartoriusFrameError, match="truncated"):
            decode_tlv(b"\x24\x01\x02")  # tag 0x24 needs 4 bytes, only 2 present

    def test_reject_past_end(self) -> None:
        with pytest.raises(SartoriusFrameError, match="truncated at tag"):
            decode_tlv(b"\x21\x00", offset=2)


# ---------------------------------------------------------------------------
# parse_tlv_sequence — multi-TLV response bodies.
# ---------------------------------------------------------------------------


class TestParseTlvSequence:
    def test_empty_body(self) -> None:
        assert parse_tlv_sequence(b"") == []

    def test_single_tlv(self) -> None:
        assert parse_tlv_sequence(b"\x21\x05") == [(0x21, b"\x05")]

    def test_parameter_table_index_0(self) -> None:
        """docs/protocol.md §5.2: read parameter table returns two u8 TLVs.

        RX body of `06 41 21 02 21 04 8f` — after stripping len/marker/chk
        the body is `21 02 21 04`. But the subtype byte (0x21) is *also*
        the first TLV tag (§5.3), so the caller prepends the subtype to
        the body before calling parse_tlv_sequence.
        """
        # Simulate §5.3 prepend: subtype_byte + rest_of_body
        body_with_subtype = b"\x21\x02\x21\x04"
        tlvs = parse_tlv_sequence(body_with_subtype)
        assert tlvs == [(0x21, b"\x02"), (0x21, b"\x04")]
        # So parameter idx 0 has (current=0x02, max=0x04) — matches §5.2.

    def test_mixed_tags(self) -> None:
        body = b"\x21\x01\x24\x00\x00\x00\xff"
        assert parse_tlv_sequence(body) == [
            (0x21, b"\x01"),
            (0x24, b"\x00\x00\x00\xff"),
        ]

    def test_truncated_mid_sequence(self) -> None:
        # First TLV ok, second truncated.
        with pytest.raises(SartoriusFrameError):
            parse_tlv_sequence(b"\x21\x01\x22\x03")


# ---------------------------------------------------------------------------
# tlv_value_as_int — big-endian u8/u16/u32 decode.
# ---------------------------------------------------------------------------


class TestTlvValueAsInt:
    def test_u8(self) -> None:
        assert tlv_value_as_int(b"\x05") == 5

    def test_u16(self) -> None:
        assert tlv_value_as_int(b"\x01\x02") == 0x0102

    def test_u32(self) -> None:
        assert tlv_value_as_int(b"\x12\x34\x56\x78") == 0x12345678

    def test_empty(self) -> None:
        assert tlv_value_as_int(b"") == 0
