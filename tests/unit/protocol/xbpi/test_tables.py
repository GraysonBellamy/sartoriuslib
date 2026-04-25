"""Tests for the xBPI subtype / opcode / error-code tables."""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.xbpi import (
    ERROR_CODE_REASONS,
    OPCODE_NAMES,
    SubtypeFamily,
    body_length_for_subtype,
    subtype_family,
)


class TestErrorCodeReasons:
    @pytest.mark.parametrize(
        ("code", "substring"),
        [
            (0x03, "out of range"),
            (0x04, "unknown opcode"),
            (0x06, "not applicable"),
            (0x07, "invalid or missing"),
            (0x10, "index"),
            (0x11, "unknown"),
        ],
    )
    def test_documented_codes(self, code: int, substring: str) -> None:
        assert substring in ERROR_CODE_REASONS[code]

    def test_unknown_code_absent(self) -> None:
        assert 0x00 not in ERROR_CODE_REASONS
        assert 0xFF not in ERROR_CODE_REASONS


class TestSubtypeFamily:
    @pytest.mark.parametrize(
        ("subtype", "family"),
        [
            (0x00, SubtypeFamily.ACK),
            (0x01, SubtypeFamily.ERROR),
            (0x12, SubtypeFamily.BARGRAPH),
            (0x14, SubtypeFamily.STRUCTURED_U32),
            (0x21, SubtypeFamily.SHORT_DATA),
            (0x22, SubtypeFamily.SHORT_DATA),
            (0x24, SubtypeFamily.SHORT_DATA),
            (0x34, SubtypeFamily.TYPED_FLOAT_ALT),
            (0x35, SubtypeFamily.TYPED_FLOAT),
            (0x41, SubtypeFamily.SHORT_BLOB),
            (0x43, SubtypeFamily.LONG_DATA),
            (0x45, SubtypeFamily.LONG_DATA),
            (0x48, SubtypeFamily.MEASUREMENT),
            (0x4A, SubtypeFamily.LONG_DATA),
            (0x50, SubtypeFamily.LONG_DATA),
            (0x51, SubtypeFamily.LONG_DATA),
            (0x54, SubtypeFamily.LONG_DATA),
        ],
    )
    def test_tabulated_subtypes(self, subtype: int, family: SubtypeFamily) -> None:
        assert subtype_family(subtype) is family

    def test_high_nibble_fallback_short_data(self) -> None:
        # 0x23 is not tabulated but high nibble 0x20 → short_data family.
        assert subtype_family(0x23) is SubtypeFamily.SHORT_DATA

    def test_high_nibble_fallback_long_data(self) -> None:
        assert subtype_family(0x52) is SubtypeFamily.LONG_DATA
        assert subtype_family(0x5F) is SubtypeFamily.LONG_DATA

    def test_completely_unknown(self) -> None:
        assert subtype_family(0xFE) is SubtypeFamily.UNKNOWN


class TestBodyLengthForSubtype:
    def test_measurement_is_variable(self) -> None:
        assert body_length_for_subtype(0x48) is None

    def test_ack_is_variable(self) -> None:
        # 0x00 usually empty, but 0xBC rides it with a variable body.
        assert body_length_for_subtype(0x00) is None

    @pytest.mark.parametrize(
        ("subtype", "expected"),
        [
            (0x01, 1),  # error, 1-byte body
            (0x21, 1),
            (0x22, 2),
            (0x24, 4),
            (0x34, 4),
            (0x35, 5),
            (0x43, 3),
            (0x45, 5),
            (0x4A, 10),  # 0x4A = 0x40 + 0x0A
            (0x50, 16),
            (0x51, 17),
            (0x54, 20),
        ],
    )
    def test_known_lengths(self, subtype: int, expected: int) -> None:
        assert body_length_for_subtype(subtype) == expected


class TestOpcodeNames:
    @pytest.mark.parametrize(
        ("op", "name"),
        [
            (0x02, "read_weigh_cell_model"),
            (0x14, "tare"),
            (0x18, "zero"),
            (0x1E, "read_net_weight"),
            (0x30, "read_balance_status_block"),
            (0x32, "read_balance_status"),
            (0x55, "read_parameter_table"),
            (0xB9, "read_last_cal_record"),
            (0xBA, "config_generation_counter"),
        ],
    )
    def test_named_opcodes(self, op: int, name: str) -> None:
        assert OPCODE_NAMES[op] == name

    def test_missing_opcode(self) -> None:
        # 0x04 is documented but intentionally left out of the public name
        # map (side-effecting write_user_id — users should use a typed
        # command, not the raw opcode).
        assert OPCODE_NAMES.get(0xEE) is None
