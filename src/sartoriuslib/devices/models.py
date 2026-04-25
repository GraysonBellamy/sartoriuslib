"""Public frozen dataclasses returned by the :class:`Balance` facade.

See design doc §7. All types are immutable (``frozen=True, slots=True``) so
they are safe to share, pass across task boundaries, and log.

``Reading`` and ``BalanceStatus`` are protocol-neutral: the xBPI decoder
and the Phase-7 SBI decoder both build the same shape. That is the whole
point of the dual-protocol seam (design §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from sartoriuslib.devices.capability import Availability, Capability, ProbeSource
    from sartoriuslib.devices.kind import BalanceFamily
    from sartoriuslib.firmware import FirmwareVersion
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.registry.units import Sign, Unit
    from sartoriuslib.transport.base import SerialSettings


__all__ = [
    "BalanceState",
    "BalanceStatus",
    "CalRecord",
    "DeviceInfo",
    "ParameterEntry",
    "ProbeOutcome",
    "Quantity",
    "Reading",
    "TemperatureReading",
]


class BalanceState(StrEnum):
    """High-level weighing state derived from the status block."""

    STABLE = "stable"
    UNSTABLE = "unstable"
    OVERLOAD = "overload"
    UNDERLOAD = "underload"
    OFF = "off"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Quantity:
    """Scalar value with its unit. Used for capacity, increment, etc."""

    value: float
    unit: Unit


@dataclass(frozen=True, slots=True)
class Reading:
    """One decoded weight reading.

    ``value`` is ``None`` on the off-scale sentinel; the measurement body
    alone cannot disambiguate overload from underload, so callers that
    need that distinction should invoke :meth:`Balance.status`.

    ``stable`` comes from the universal measurement-frame flag bit
    ``0x40`` (design §7 note) — more portable across MSE/WZA/BCE than
    the family-specific status-block state byte.

    ``status_flags`` carries a bag of protocol-specific signals
    (``"stable"``, ``"off_scale"``, and in long-frame reads
    ``"isocal_due"`` / ``"adc_trusted"``) so power users can inspect
    without reaching for the raw bytes.
    """

    value: float | None
    unit: Unit
    sign: Sign
    stable: bool
    overload: bool
    underload: bool
    decimals: int | None
    sequence: int | None
    status_flags: Mapping[str, bool]
    protocol: ProtocolKind
    received_at: datetime
    monotonic_ns: int
    raw: bytes

    def as_dict(self) -> dict[str, float | int | str | None]:
        """Flatten the reading into a row-shaped dict for tabular sinks.

        Content-only — timing provenance (``received_at``,
        ``monotonic_ns``) lives on the surrounding
        :class:`~sartoriuslib.streaming.sample.Sample` because sample-
        level send/receive boundaries are the authoritative timeline
        (design §10). Booleans render as ``0`` / ``1`` so SQLite picks
        INTEGER affinity and CSV / JSONL round-trip cleanly through
        every stdlib reader.
        """
        return {
            "value": self.value,
            "unit": self.unit.value,
            "sign": self.sign.value,
            "stable": int(self.stable),
            "overload": int(self.overload),
            "underload": int(self.underload),
            "decimals": self.decimals,
            "sequence": self.sequence,
            "protocol": self.protocol.value,
            "raw": self.raw.hex(),
        }

    def __format__(self, format_spec: str) -> str:
        """Delegate format specs to :attr:`value` so ``f"{r:.4f}"`` works.

        The empty spec falls back to :func:`str` (the ``frozen``
        dataclass default) so ``f"{r}"`` still prints the structured
        repr. Off-scale readings (``value`` is ``None``) format as
        ``"None"`` for any non-empty numeric spec rather than raising —
        a stream of mixed valid/None readings is the common case during
        a tare or zero settling window and crashing in a log-line
        f-string would be a surprising failure mode.
        """
        if format_spec == "":
            return str(self)
        if self.value is None:
            return "None"
        return format(self.value, format_spec)


@dataclass(frozen=True, slots=True)
class BalanceStatus:
    """Status-block snapshot from xBPI ``0x30`` (or SBI equivalent).

    ``adc_trusted`` and ``isocal_due`` are MSE-only signals; on WZA/BCE
    they decode to ``None``. ``raw_state`` and ``raw_status`` are the
    untouched wire bytes (as integers for xBPI, strings for SBI where
    applicable) so callers can cross-check against ``docs/protocol.md``
    §8.2 without re-decoding.
    """

    stable: bool | None
    state: BalanceState
    isocal_due: bool | None
    adc_trusted: bool | None
    sequence: int | None
    raw_state: int | str | None
    raw_status: int | str | None
    raw: bytes | str


@dataclass(frozen=True, slots=True)
class TemperatureReading:
    """One sensor's temperature read (xBPI ``0x76``).

    :attr:`celsius` is ``None`` when the sensor index is not installed
    — the balance returns the ``7f ff ff ff`` sentinel in that case
    (``docs/protocol.md`` §9). :attr:`sensor` is the TLV-21 index the
    caller passed; :attr:`raw` is the 5-byte typed-float body.
    """

    sensor: int
    celsius: float | None
    raw: bytes


@dataclass(frozen=True, slots=True)
class ParameterEntry:
    """One parameter-table entry from ``0x55``.

    ``current`` and ``max`` are the two u8 TLVs returned in the reply.
    Callers normally route through the typed ``Balance.get_X()`` /
    ``Balance.set_X()`` accessors which decode ``current`` through the
    :class:`sartoriuslib.registry.parameters.ParameterSpec` table.
    """

    index: int
    current: int
    max: int
    raw: bytes


@dataclass(frozen=True, slots=True)
class CalRecord:
    """Last-calibration snapshot from ``0xB9``.

    Layout per ``docs/protocol.md`` §7.12. The 17-byte RAM buffer is
    cleared on cold boot, so :attr:`temperature_celsius` can be present
    (the kernel maintains it separately) while :attr:`signature` and
    :attr:`counters` are all-zero. Callers that just want "was there a
    cal?" should check :attr:`has_metadata`.
    """

    temperature_celsius: float | None
    signature: bytes
    counters: bytes
    padding: int
    raw: bytes

    @property
    def has_metadata(self) -> bool:
        """``True`` if any metadata byte is non-zero.

        All-zero :attr:`signature` + :attr:`counters` means the balance
        has never recorded a cal in the current RAM buffer (post cold
        boot). :attr:`temperature_celsius` can still be valid in that
        state — see §7.12's three-tier storage note.
        """
        return any(self.signature) or any(self.counters)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One capability's current availability plus provenance.

    See design §5.1: ``Availability`` is the derived state, while
    ``ProbeOutcome`` is the observation record that produced it.
    """

    availability: Availability
    source: ProbeSource
    at: datetime | None
    detail: str | None


