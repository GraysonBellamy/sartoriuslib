"""Parameter-table index → typed spec map.

Drives two things at the command layer:

1. **Typed accessors** on :class:`~sartoriuslib.devices.balance.Balance`
   (``get_filter_mode()``, ``set_filter_mode(...)``, etc.). The spec
   carries the enum class so the setter can validate + encode and the
   getter can decode to a typed value.
2. **Cache invalidation** on :class:`~sartoriuslib.devices.session.Session`.
   ``bumps_config_counter`` tells the session whether a write to this
   index will tick ``0xBA``. Indices that don't (``p13``, ``p50``) must
   still invalidate their cached entries on explicit write — the
   §6.3 caveat from design doc.

Only the [SURE] rows from ``docs/protocol.md`` §10.1 are modelled
here. [LIKELY] rows remain reachable via raw
``read_parameter`` / ``write_parameter`` until they get promoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sartoriuslib.devices.kind import BalanceFamily
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
)

if TYPE_CHECKING:
    from enum import IntEnum

    from sartoriuslib.registry.units import Unit

__all__ = [
    "PARAMETER_TABLE",
    "ParameterSpec",
    "get_parameter_spec",
]


#: Families that expose the full 70-index parameter table.
_MSE_BCE: frozenset[BalanceFamily] = frozenset(
    {BalanceFamily.CUBIS, BalanceFamily.BASIC_LAB, BalanceFamily.UNKNOWN},
)

#: Families that expose (a subset of) the parameter table — everyone who
#: advertises :class:`~sartoriuslib.devices.capability.Capability.PARAMETER_TABLE`.
#: WZA's 8-index subset is a proper subset of the MSE+BCE encoding, so we
#: accept it here too; per-index reachability is resolved at runtime via
#: the availability cache, not pre-filtered on the family table.
_ALL_FAMILIES: frozenset[BalanceFamily] = frozenset(
    {
        BalanceFamily.CUBIS,
        BalanceFamily.BASIC_LAB,
        BalanceFamily.OEM_WEIGH_CELL,
        BalanceFamily.UNKNOWN,
    },
)


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Typed description of one well-understood parameter-table index.

    Attributes:
        index: ``0x55`` TLV-21 argument value (1-based p-index).
        name: Human-readable symbol matching the ``get_X`` / ``set_X``
            accessor pair on :class:`~sartoriuslib.devices.balance.Balance`.
        enum: The typed :class:`IntEnum` the wire u8 decodes to.
        writable: ``True`` if the balance accepts ``0x56`` for this
            index. A few indices are read-only; most [SURE] entries
            are writable.
        bumps_config_counter: ``True`` if an accepted write ticks the
            ``0xBA`` config counter. Used by the session cache to
            decide whether a write requires an explicit cache flush
            (``False`` rows do — design §6.3 caveat).
        families: Families known to expose this index. Advisory only —
            the session's runtime availability cache is the source of
            truth.
        unit_enum: Optional :class:`Unit` mapping for indices whose
            value space is a unit enumeration (just ``p07`` today).
            When set, the :attr:`enum` field is unused and the typed
            getter/setter routes through
            :data:`sartoriuslib.registry.units.DISPLAY_UNIT_CODE_TO_UNIT`.
    """

    index: int
    name: str
    enum: type[IntEnum] | None
    writable: bool = True
    bumps_config_counter: bool = True
    families: frozenset[BalanceFamily] = field(default_factory=lambda: _ALL_FAMILIES)
    unit_enum: bool = False

    def decode(self, current: int) -> IntEnum | Unit:
        """Turn a wire u8 into the typed value (enum member or :class:`Unit`)."""
        if self.unit_enum:
            from sartoriuslib.registry.units import (  # noqa: PLC0415
                DISPLAY_UNIT_CODE_TO_UNIT,
                Unit,
            )

            return DISPLAY_UNIT_CODE_TO_UNIT.get(current, Unit.UNKNOWN)
        if self.enum is None:  # pragma: no cover — every non-unit row sets enum
            raise RuntimeError(f"parameter {self.index} has no decoder")
        try:
            return self.enum(current)
        except ValueError:
            return self.enum(0)  # UNKNOWN sentinel — every mode enum sets it

    def encode(self, value: IntEnum | Unit | int) -> int:
        """Turn a typed value back into the wire u8 for ``0x56``.

        Raises :class:`ValueError` if ``value`` is not a member of the
        spec's enum / unit set. Accepts a plain ``int`` as an escape
        hatch for values outside the modelled range (but still runs
        through the enum constructor so mid-table gaps stay rejected).
        """
        if self.unit_enum:
            from sartoriuslib.registry.units import (  # noqa: PLC0415
                Unit,
                unit_to_display_code,
            )

            if isinstance(value, Unit):
                return unit_to_display_code(value)
            # IntEnum or raw int — both coerce to an int code; validate
            # against the 1..24 p07 range.
            code = int(value)
            if code not in range(1, 25):
                raise ValueError(
                    f"display-unit code {code} out of range (1..24)",
                )
            return code
        if self.enum is None:  # pragma: no cover
            raise RuntimeError(f"parameter {self.index} has no encoder")
        member = value if isinstance(value, self.enum) else self.enum(int(value))
        if member.value == 0:
            raise ValueError(
                f"cannot write UNKNOWN (0) to parameter {self.index} ({self.name})",
            )
        return int(member.value)


