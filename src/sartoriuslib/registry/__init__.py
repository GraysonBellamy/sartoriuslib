"""Registry — typed enums for units, parameter-table indices, modes, aliases.

Inline tables for v1; build-time generation remains an open question
(design doc §16 Q4) if the tables grow.
"""

from __future__ import annotations

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
    AppFilter,
    AutoZeroMode,
    CalButtonAssignment,
    CalibrationUnit,
    DisplayAccuracyMode,
    ExternalCalLock,
    FilterMode,
    IsoCalMode,
    MenuAccessMode,
    OutputMode,
    ParityMode,
    StabilityDelay,
    StabilityRange,
    StopBitsMode,
    TareBehavior,
    TareOnPowerOn,
    ZeroRange,
    decode_mode,
)
from sartoriuslib.registry.parameters import (
    PARAMETER_TABLE,
    ParameterSpec,
    get_parameter_spec,
)
from sartoriuslib.registry.units import (
    DISPLAY_UNIT_CODE_TO_UNIT,
    Sign,
    Unit,
    unit_to_display_code,
)

__all__ = [
    "DISPLAY_UNIT_CODE_TO_UNIT",
    "PARAMETER_TABLE",
    "AppFilter",
    "AutoZeroMode",
    "CalButtonAssignment",
    "CalibrationUnit",
    "DisplayAccuracyMode",
    "ExternalCalLock",
    "FilterMode",
    "IsoCalMode",
    "MenuAccessMode",
    "OutputMode",
    "ParameterSpec",
    "ParityMode",
    "Sign",
    "StabilityDelay",
    "StabilityRange",
    "StopBitsMode",
    "TareBehavior",
    "TareOnPowerOn",
    "Unit",
    "ZeroRange",
    "decode_mode",
    "get_parameter_spec",
    "normalise",
    "resolve_auto_zero",
    "resolve_display_accuracy",
    "resolve_filter_mode",
    "resolve_isocal_mode",
    "resolve_menu_access",
    "resolve_output_mode",
    "resolve_tare_behavior",
    "resolve_unit",
    "unit_to_display_code",
]
