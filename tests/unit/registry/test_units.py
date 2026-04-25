"""Tests for the expanded :class:`Unit` enum + p07 display-unit map."""

from __future__ import annotations

import pytest

from sartoriuslib.registry.units import (
    DISPLAY_UNIT_CODE_TO_UNIT,
    Unit,
    unit_to_display_code,
)


class TestUnitEnum:
    def test_backward_compatible_symbols(self) -> None:
        """Core symbol values stay stable across enum extensions."""
        assert Unit.G.value == "g"
        assert Unit.KG.value == "kg"
        assert Unit.MG.value == "mg"
        assert Unit.NEWTON.value == "N"
        assert Unit.UNKNOWN.value == "unknown"

    def test_member_count_matches_protocol_doc(self) -> None:
        """24 display-unit members + UNKNOWN = 25 total."""
        assert len(Unit) == 25

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (Unit.USERDEF, "userdef"),
            (Unit.CT, "ct"),
            (Unit.LB, "lb"),
            (Unit.OZ, "oz"),
            (Unit.OZT, "ozt"),
            (Unit.TAEL_HK, "tl.hk"),
            (Unit.GR, "gr"),
            (Unit.DWT, "dwt"),
            (Unit.PARTS_PER_POUND, "/lb"),
            (Unit.MOMME, "momme"),
            (Unit.TOLA, "tola"),
            (Unit.BAHT, "baht"),
            (Unit.T, "t"),
            (Unit.LB_OZ, "lb_oz"),
            (Unit.UG, "µg"),
        ],
    )
    def test_new_member_values(self, member: Unit, value: str) -> None:
        assert member.value == value


class TestDisplayUnitCodeMap:
    def test_full_table_coverage(self) -> None:
        """Every code 1..24 is in the table — no holes."""
        assert set(DISPLAY_UNIT_CODE_TO_UNIT) == set(range(1, 25))

    @pytest.mark.parametrize(
        ("code", "unit"),
        [
            (1, Unit.USERDEF),
            (2, Unit.G),
            (3, Unit.KG),
            (4, Unit.CT),
            (13, Unit.MG),
            (23, Unit.NEWTON),
            (24, Unit.UG),
        ],
    )
    def test_known_rows(self, code: int, unit: Unit) -> None:
        """Spot-check the [SURE] rows from ``docs/protocol.md`` §10.1 idx 7."""
        assert DISPLAY_UNIT_CODE_TO_UNIT[code] is unit

    def test_no_duplicate_units(self) -> None:
        """Each Unit appears in the display-unit table at most once."""
        units = list(DISPLAY_UNIT_CODE_TO_UNIT.values())
        assert len(units) == len(set(units))


class TestUnitToDisplayCode:
    @pytest.mark.parametrize(
        ("unit", "code"),
        [
            (Unit.USERDEF, 1),
            (Unit.G, 2),
            (Unit.KG, 3),
            (Unit.MG, 13),
            (Unit.NEWTON, 23),
            (Unit.UG, 24),
        ],
    )
    def test_round_trip(self, unit: Unit, code: int) -> None:
        assert unit_to_display_code(unit) == code
        assert DISPLAY_UNIT_CODE_TO_UNIT[code] is unit

    def test_unknown_rejected(self) -> None:
        """UNKNOWN has no code — writing it to p07 would be a bug."""
        with pytest.raises(ValueError, match="no display-unit code"):
            unit_to_display_code(Unit.UNKNOWN)
