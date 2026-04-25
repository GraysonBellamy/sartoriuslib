"""Typed enums for the well-understood parameter-table indices.

One :class:`IntEnum` per [SURE] row in ``docs/protocol.md`` §10.1.
Values match the wire u8 the balance accepts / returns, so encoding is
``int(mode)`` and decoding is ``Mode(byte)``.

Each enum includes :attr:`UNKNOWN` (value ``0``) as the
forward-compatibility escape hatch — a byte the library does not yet
recognise decodes to ``UNKNOWN`` rather than raising, mirroring the
:class:`Unit` / :class:`Sign` policy in :mod:`sartoriuslib.registry.units`.
``0`` is safe because the p* indices documented here use 1-based
numbering on the wire.
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
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
    "ParityMode",
    "StabilityDelay",
    "StabilityRange",
    "StopBitsMode",
    "TareBehavior",
    "TareOnPowerOn",
    "ZeroRange",
    "decode_mode",
]


class FilterMode(IntEnum):
    """p01 — filter / ambient mode.

    Equivalent to opcode ``0x26`` (``read_weighing_mode``). Fully
    mapped.
    """

    UNKNOWN = 0
    VERY_STABLE = 1
    STABLE = 2
    UNSTABLE = 3
    VERY_UNSTABLE = 4


class AppFilter(IntEnum):
    """p02 — application filter. Orthogonal to :class:`FilterMode`."""

    UNKNOWN = 0
    FINAL_READING = 1
    FILLING = 2
    REDUCED = 3
    OFF = 4


class StabilityRange(IntEnum):
    """p03 — stability detection band width."""

    UNKNOWN = 0
    MAX_ACCURACY = 1
    VERY_ACCURATE = 2
    ACCURATE = 3
    FAST = 4
    VERY_FAST = 5
    MAX_FAST = 6


class StabilityDelay(IntEnum):
    """p04 — stability delay (how long above-threshold must persist)."""

    UNKNOWN = 0
    NONE = 1
    SHORT = 2
    AVERAGE = 3
    LONG = 4


class TareBehavior(IntEnum):
    """p05 — tare-on-stability behaviour."""

    UNKNOWN = 0
    WITHOUT_STABILITY = 1
    WITH_STABILITY = 2
    AT_STABILITY = 3


class AutoZeroMode(IntEnum):
    """p06 — auto-zero tracking on / off."""

    UNKNOWN = 0
    ON = 1
    OFF = 2


class DisplayAccuracyMode(IntEnum):
    """p08 — display-accuracy mode.

    Only the four MSE1203S-live values are named. Gaps in the sparse
    ``max=18`` range belong to other balances within the family and
    decode to :attr:`UNKNOWN` on devices that do not expose them.
    """

    UNKNOWN = 0
    DEFAULT = 1
    """Display at native increment."""

    LOW_POWER_ON_OFF = 2
    """``lponoff`` — power-save display toggle."""

    DIV1 = 6
    """``div1`` — increment divided by 1 (identity with DEFAULT on
    MSE1203S; retained as a distinct menu entry)."""

    MINUS_1_DIGIT = 7
    """``-1 digit`` — drops the ``0x0D`` increment by one decimal (10x)."""


class ZeroRange(IntEnum):
    """p11 — runtime auto-zero range."""

    UNKNOWN = 0
    ONE_PERCENT = 1
    TWO_PERCENT = 2


class IsoCalMode(IntEnum):
    """p15 — persistent isoCAL mode.

    Status-byte bit ``0x10`` is the *attention* flag, not the enable
    flag — see ``docs/protocol.md`` §10.1 p15.
    """

    UNKNOWN = 0
    OFF = 1
    NOTE = 2
    ON = 3


class ExternalCalLock(IntEnum):
    """p16 — external calibration lock."""

    UNKNOWN = 0
    FREE = 1
    LOCKED = 2


class ParityMode(IntEnum):
    """p32 / p64 — UART parity.

    Shared encoding between the peripheral port (p32) and the PC-USB
    port (p64). Gaps 1/2 are reserved (mark/space).
    """

    UNKNOWN = 0
    ODD = 3
    EVEN = 4
    NONE = 5


class StopBitsMode(IntEnum):
    """p33 / p65 — UART stop bits."""

    UNKNOWN = 0
    ONE = 1
    TWO = 2


class OutputMode(IntEnum):
    """p36 — SBI output mode (trigger × stability-filter matrix)."""

    UNKNOWN = 0
    MANUAL_IMMEDIATE = 1
    """``ind_no`` — manual trigger, send current value."""

    MANUAL_AFTER_STABILITY = 2
    """``ind_after`` — manual trigger, send after next stability."""

    MANUAL_AT_STABILITY = 3
    """``ind_at`` — manual trigger, send only when already stable."""

    AUTOPRINT_UNFILTERED = 4
    """``auto_wo`` — autoprint each cycle regardless of stability."""

    AUTOPRINT_STABLE = 5
    """``auto_w`` — autoprint only stable cycles."""


class MenuAccessMode(IntEnum):
    """p40 — front-panel menu access."""

    UNKNOWN = 0
    CAN_EDIT = 1
    READ_ONLY = 2


class CalButtonAssignment(IntEnum):
    """p09 — calibration-button assignment.

    MSE1203S live values only; gaps reserved.
    """

    UNKNOWN = 0
    CAL_EXTERNAL = 1
    E_CAL_USER = 3
    CAL_INTERNAL = 4
    SET_PRELOAD = 8
    DELETE_PRELOAD = 9
    BLOCKED = 10
    SELECT = 12
    SET_EXTERNAL_WEIGHT = 17


class CalibrationUnit(IntEnum):
    """p44 — calibration unit.

    Note: wire encoding differs from opcode ``0x79``'s args.
    """

    UNKNOWN = 0
    G = 1
    KG = 2
    USER_DEFINED = 4


class TareOnPowerOn(IntEnum):
    """p13 — tare on power-on.

    Bit: this is a boot-time flag. Writing it does *not* bump the
    ``0xBA`` config counter — see ``docs/protocol.md`` §10.1 persistence
    note and design doc §6.3 caveat.
    """

    UNKNOWN = 0
    ON = 1
    OFF = 2


def decode_mode[E: IntEnum](enum_cls: type[E], raw: int) -> E:
    """Turn a wire u8 into a member of ``enum_cls``.

    Unrecognised codes collapse to the ``UNKNOWN`` member (value ``0``)
    so new firmware reveals stay non-crashing. Every enum in this
    module is guaranteed to carry an ``UNKNOWN = 0`` member.
    """
    try:
        return enum_cls(raw)
    except ValueError:
        return enum_cls(0)