def _spec(
    index: int,
    name: str,
    enum: type[IntEnum] | None,
    *,
    writable: bool = True,
    bumps_counter: bool = True,
    unit_enum: bool = False,
    families: frozenset[BalanceFamily] = _ALL_FAMILIES,
) -> ParameterSpec:
    return ParameterSpec(
        index=index,
        name=name,
        enum=enum,
        writable=writable,
        bumps_config_counter=bumps_counter,
        families=families,
        unit_enum=unit_enum,
    )


#: Parameter index → :class:`ParameterSpec`. Only [SURE] indices from
#: ``docs/protocol.md`` §10.1 are modelled. Indices not in this map stay
#: reachable via raw ``read_parameter`` / ``write_parameter``.
PARAMETER_TABLE: dict[int, ParameterSpec] = {
    1: _spec(1, "filter_mode", FilterMode),
    2: _spec(2, "app_filter", AppFilter),
    3: _spec(3, "stability_range", StabilityRange),
    4: _spec(4, "stability_delay", StabilityDelay),
    5: _spec(5, "tare_behavior", TareBehavior),
    6: _spec(6, "auto_zero", AutoZeroMode),
    7: _spec(7, "display_unit", enum=None, unit_enum=True),
    8: _spec(8, "display_accuracy", DisplayAccuracyMode),
    9: _spec(9, "cal_button_assignment", CalButtonAssignment, families=_MSE_BCE),
    # p13 and p50 are the two §6.3 caveat rows: writes persist but do
    # NOT bump the ``0xBA`` config counter. The cache handles that by
    # invalidating on explicit write regardless of counter state.
    13: _spec(13, "tare_on_power_on", TareOnPowerOn, bumps_counter=False, families=_MSE_BCE),
    15: _spec(15, "isocal_mode", IsoCalMode, families=_MSE_BCE),
    16: _spec(16, "external_cal_lock", ExternalCalLock, families=_MSE_BCE),
    32: _spec(32, "peripheral_parity", ParityMode, families=_MSE_BCE),
    33: _spec(33, "peripheral_stop_bits", StopBitsMode, families=_MSE_BCE),
    36: _spec(36, "sbi_output_mode", OutputMode, families=_MSE_BCE),
    40: _spec(40, "menu_access", MenuAccessMode),
    44: _spec(44, "calibration_unit", CalibrationUnit, families=_MSE_BCE),
    64: _spec(64, "pc_usb_parity", ParityMode, families=_MSE_BCE),
    65: _spec(65, "pc_usb_stop_bits", StopBitsMode, families=_MSE_BCE),
}


def get_parameter_spec(index: int) -> ParameterSpec | None:
    """Look up the spec for a parameter index, or ``None`` if unmapped."""
    return PARAMETER_TABLE.get(index)
