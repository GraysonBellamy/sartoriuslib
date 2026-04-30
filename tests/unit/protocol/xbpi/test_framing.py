"""Golden tests for the xBPI framing codec.

Every fixture is traceable to ``docs/protocol.md`` §3.3 or to a
real-world capture.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sartoriuslib.errors import SartoriusFrameError
from sartoriuslib.protocol.xbpi import (
    BALANCE_SBN_DEFAULT,
    HOST_SBN_DEFAULT,
    RX_MARKER,
    build_command,
    checksum,
    parse_frame,
)

# ---------------------------------------------------------------------------
# Checksum — trivial sum & 0xFF.
# ---------------------------------------------------------------------------


class TestChecksum:
    def test_empty_is_zero(self) -> None:
        assert checksum(b"") == 0

    def test_small(self) -> None:
        # From docs/protocol.md §3.3: sum(04 01 09 71) = 0x7F.
        assert checksum(b"\x04\x01\x09\x71") == 0x7F

    def test_wraps_at_256(self) -> None:
        assert checksum(b"\xff\xff") == 0xFE
        assert checksum(b"\xff" * 256) == 0x00

    @given(st.binary(min_size=0, max_size=256))
    def test_matches_python_sum(self, data: bytes) -> None:
        assert checksum(data) == sum(data) & 0xFF


# ---------------------------------------------------------------------------
# build_command — host→balance TX frames.
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_read_sbn_matches_protocol_doc(self) -> None:
        """docs/protocol.md §3.3 worked example: read SBN."""
        assert build_command(0x71) == bytes.fromhex("0401097 17f".replace(" ", ""))

    def test_read_net_weight_matches_protocol_doc(self) -> None:
        """docs/protocol.md §3.3 worked example: read net weight."""
        assert build_command(0x1E) == bytes.fromhex("04 01 09 1e 2c".replace(" ", ""))

    def test_with_tlv_arg_matches_protocol_doc(self) -> None:
        """docs/protocol.md §5.1: read max on Cubis requires TLV-21 area arg."""
        # Opcode 0x0C + TLV 21 00. checksum = sum(06 01 09 0c 21 00) & 0xff = 0x3d.
        frame = build_command(0x0C, b"\x21\x00")
        assert frame == bytes.fromhex("06 01 09 0c 21 00 3d".replace(" ", ""))

    def test_with_tlv_arg_read_param_idx0(self) -> None:
        """docs/protocol.md §5.2: read parameter table index 0."""
        frame = build_command(0x55, b"\x21\x00")
        assert frame == bytes.fromhex("06 01 09 55 21 00 86".replace(" ", ""))

    def test_default_sbns(self) -> None:
        assert HOST_SBN_DEFAULT == 0x01
        assert BALANCE_SBN_DEFAULT == 0x09
        frame = build_command(0x71)
        assert frame[1] == HOST_SBN_DEFAULT
        assert frame[2] == BALANCE_SBN_DEFAULT

    def test_custom_sbns(self) -> None:
        frame = build_command(0x71, src_sbn=0x05, dst_sbn=0x0A)
        assert frame[1:3] == b"\x05\x0a"
        # checksum must follow the new SBNs
        assert frame[-1] == checksum(frame[:-1])

    def test_length_field_counts_bytes_after_length(self) -> None:
        frame = build_command(0x1E)
        assert frame[0] == len(frame) - 1

    def test_length_field_with_args(self) -> None:
        frame = build_command(0x76, b"\x21\x00")
        assert frame[0] == len(frame) - 1

    def test_opcode_out_of_range(self) -> None:
        with pytest.raises(SartoriusFrameError):
            build_command(0x100)
        with pytest.raises(SartoriusFrameError):
            build_command(-1)

    def test_src_sbn_out_of_range(self) -> None:
        with pytest.raises(SartoriusFrameError):
            build_command(0x1E, src_sbn=0x100)

    def test_too_long(self) -> None:
        # length byte is a single u8, so args limited to ~252 bytes.
        with pytest.raises(SartoriusFrameError):
            build_command(0x1E, args=b"\x00" * 253)


# ---------------------------------------------------------------------------
# parse_frame — balance→host RX frames.
# ---------------------------------------------------------------------------

# Fixtures from docs/protocol.md §3.3.
_READ_SBN_REPLY = bytes.fromhex("04 41 21 00 66".replace(" ", ""))
_MEASUREMENT_REPLY = bytes.fromhex(
    "0b 41 48 bb a3 d7 0a 3d 30 82 45 07".replace(" ", ""),
)
# docs/protocol.md §11.1 tare ACK.
_TARE_ACK = bytes.fromhex("03 41 00 44".replace(" ", ""))


class TestParseFrame:
    def test_read_sbn_reply(self) -> None:
        frame = parse_frame(_READ_SBN_REPLY)
        assert frame.length == 4
        assert frame.marker == RX_MARKER
        assert frame.subtype == 0x21
        assert frame.body == b"\x00"
        assert frame.checksum == 0x66
        assert frame.raw == _READ_SBN_REPLY

    def test_measurement_reply(self) -> None:
        frame = parse_frame(_MEASUREMENT_REPLY)
        assert frame.length == 0x0B
        assert frame.subtype == 0x48
        assert len(frame.body) == 8
        assert frame.body == b"\xbb\xa3\xd7\x0a\x3d\x30\x82\x45"

    def test_tare_ack(self) -> None:
        frame = parse_frame(_TARE_ACK)
        assert frame.subtype == 0x00
        assert frame.body == b""

    def test_roundtrip_synthetic_rx_frame(self) -> None:
        """Construct an RX frame from scratch and round-trip it through
        parse_frame. build_command emits TX (src/dst SBN) so it can't be
        used directly — we assemble an RX (marker + subtype) frame here."""
        # [len=4] [marker=0x41] [subtype=0x21] [body=0x05] [chk]
        pre = bytes([0x04, 0x41, 0x21, 0x05])
        rx = pre + bytes([checksum(pre)])
        frame = parse_frame(rx)
        assert frame.body == b"\x05"

    def test_frame_too_short(self) -> None:
        with pytest.raises(SartoriusFrameError, match="too short"):
            parse_frame(b"\x03\x41\x00")  # missing chk

    def test_length_mismatch_short(self) -> None:
        """Length byte says more bytes than buffer holds."""
        with pytest.raises(SartoriusFrameError, match="length mismatch"):
            parse_frame(b"\x04\x41\x00\x45")  # claims 4 trailing, only 3 present

    def test_length_mismatch_long(self) -> None:
        """Length byte says fewer bytes than buffer holds."""
        with pytest.raises(SartoriusFrameError, match="length mismatch"):
            parse_frame(_TARE_ACK + b"\x00")  # extra byte

    def test_bad_marker(self) -> None:
        bad = bytearray(_READ_SBN_REPLY)
        bad[1] = 0x42  # not 0x41
        bad[-1] = checksum(bytes(bad[:-1]))
        with pytest.raises(SartoriusFrameError, match="bad marker"):
            parse_frame(bytes(bad))

    def test_bad_checksum(self) -> None:
        bad = bytearray(_READ_SBN_REPLY)
        bad[-1] ^= 0xFF
        with pytest.raises(SartoriusFrameError, match="bad checksum"):
            parse_frame(bytes(bad))

    def test_error_includes_raw_bytes_in_context(self) -> None:
        bad = bytearray(_READ_SBN_REPLY)
        bad[-1] ^= 0xFF
        with pytest.raises(SartoriusFrameError) as ei:
            parse_frame(bytes(bad))
        assert ei.value.context.raw_response == bytes(bad)

    @given(st.binary(min_size=0, max_size=3))
    def test_fuzz_too_short(self, data: bytes) -> None:
        """Any buffer below the minimum frame size must raise, never crash."""
        with pytest.raises(SartoriusFrameError):
            parse_frame(data)


# ---------------------------------------------------------------------------
# Round-trip property: manufactured RX frames always parse.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @given(
        subtype=st.integers(min_value=0, max_value=0xFF),
        body=st.binary(min_size=0, max_size=250),
    )
    def test_manufactured_rx_frames_parse(self, subtype: int, body: bytes) -> None:
        length = 1 + 1 + len(body) + 1  # marker + subtype + body + chk
        pre = bytes([length, RX_MARKER, subtype]) + body
        rx = pre + bytes([checksum(pre)])
        frame = parse_frame(rx)
        assert frame.length == length
        assert frame.marker == RX_MARKER
        assert frame.subtype == subtype
        assert frame.body == body
        assert frame.raw == rx
