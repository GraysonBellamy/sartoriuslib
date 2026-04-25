"""Tests for typed parameter-mode enums + ``decode_mode`` helper."""

from __future__ import annotations

from typing import Any

import pytest

from sartoriuslib.registry.modes import (
    AutoZeroMode,
    DisplayAccuracyMode,
    FilterMode,
    IsoCalMode,
    MenuAccessMode,
    OutputMode,
    ParityMode,
    StabilityRange,
    TareBehavior,
    TareOnPowerOn,
    decode_mode,
)


class TestWireValues:
    """Wire u8 values must match ``docs/protocol.md`` §10.1 exactly.

    Every mapped index is [SURE], so the value-to-name bindings here are
    what the balance will accept and return.
    """

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (FilterMode.VERY_STABLE, 1),
            (FilterMode.STABLE, 2),
            (FilterMode.UNSTABLE, 3),
            (FilterMode.VERY_UNSTABLE, 4),
            (AutoZeroMode.ON, 1),
            (AutoZeroMode.OFF, 2),
            (TareBehavior.WITHOUT_STABILITY, 1),
            (TareBehavior.WITH_STABILITY, 2),
            (TareBehavior.AT_STABILITY, 3),
            (StabilityRange.MAX_ACCURACY, 1),
            (StabilityRange.MAX_FAST, 6),
            (DisplayAccuracyMode.DEFAULT, 1),
            (DisplayAccuracyMode.LOW_POWER_ON_OFF, 2),
            (DisplayAccuracyMode.DIV1, 6),
            (DisplayAccuracyMode.MINUS_1_DIGIT, 7),
            (IsoCalMode.OFF, 1),
            (IsoCalMode.NOTE, 2),
            (IsoCalMode.ON, 3),
            (OutputMode.MANUAL_IMMEDIATE, 1),
            (OutputMode.AUTOPRINT_UNFILTERED, 4),
            (OutputMode.AUTOPRINT_STABLE, 5),
            (MenuAccessMode.CAN_EDIT, 1),
            (MenuAccessMode.READ_ONLY, 2),
            (ParityMode.ODD, 3),
            (ParityMode.EVEN, 4),
            (ParityMode.NONE, 5),
            (TareOnPowerOn.ON, 1),
            (TareOnPowerOn.OFF, 2),
        ],
    )
    def test_value(self, member: int, value: int) -> None:
        assert int(member) == value


class TestUnknownSentinel:
    """Every enum carries ``UNKNOWN = 0`` so forward-compat decoding never raises."""

    @pytest.mark.parametrize(
        "enum_cls",
        [
            FilterMode,
            AutoZeroMode,
            TareBehavior,
            StabilityRange,
            DisplayAccuracyMode,
            IsoCalMode,
            OutputMode,
            MenuAccessMode,
            ParityMode,
            TareOnPowerOn,
        ],
    )
    def test_has_unknown_zero(self, enum_cls: Any) -> None:
        assert enum_cls.UNKNOWN.value == 0


class TestDecodeMode:
    def test_known_value(self) -> None:
        assert decode_mode(FilterMode, 2) is FilterMode.STABLE

    def test_unknown_value_collapses(self) -> None:
        """A byte not in the enum decodes to ``UNKNOWN`` instead of raising."""
        assert decode_mode(FilterMode, 99) is FilterMode.UNKNOWN
        assert decode_mode(AutoZeroMode, 5) is AutoZeroMode.UNKNOWN

    def test_reserved_gap_in_sparse_enum(self) -> None:
        """Sparse enums (``DisplayAccuracyMode`` has gaps) collapse to UNKNOWN."""
        # Values 3, 4, 5 are gaps in DisplayAccuracyMode
        assert decode_mode(DisplayAccuracyMode, 3) is DisplayAccuracyMode.UNKNOWN
        assert decode_mode(DisplayAccuracyMode, 5) is DisplayAccuracyMode.UNKNOWN
