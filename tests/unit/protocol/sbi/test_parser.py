"""Tests for :mod:`sartoriuslib.protocol.sbi.parser`."""

from __future__ import annotations

import pytest

from sartoriuslib import ProtocolKind, Sign, Unit
from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.sbi import (
    SbiLineKind,
    parse_line,
    parse_reply,
    parse_weight_line,
    require_identity_text,
    require_reading,
)


class TestWeightParser:
    def test_zero_grams_doc_example(self) -> None:
        reading = parse_weight_line(b"+     0.00 g  \r\n")
        assert reading.value == 0.0
        assert reading.unit is Unit.G
        assert reading.sign is Sign.ZERO
        assert reading.stable is True
        assert reading.decimals == 2
        assert reading.protocol is ProtocolKind.SBI

    def test_negative_milligrams(self) -> None:
        reading = parse_weight_line(b"-   12.345 mg \r\n")
        assert reading.value == -12.345
        assert reading.unit is Unit.MG
        assert reading.sign is Sign.NEGATIVE
        assert reading.decimals == 3

    def test_unstable_marker(self) -> None:
        reading = parse_weight_line(b"?+     1.0 g\r\n")
        assert reading.stable is False
        assert reading.status_flags["stable"] is False

    def test_space_sign_is_positive_or_zero(self) -> None:
        reading = parse_weight_line(b"     123 g  \r\n")
        assert reading.value == 123.0
        assert reading.sign is Sign.POSITIVE
        assert reading.decimals == 0

    def test_22_character_id_prefix(self) -> None:
        reading = parse_weight_line(b"Qnt +    253 pcs  \r\n")
        assert reading.value == 253.0
        assert reading.unit is Unit.UNKNOWN
        assert reading.sign is Sign.POSITIVE
        assert reading.stable is True

    def test_live_autoprint_without_unit_is_unstable(self) -> None:
        reading = parse_weight_line(b"N     +    0.031    \r\n")
        assert reading.value == 0.031
        assert reading.unit is Unit.UNKNOWN
        assert reading.sign is Sign.POSITIVE
        assert reading.stable is False
        assert reading.status_flags["stable"] is False

    def test_live_autoprint_with_unit_is_stable(self) -> None:
        reading = parse_weight_line(b"N     +    0.006 g  \r\n")
        assert reading.value == 0.006
        assert reading.unit is Unit.G
        assert reading.stable is True

    def test_overload_marker(self) -> None:
        reading = parse_weight_line(b"H\r\n")
        assert reading.value is None
        assert reading.overload is True
        assert reading.underload is False

    def test_overload_marker_with_stat_prefix(self) -> None:
        reading = parse_weight_line(b"Stat High\r\n")
        assert reading.value is None
        assert reading.overload is True
        assert reading.underload is False

    def test_underload_marker(self) -> None:
        reading = parse_weight_line(b"L\r\n")
        assert reading.value is None
        assert reading.overload is False
        assert reading.underload is True

    def test_underload_marker_with_stat_prefix(self) -> None:
        reading = parse_weight_line(b"Stat Low\r\n")
        assert reading.value is None
        assert reading.overload is False
        assert reading.underload is True

    def test_bad_weight_line_raises(self) -> None:
        with pytest.raises(SartoriusParseError, match="unparseable"):
            parse_weight_line(b"not a weight\r\n")


class TestReplyParser:
    def test_parse_reply_with_weight(self) -> None:
        reply = parse_reply(b"+     0.00 g  \r\n")
        assert reply.raw == b"+     0.00 g  \r\n"
        assert reply.lines[0].kind is SbiLineKind.WEIGHT
        assert require_reading(reply).value == 0.0

    def test_require_reading_skips_leading_empty_line(self) -> None:
        reply = parse_reply(b"\nN     +    0.031    \r\n")
        reading = require_reading(reply)
        assert reading.value == 0.031
        assert reading.stable is False

    def test_parse_identity_line(self) -> None:
        line = parse_line(b"WZA8202-N\r\n")
        assert line.kind is SbiLineKind.IDENTITY
        reply = parse_reply(b"WZA8202-N\r\n")
        assert require_identity_text(reply) == "WZA8202-N"

    def test_refusal_line(self) -> None:
        line = parse_line(b"ERR\r\n")
        assert line.kind is SbiLineKind.REFUSAL

    @pytest.mark.parametrize(
        "line",
        [
            b"Err 101\r\n",
            b"APP. ERR\r\n",
            b"Stat ERR 101\r\n",
            b"Stat PRT. ERR\r\n",
        ],
    )
    def test_documented_error_lines_are_refusals(self, line: bytes) -> None:
        assert parse_line(line).kind is SbiLineKind.REFUSAL

    def test_special_non_weight_status_is_unknown_not_identity(self) -> None:
        line = parse_line(b"Stat Cal. Ext.\r\n")
        assert line.kind is SbiLineKind.UNKNOWN

    def test_live_internal_cal_status_is_unknown_not_identity(self) -> None:
        line = parse_line(b"Stat     Cal.Int.\r\n")
        assert line.kind is SbiLineKind.UNKNOWN
        assert line.reading is None

    def test_hyphenated_model_string_is_identity_not_weight(self) -> None:
        line = parse_line(b"BCE3202-1S\r\n")
        assert line.kind is SbiLineKind.IDENTITY
        reply = parse_reply(b"BCE3202-1S\r\n")
        assert require_identity_text(reply) == "BCE3202-1S"

    def test_compact_bare_number_is_identity_not_weight_fragment(self) -> None:
        line = parse_line(b"0.090\r\n")
        assert line.kind is SbiLineKind.IDENTITY
        assert line.reading is None

    def test_identity_replies_with_periods_are_not_weight_lines(self) -> None:
        # Regression: MSE1203S BAC 00-39-21 returns SerNo./BAC: identity
        # lines whose prefix contains a period. The parser's _WEIGHT_RE
        # used to permit ``.`` in the prefix character class, causing
        # ``SerNo.    0031801165\r\n`` to be classified WEIGHT and decoded
        # as a Reading of 31801165.0. Mirrors the framing-side test
        # in test_framing.py::test_autoprint_recognizer_rejects_identity_replies.
        for raw in (
            b"SerNo.    0031801165\r\n",
            b"BAC:        00-39-21\r\n",
        ):
            line = parse_line(raw)
            assert line.kind is SbiLineKind.IDENTITY, (raw, line.kind)
            assert line.reading is None, raw

    def test_short_padded_bare_number_is_identity_not_weight_fragment(self) -> None:
        line = parse_line(b"   079\r\n")
        assert line.kind is SbiLineKind.IDENTITY
        assert line.reading is None

    def test_require_reading_rejects_identity(self) -> None:
        reply = parse_reply(b"WZA8202-N\r\n")
        with pytest.raises(SartoriusParseError, match="weight line"):
            require_reading(reply)

    def test_require_identity_rejects_special_status(self) -> None:
        reply = parse_reply(b"Stat Cal. Ext.\r\n")
        with pytest.raises(SartoriusParseError, match="identity text"):
            require_identity_text(reply)

    def test_require_identity_can_reject_weight_like_autoprint(self) -> None:
        reply = parse_reply(b"N     +    0.031    \r\n")
        with pytest.raises(SartoriusParseError, match="identity text"):
            require_identity_text(reply, allow_weight_like=False)
