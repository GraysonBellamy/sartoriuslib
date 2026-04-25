"""``sarto-decode`` — offline xBPI / SBI decode CLI.

The CLI is in-process by design (no hardware, no transport), so these
tests drive :func:`sartoriuslib.cli.decode.main` directly and capture
stdout via ``capsys``. The acceptance hex string from the
implementation plan
(``0b4148bba3d70a3d3082 45 55``) is the headline case — it includes
the protocol-doc §3.3 typo'd checksum, and the CLI must surface the
mismatch *and* still decode the (self-consistent) body.
"""

from __future__ import annotations

import pytest

from sartoriuslib.cli.decode import (
    decode_sbi_line,
    decode_xbpi_bytes,
    main,
)


class TestDecodeXbpi:
    def test_acceptance_hex_with_typo_checksum_still_decodes(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--xbpi", "0b4148bba3d70a3d3082", "45", "55"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "subtype:    0x48" in out
        assert "family=measurement" in out
        # Checksum mismatch is surfaced.
        assert "INVALID" in out
        assert "expected 0x07" in out
        # Body still decodes — the docs §3.3 example is self-consistent
        # at -0.005 g except for the trailing checksum byte.
        assert "value:    -0.005" in out
        assert "unit:     g" in out
        assert "sign:     negative" in out

    def test_correct_checksum_reports_valid(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--xbpi", "0b 41 48 bb a3 d7 0a 3d 30 82 45 07"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "checksum:   0x07 (valid)" in out

    def test_short_data_u8(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # 04 41 21 00 66 — SBN read reply, value 0x00, checksum 0x66.
        rc = main(["--xbpi", "04 41 21 00 66"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "family=short_data" in out
        assert "u8:     0x00 (0)" in out

    def test_error_subtype_decodes_reason(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # 04 41 01 04 4a — error subtype 0x01, body=0x04 (unknown opcode).
        rc = main(["--xbpi", "04 41 01 04 4a"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "family=error" in out
        assert "code:   0x04" in out
        assert "unknown opcode" in out

    def test_too_short_input_reports_clearly(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--xbpi", "01"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "frame error: too short" in out

    def test_length_mismatch_reports_clearly(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # length=0x05 says 5 bytes follow → 6 total, but only 4 supplied.
        rc = main(["--xbpi", "05 41 21 00"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "frame error: length byte says 6 bytes total, got 4" in out

    def test_unexpected_marker_reported(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Marker 0x42 (host-direction) — RX must be 0x41.
        rc = main(["--xbpi", "04 42 21 00 67"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "marker:     0x42 (UNEXPECTED)" in out

    def test_rejects_odd_hex(self) -> None:
        with pytest.raises(SystemExit):
            main(["--xbpi", "abc"])

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(SystemExit):
            main(["--xbpi", "zz"])


class TestDecodeSbi:
    def test_weight_line_decodes_to_reading(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--sbi", "+     0.00 g"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "kind:   weight" in out
        assert "unit:      g" in out
        assert "stable:    True" in out

    def test_unstable_marker_decoded(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--sbi", "?     0.50 g"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "stable:    False" in out

    def test_identity_line_classified(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["--sbi", "WZA8202-N"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "kind:   identity" in out


class TestArgGate:
    def test_either_xbpi_or_sbi_required(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_xbpi_and_sbi_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            main(["--xbpi", "00", "--sbi", "anything"])


class TestDecodeAPI:
    """The module-level helpers are public so callers can reuse them
    from test harnesses and notebooks without going through argparse."""

    def test_decode_xbpi_bytes_returns_text(self) -> None:
        report = decode_xbpi_bytes(bytes.fromhex("0b4148bba3d70a3d308245 07"))
        assert report.endswith("\n")
        assert "decoded measurement" in report

    def test_decode_sbi_line_appends_terminator(self) -> None:
        report = decode_sbi_line("+     0.00 g")
        assert "0d 0a" in report  # CRLF was added before parsing
