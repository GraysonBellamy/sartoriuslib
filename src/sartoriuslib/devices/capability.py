"""Capability flags, safety tiers, and probe/availability enums.

See design doc §5 (families + capabilities) and §6.1 (gates).
"""

from __future__ import annotations

from enum import Flag, IntEnum, StrEnum, auto


class Capability(Flag):
    """Feature capabilities derived from family defaults + live probing.

    Flag bitmap carries capabilities currently believed ``SUPPORTED``. Full
    tri/quad-state per capability lives in ``DeviceInfo.probe_report``.
    """

    # Protocols the balance itself supports (may be more than the one currently active)
    XBPI_SUPPORT = auto()
    SBI_SUPPORT = auto()
    PROTOCOL_SWITCHING = auto()  # confirmed mode switch available

    # Feature capabilities
    HIRES_WEIGHT = auto()  # xBPI 0x1F — sub-mg
    PARAMETER_TABLE = auto()  # xBPI 0x55 — size varies 70 vs 8
    CONFIG_COUNTER = auto()  # xBPI 0xBA — cache-invalidation signal
    TEMPERATURE_SENSORS = auto()  # xBPI 0x76; count varies 1/2/3 across families
    CAL_RECORD = auto()  # xBPI 0xB9 — last cal metadata
    INTERNAL_CAL = auto()  # 0x28 internal adjust (MSE)
    EXTERNAL_CAL = auto()
    ISOCAL = auto()  # p15, status bit 0x10
    EXTENDED_OPCODES = auto()  # 0xBC module list etc. — Cubis
    APP_MODES = auto()  # count / density / percent — Cubis
    LEVEL_SENSOR = auto()  # p59/p60 — Cubis
    BARGRAPH = auto()  # xBPI 0x2F
    AUTO_OUTPUT = auto()  # SBI autoprint (p36 auto_wo / auto_w)
    RAW_ADC = auto()  # xBPI 0x75 (BCE)


class SafetyTier(IntEnum):
    """Per-command safety tier. See design doc §6.1."""

    READ_ONLY = 0
    """Weight, status, identity, capacity, increment, temperature, parameter reads."""

    STATEFUL = 1
    """Transient state change (tare, zero). No EEPROM write."""

    PERSISTENT = 2
    """Parameter writes, save menu, communication settings. Requires ``confirm=True``."""

    DANGEROUS = 3
    """Baud/SBN change, reset, calibration init, protocol switch. Requires ``confirm=True``."""


class Availability(StrEnum):
    """Derived state the session consults when dispatching a command.

    See design doc §5.1, §6.1.1.
    """

    UNKNOWN = "unknown"
    """Never exercised; priors may exist but no device observation yet."""

    SUPPORTED = "supported"
    """Directly confirmed by a successful call or probe."""

    UNSUPPORTED = "unsupported"
    """Device responded with xBPI ``0x04`` / equivalent SBI refusal. Sticky per session."""

    INAPPLICABLE = "inapplicable"
    """Device responded with xBPI ``0x06``. Retryable; state-dependent."""


class ProbeSource(StrEnum):
    """Where an :class:`Availability` value came from."""

    FAMILY_TABLE = "family_table"
    """Seeded prior from our captures."""

    TARGETED_PROBE = "targeted_probe"
    """Explicit probe during ``identify()`` / discovery."""

    LIVE_CALL = "live_call"
    """Updated by the device's response to a normal command."""

    USER_OVERRIDE = "user_override"
    """Set explicitly by the caller."""


__all__ = ["Availability", "Capability", "ProbeSource", "SafetyTier"]
