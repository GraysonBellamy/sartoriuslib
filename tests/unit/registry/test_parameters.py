"""Tests for :class:`ParameterSpec` + :data:`PARAMETER_TABLE`."""

from __future__ import annotations

import pytest

from sartoriuslib.registry.modes import (
    AutoZeroMode,
    FilterMode,
    IsoCalMode,
    TareOnPowerOn,
)
from sartoriuslib.registry.parameters import (
    PARAMETER_TABLE,
    get_parameter_spec,
)
from sartoriuslib.registry.units import Unit


class TestParameterTableCoverage:
    def test_all_sure_indices_present(self) -> None:
        """Every [SURE] row in ``docs/protocol.md`` §10.1 has a spec."""
        sure_indices = {1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 15, 16, 32, 33, 36, 40, 44, 64, 65}
        assert set(PARAMETER_TABLE) == sure_indices

    def test_get_parameter_spec_none_for_unmapped(self) -> None:
        assert get_parameter_spec(99) is None
        assert get_parameter_spec(25) is None  # p25 is [LIKELY], not [SURE]

    def test_caveat_rows_do_not_bump_counter(self) -> None:
        """§6.3 caveat: p13 writes don't tick 0xBA — the spec must say so.

        p50 is not modelled yet (no enum); it would join this list when
        promoted. p13 is the canonical test case.
        """
        assert PARAMETER_TABLE[13].bumps_config_counter is False
        # Every other modelled index should bump the counter.
        for index, spec in PARAMETER_TABLE.items():
            if index == 13:
                continue
            assert spec.bumps_config_counter is True, (
                f"p{index} ({spec.name}) unexpectedly marked as not bumping 0xBA"
            )


class TestDecode:
    def test_decodes_to_enum_member(self) -> None:
        spec = PARAMETER_TABLE[1]
        assert spec.decode(2) is FilterMode.STABLE

    def test_decodes_unknown_to_sentinel(self) -> None:
        spec = PARAMETER_TABLE[1]
        assert spec.decode(99) is FilterMode.UNKNOWN

    def test_decodes_display_unit_to_unit(self) -> None:
        """p07 special case: decode routes through DISPLAY_UNIT_CODE_TO_UNIT."""
        spec = PARAMETER_TABLE[7]
        assert spec.decode(2) is Unit.G
        assert spec.decode(24) is Unit.UG
        assert spec.decode(99) is Unit.UNKNOWN


class TestEncode:
    def test_encodes_enum_member(self) -> None:
        spec = PARAMETER_TABLE[1]
        assert spec.encode(FilterMode.STABLE) == 2

    def test_encodes_raw_int(self) -> None:
        spec = PARAMETER_TABLE[1]
        assert spec.encode(3) == 3

    def test_rejects_unknown_sentinel(self) -> None:
        """Writing ``UNKNOWN`` to the balance would be nonsense — refuse."""
        spec = PARAMETER_TABLE[1]
        with pytest.raises(ValueError, match="UNKNOWN"):
            spec.encode(FilterMode.UNKNOWN)

    def test_rejects_out_of_enum_int(self) -> None:
        spec = PARAMETER_TABLE[6]  # AutoZeroMode: only 1, 2 valid
        with pytest.raises(ValueError, match="99"):
            spec.encode(99)

    def test_encodes_unit_via_display_code(self) -> None:
        spec = PARAMETER_TABLE[7]
        assert spec.encode(Unit.G) == 2
        assert spec.encode(Unit.KG) == 3
        assert spec.encode(Unit.UG) == 24

    def test_display_unit_rejects_out_of_range_int(self) -> None:
        spec = PARAMETER_TABLE[7]
        with pytest.raises(ValueError, match="out of range"):
            spec.encode(0)
        with pytest.raises(ValueError, match="out of range"):
            spec.encode(99)

    def test_display_unit_accepts_raw_int(self) -> None:
        spec = PARAMETER_TABLE[7]
        assert spec.encode(2) == 2
        assert spec.encode(24) == 24


class TestSpecMetadata:
    def test_name_matches_accessor(self) -> None:
        """The ``name`` field is what the Balance facade will use as ``get_<name>``."""
        assert PARAMETER_TABLE[1].name == "filter_mode"
        assert PARAMETER_TABLE[6].name == "auto_zero"
        assert PARAMETER_TABLE[7].name == "display_unit"
        assert PARAMETER_TABLE[15].name == "isocal_mode"

    def test_writable_default(self) -> None:
        for spec in PARAMETER_TABLE.values():
            assert spec.writable is True

    def test_enum_for_caveat_rows(self) -> None:
        """p13 maps to the modelled enum."""
        assert PARAMETER_TABLE[13].enum is TareOnPowerOn

    def test_unit_enum_flag(self) -> None:
        assert PARAMETER_TABLE[7].unit_enum is True
        assert PARAMETER_TABLE[6].unit_enum is False

    def test_unit_spec_has_no_enum(self) -> None:
        """p07 is the only spec where ``enum`` is ``None`` — decode/encode
        route through the unit map instead."""
        assert PARAMETER_TABLE[7].enum is None
        for index, spec in PARAMETER_TABLE.items():
            if spec.unit_enum:
                continue
            assert spec.enum is not None, f"p{index} has no enum and is not a unit row"

    def test_isocal_is_mse_only(self) -> None:
        """p15 was only observed on Cubis; WZA has a narrower table."""
        from sartoriuslib.devices.kind import BalanceFamily

        spec = PARAMETER_TABLE[15]
        assert BalanceFamily.CUBIS in spec.families
        assert BalanceFamily.OEM_WEIGH_CELL not in spec.families
        # Sanity: the default (general) spec does include OEM_WEIGH_CELL
        assert BalanceFamily.OEM_WEIGH_CELL in PARAMETER_TABLE[1].families
        _ = AutoZeroMode  # keep the import referenced
        _ = IsoCalMode