def _empty_probe_report() -> dict[Capability, ProbeOutcome]:
    return {}


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity snapshot produced by :meth:`Balance.identify`.

    Populated at :func:`open_device` time when ``identify=True`` and
    cached on the :class:`Balance`. Most fields are ``None`` for
    balances we have not yet RE'd beyond the model-string classifier.

    ``capacity`` and ``increment`` are populated by the metrology
    probe and otherwise default to ``None``. ``capabilities`` is
    seeded from the family discriminator at identify time and
    refined as commands probe the device.

    ``temperature_sensor_indices`` is populated only when a caller
    has explicitly run :meth:`Balance.discover_temperature_sensors`,
    which probes the device at runtime and records exactly which
    indices replied. ``None`` (the default) means "not yet probed" —
    no assumption baked in. Some firmwares expose sparse indices
    (the MSE1203S we tested replies at ``0``, ``1``, ``3`` and the
    ``7f ff ff ff`` sentinel at ``2``), some expose contiguous, some
    expose none at all; the device is the source of truth.
    """

    manufacturer: str | None
    model: str
    serial: str | None
    factory_number: str | bytes | None
    software: str | None
    firmware: FirmwareVersion | None
    family: BalanceFamily
    protocol: ProtocolKind
    capacity: Quantity | None
    increment: Quantity | None
    sbn: int | None
    serial_settings: SerialSettings
    capabilities: Capability
    probe_report: Mapping[Capability, ProbeOutcome] = field(
        default_factory=_empty_probe_report,
    )
    temperature_sensor_indices: tuple[int, ...] | None = None
