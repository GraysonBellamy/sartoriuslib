"""Tests for :mod:`sartoriuslib.testing` canned frames + script builder."""

from __future__ import annotations

import pytest

from sartoriuslib.errors import SartoriusValidationError
from sartoriuslib.protocol.xbpi import parse_frame
from sartoriuslib.testing import (
    build_identify_script,
    build_sbi_identify_script,
    canned_frames,
    parse_sbi_fixture,
    parse_xbpi_fixture,
)


class TestCannedFrames:
    def test_rx_sbn_parses(self) -> None:
        frame = parse_frame(canned_frames.RX_SBN_00)
        assert frame.subtype == 0x21
        assert frame.body == b"\x00"

    def test_rx_ack_parses(self) -> None:
        frame = parse_frame(canned_frames.RX_ACK)
        assert frame.subtype == 0x00
        assert frame.body == b""

    def test_rx_net_weight_parses(self) -> None:
        frame = parse_frame(canned_frames.RX_NET_WEIGHT_EMPTY_PAN)
        assert frame.subtype == 0x48
        assert len(frame.body) == 8

    def test_rx_model_mse_decodes_to_expected_string(self) -> None:
        frame = parse_frame(canned_frames.RX_MODEL_MSE)
        assert frame.subtype == 0x54
        text = frame.body.rstrip(b"\x00").decode("ascii")
        assert text == "MSE1203S-100-DR"

    def test_rx_manufacturer_decodes(self) -> None:
        frame = parse_frame(canned_frames.RX_MANUFACTURER_SARTORIUS)
        text = frame.body.rstrip(b"\x00").decode("ascii")
        assert text == "Sartorius"

    def test_rx_status_stable_cubis_parses(self) -> None:
        frame = parse_frame(canned_frames.RX_STATUS_STABLE_CUBIS)
        assert frame.subtype == 0x48
        assert len(frame.body) == 8


class TestBuildIdentifyScript:
    def test_covers_every_identify_tx(self) -> None:
        script = build_identify_script()
        assert canned_frames.TX_READ_MODEL in script
        assert canned_frames.TX_READ_MANUFACTURER in script
        assert canned_frames.TX_READ_SW_VERSION in script
        assert canned_frames.TX_READ_FACTORY_NUMBER in script
        assert canned_frames.TX_READ_SBN in script

    def test_all_scripted_replies_parse(self) -> None:
        for rx in build_identify_script().values():
            # Every scripted reply must be a valid xBPI frame.
            parse_frame(rx)

    def test_custom_model_propagates(self) -> None:
        script = build_identify_script(model="BCE3202-1S")
        rx = script[canned_frames.TX_READ_MODEL]
        frame = parse_frame(rx)
        text = frame.body.rstrip(b"\x00").decode("ascii")
        assert text == "BCE3202-1S"

    def test_custom_sbn_propagates(self) -> None:
        script = build_identify_script(sbn=0x05)
        rx = script[canned_frames.TX_READ_SBN]
        frame = parse_frame(rx)
        assert frame.body == b"\x05"


class TestBuildSbiIdentifyScript:
    def test_covers_known_identity_tokens(self) -> None:
        script = build_sbi_identify_script(model="WZA8202-N", serial="SN", software="1.2")
        assert script[b"\x1bx1_"] == b"WZA8202-N\r\n"
        assert script[b"\x1bx2_"] == b"SN\r\n"
        assert script[b"\x1bx3_"] == b"1.2\r\n"


# ---------------------------------------------------------------------------
# parse_xbpi_fixture — design §8.2 text-fixture parser.
# ---------------------------------------------------------------------------


class TestParseXbpiFixture:
    def test_roundtrip_protocol_doc_example(self) -> None:
        """docs/protocol.md §3.3 read-net-weight worked example."""
        fixture = """
        # xBPI fixture: read net weight
        > 04 01 09 1e 2c
        < 0b 41 48 bb a3 d7 0a 3d 30 82 45 07
        """
        mapping = parse_xbpi_fixture(fixture)
        tx = bytes.fromhex("040109 1e 2c".replace(" ", ""))
        rx = bytes.fromhex("0b4148bba3d70a3d30824507")
        assert mapping == {tx: rx}

    def test_ignores_blank_and_comment_lines(self) -> None:
        fixture = """
        # leading comment
        > 04 01 09 71 7f   # inline comment
        # middle comment

        < 04 41 21 00 66
        """
        mapping = parse_xbpi_fixture(fixture)
        assert len(mapping) == 1

    def test_multiple_reply_lines_concatenate(self) -> None:
        fixture = """
        > 04 01 09 71 7f
        < 04 41
        < 21 00 66
        """
        mapping = parse_xbpi_fixture(fixture)
        tx = bytes.fromhex("040109717f")
        assert mapping[tx] == bytes.fromhex("0441210066")

    def test_tx_without_reply_scripts_empty(self) -> None:
        """Request with no ``<`` line gets an empty-bytes reply placeholder."""
        fixture = "> 04 01 09 71 7f\n"
        mapping = parse_xbpi_fixture(fixture)
        tx = bytes.fromhex("040109717f")
        assert mapping == {tx: b""}

    def test_reply_before_request_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="before any"):
            parse_xbpi_fixture("< 04 41 21 00 66\n")

    def test_odd_hex_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="odd hex length"):
            parse_xbpi_fixture("> 04 01 09 71 7\n")

    def test_bad_hex_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="invalid hex"):
            parse_xbpi_fixture("> 04 01 zz 71 7f\n")

    def test_unknown_marker_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="unrecognised marker"):
            parse_xbpi_fixture("? 04 01 09 71 7f\n")

    def test_output_drives_fake_transport(self) -> None:
        """End-to-end: parser output plugs straight into FakeTransport."""
        from sartoriuslib.testing import FakeTransport

        fixture = """
        > 04 01 09 71 7f
        < 04 41 21 00 66
        """
        script = parse_xbpi_fixture(fixture)
        transport = FakeTransport(script)
        # sanity — transport is usable with our script
        assert len(transport.writes) == 0


class TestParseSbiFixture:
    def test_roundtrip_print_fixture(self) -> None:
        fixture = """
        # SBI fixture: print
        > ESC P
        < +     0.00 g
        """
        mapping = parse_sbi_fixture(fixture)
        assert mapping == {b"\x1bP": b"+     0.00 g\r\n"}

    def test_multiple_reply_lines_concatenate(self) -> None:
        fixture = """
        > ESC x1_
        < WZA8202-N
        < extra
        """
        mapping = parse_sbi_fixture(fixture)
        assert mapping[b"\x1bx1_"] == b"WZA8202-N\r\nextra\r\n"

    def test_reply_before_request_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="before any"):
            parse_sbi_fixture("< +     0.00 g\n")

    def test_invalid_token_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="invalid SBI token"):
            parse_sbi_fixture("> P\n")
