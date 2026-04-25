"""Tests for fuzzy alias resolvers."""

from __future__ import annotations

import pytest

from sartoriuslib.errors import SartoriusValidationError, UnknownUnitError
from sartoriuslib.registry.aliases import (
    normalise,
    resolve_auto_zero,
    resolve_display_accuracy,
    resolve_filter_mode,
    resolve_isocal_mode,
    resolve_menu_access,
    resolve_output_mode,
    resolve_tare_behavior,
    resolve_unit,
)
from sartoriuslib.registry.modes import (
    AutoZeroMode,
    DisplayAccuracyMode,
    FilterMode,
    IsoCalMode,
    MenuAccessMode,
    OutputMode,
    TareBehavior,
)
from sartoriuslib.registry.units import Unit


class TestNormalise:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Stable", "stable"),
            ("Very Stable", "very_stable"),
            ("very-stable", "very_stable"),
            ("very.stable", "very_stable"),
            ("  VERY   STABLE  ", "very_stable"),
            ("lb/oz", "lboz"),
            ("/lb", "lb"),  # strips leading separator to empty then "lb"
            ("µg", "µg"),
        ],
    )
    def test_cases(self, raw: str, expected: str) -> None:
        assert normalise(raw) == expected


class TestResolveUnit:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("g", Unit.G),
            ("G", Unit.G),
            ("grams", Unit.G),
            ("kg", Unit.KG),
            ("kilogram", Unit.KG),
            ("mg", Unit.MG),
            ("µg", Unit.UG),
            ("ug", Unit.UG),
            ("microgram", Unit.UG),
            ("N", Unit.NEWTON),
            ("newton", Unit.NEWTON),
            ("Newtons", Unit.NEWTON),
            ("ct", Unit.CT),
            ("carat", Unit.CT),
            ("lb", Unit.LB),
            ("pound", Unit.LB),
            ("tonne", Unit.T),
            ("t", Unit.T),
            ("hk tael", Unit.TAEL_HK),
            ("singapore tael", Unit.TAEL_SG),
        ],
    )
    def test_known(self, raw: str, expected: Unit) -> None:
        assert resolve_unit(raw) is expected

    def test_passthrough_unit(self) -> None:
        assert resolve_unit(Unit.KG) is Unit.KG

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnknownUnitError):
            resolve_unit("wibbles")


class TestResolveFilter:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("stable", FilterMode.STABLE),
            ("Stable", FilterMode.STABLE),
            ("very stable", FilterMode.VERY_STABLE),
            ("very-stable", FilterMode.VERY_STABLE),
            ("vs", FilterMode.VERY_STABLE),
            ("unstable", FilterMode.UNSTABLE),
            ("very unstable", FilterMode.VERY_UNSTABLE),
            ("vu", FilterMode.VERY_UNSTABLE),
        ],
    )
    def test_known(self, raw: str, expected: FilterMode) -> None:
        assert resolve_filter_mode(raw) is expected

    def test_int_passthrough(self) -> None:
        assert resolve_filter_mode(2) is FilterMode.STABLE

    def test_enum_passthrough(self) -> None:
        assert resolve_filter_mode(FilterMode.STABLE) is FilterMode.STABLE

    def test_unknown_string_raises(self) -> None:
        with pytest.raises(SartoriusValidationError):
            resolve_filter_mode("extremely_calm")

    def test_unknown_int_raises(self) -> None:
        with pytest.raises(SartoriusValidationError):
            resolve_filter_mode(99)


class TestResolveOthers:
    def test_auto_zero(self) -> None:
        assert resolve_auto_zero("on") is AutoZeroMode.ON
        assert resolve_auto_zero("enabled") is AutoZeroMode.ON
        assert resolve_auto_zero("off") is AutoZeroMode.OFF
        assert resolve_auto_zero("disabled") is AutoZeroMode.OFF
        assert resolve_auto_zero(True.__index__()) is AutoZeroMode.ON  # 1

    def test_tare_behavior(self) -> None:
        assert resolve_tare_behavior("wo stab") is TareBehavior.WITHOUT_STABILITY
        assert resolve_tare_behavior("w_stab") is TareBehavior.WITH_STABILITY
        assert resolve_tare_behavior("at_stab") is TareBehavior.AT_STABILITY
        assert resolve_tare_behavior("AT STABILITY") is TareBehavior.AT_STABILITY

    def test_display_accuracy(self) -> None:
        assert resolve_display_accuracy("default") is DisplayAccuracyMode.DEFAULT
        assert resolve_display_accuracy("normal") is DisplayAccuracyMode.DEFAULT
        assert resolve_display_accuracy("-1 digit") is DisplayAccuracyMode.MINUS_1_DIGIT
        assert resolve_display_accuracy("minus one digit") is DisplayAccuracyMode.MINUS_1_DIGIT
        assert resolve_display_accuracy("reduced") is DisplayAccuracyMode.MINUS_1_DIGIT

    def test_isocal(self) -> None:
        assert resolve_isocal_mode("on") is IsoCalMode.ON
        assert resolve_isocal_mode("off") is IsoCalMode.OFF
        assert resolve_isocal_mode("note") is IsoCalMode.NOTE

    def test_output_mode(self) -> None:
        assert resolve_output_mode("auto_w") is OutputMode.AUTOPRINT_STABLE
        assert resolve_output_mode("auto wo") is OutputMode.AUTOPRINT_UNFILTERED
        assert resolve_output_mode("autoprint") is OutputMode.AUTOPRINT_STABLE
        assert resolve_output_mode("manual immediate") is OutputMode.MANUAL_IMMEDIATE

    def test_menu_access(self) -> None:
        assert resolve_menu_access("can edit") is MenuAccessMode.CAN_EDIT
        assert resolve_menu_access("editable") is MenuAccessMode.CAN_EDIT
        assert resolve_menu_access("read only") is MenuAccessMode.READ_ONLY
        assert resolve_menu_access("locked") is MenuAccessMode.READ_ONLY
