"""Tests for xBPI unit/sign/decimal byte decoding — ``docs/protocol.md`` §8.4."""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.xbpi import (
    decode_decimals,
    decode_sign,
    decode_unit,
    unit_byte_to_unit,
)
from sartoriuslib.registry.units import Sign, Unit


class TestDecodeDecimals:
    @pytest.mark.parametrize(
        ("byte5", "expected"),
        [
            (0x00, 0),
            (0x10, 1),
            (0x30, 3),  # observed value from docs §3.3 measurement fixture
            (0x70, 7),
            # Low nibble is advisory (WZA mg quirk) — ignored.
            (0x03, 0),
            (0x23, 2),
        ],
    )
    def test_high_nibble(self, byte5: int, expected: int) -> None:
        assert decode_decimals(byte5) == expected


class TestDecodeSign:
    @pytest.mark.parametrize(
        ("byte6", "expected"),
        [
            (0x00, Sign.ZERO),
            (0x02, Sign.ZERO),  # zero-sign + g unit id
            (0x40, Sign.POSITIVE),
            (0x42, Sign.POSITIVE),  # pos + g
            (0x80, Sign.NEGATIVE),
            (0x82, Sign.NEGATIVE),  # neg + g (docs §3.3 fixture)
        ],
    )
    def test_sign_bits(self, byte6: int, expected: Sign) -> None:
        assert decode_sign(byte6) is expected

    def test_both_bits_set_is_unknown(self) -> None:
        """Only one bit of the 2-bit sign field should be set at a time."""
        assert decode_sign(0xC0) is Sign.UNKNOWN


class TestUnitByteToUnit:
    @pytest.mark.parametrize(
        ("unit_id", "expected"),
        [
            (0x02, Unit.G),
            (0x03, Unit.KG),
            (0x0D, Unit.MG),
            (0x17, Unit.NEWTON),
        ],
    )
    def test_known_units(self, unit_id: int, expected: Unit) -> None:
        assert unit_byte_to_unit(unit_id) is expected

    def test_unknown_decodes_to_unknown(self) -> None:
        assert unit_byte_to_unit(0x00) is Unit.UNKNOWN
        assert unit_byte_to_unit(0x05) is Unit.UNKNOWN
        assert unit_byte_to_unit(0x3F) is Unit.UNKNOWN


class TestDecodeUnit:
    def test_strips_sign_bits(self) -> None:
        # negative + g
        assert decode_unit(0x82) is Unit.G
        # positive + kg
        assert decode_unit(0x43) is Unit.KG
        # zero + mg
        assert decode_unit(0x0D) is Unit.MG
