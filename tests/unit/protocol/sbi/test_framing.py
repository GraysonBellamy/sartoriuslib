"""Tests for :mod:`sartoriuslib.protocol.sbi.framing`."""

from __future__ import annotations

import pytest

from sartoriuslib.errors import SartoriusFrameError, SartoriusValidationError
from sartoriuslib.protocol.sbi import (
    LINE_TERMINATOR,
    build_command,
    is_autoprint_line,
    normalize_token,
    split_lines,
    strip_line_terminator,
)


class TestNormalizeToken:
    def test_readable_esc_form(self) -> None:
        assert normalize_token("ESC P") == b"\x1bP"

    def test_extended_token(self) -> None:
        assert normalize_token("ESC x1_") == b"\x1bx1_"

    def test_literal_escape_string(self) -> None:
        assert normalize_token("\x1bP\r\n") == b"\x1bP"

    def test_bytes(self) -> None:
        assert normalize_token(b"\x1bT\r\n") == b"\x1bT"

    def test_missing_escape_raises(self) -> None:
        with pytest.raises(SartoriusValidationError, match="start with ESC"):
            normalize_token("P")


class TestBuildCommand:
    def test_default_has_no_terminator(self) -> None:
        assert build_command("ESC P") == b"\x1bP"

    def test_optional_terminator(self) -> None:
        assert build_command("ESC P", terminator=LINE_TERMINATOR) == b"\x1bP\r\n"


class TestLines:
    def test_strip_crlf(self) -> None:
        assert strip_line_terminator(b"+     0.00 g  \r\n") == b"+     0.00 g  "

    def test_split_lines_keeps_terminators(self) -> None:
        assert split_lines(b"one\r\ntwo\r\n") == (b"one\r\n", b"two\r\n")

    def test_split_rejects_unterminated_tail(self) -> None:
        with pytest.raises(SartoriusFrameError, match="terminator"):
            split_lines(b"one\r\ntwo")

    def test_autoprint_recognizer(self) -> None:
        assert is_autoprint_line(b"+     0.00 g  \r\n")
        assert is_autoprint_line(b"     123 g  \r\n")
        assert is_autoprint_line(b"Qnt +    253 pcs  \r\n")
        assert is_autoprint_line(b"N     +    0.031    \r\n")
        assert is_autoprint_line(b"       0.123    \r\n")
        assert is_autoprint_line(b"Stat High\r\n")
        assert is_autoprint_line(b"Stat     Cal.Int.\r\n")
        assert not is_autoprint_line(b"0.090\r\n")
        assert not is_autoprint_line(b"   079\r\n")
        assert not is_autoprint_line(b"WZA8202-N\r\n")
        assert not is_autoprint_line(b"BCE3202-1S\r\n")

    def test_autoprint_recognizer_rejects_identity_replies(self) -> None:
        # MSE1203S BAC 00-39-21 returns these in response to the
        # Format-2 identity tokens. Verified on real hardware
        # 2026-04-25; they appear interleaved in autoprint streams
        # and must not be misclassified as weight lines (regression
        # test for the SerNo. case where the prefix `[A-Za-z0-9 .]`
        # character class accepted the period and matched the line).
        assert not is_autoprint_line(b"MSE1203S-100-DR     \r\n")
        assert not is_autoprint_line(b"SerNo.    0031801165\r\n")
        assert not is_autoprint_line(b"BAC:        00-39-21\r\n")
