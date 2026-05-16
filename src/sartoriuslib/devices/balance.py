"""The :class:`Balance` facade.

One protocol-neutral class — design §5 ("no family subclasses"). The
balance dispatches every call through :meth:`Session.execute` so all
gates run before any byte leaves the host, and runtime behaviour stays
identical whether the device is on xBPI or SBI.

Surfaces:

Weight & state:
    :meth:`poll`, :meth:`read_net`, :meth:`read_gross`,
    :meth:`read_tare_value`, :meth:`tare`, :meth:`zero`,
    :meth:`status`, :meth:`identify`, :meth:`raw_xbpi`.

Metrology, parameters, cal, persistence:
    :meth:`capacity`, :meth:`increment`, :meth:`temperature`,
    :meth:`read_parameter`, :meth:`write_parameter`,
    :meth:`save_menu`, :meth:`reload_menu`,
    :meth:`last_cal_record`, :meth:`internal_adjust`.

Typed parameter pairs:
    :meth:`get_filter_mode` / :meth:`set_filter_mode`,
    :meth:`get_display_unit` / :meth:`set_display_unit`,
    :meth:`get_auto_zero` / :meth:`set_auto_zero`,
    :meth:`get_isocal_mode` / :meth:`set_isocal_mode`,
    :meth:`get_tare_behavior` / :meth:`set_tare_behavior`,
    :meth:`get_menu_access` / :meth:`set_menu_access`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Self, cast

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.calibration import (
    INTERNAL_ADJUST,
    LAST_CAL_RECORD,
    InternalAdjustRequest,
    LastCalRecordRequest,
)
from sartoriuslib.commands.identity import (
    READ_FACTORY_NUMBER,
    READ_MANUFACTURER,
    READ_MODEL,
    READ_SBN,
    READ_SW_VERSION,
    IdentityRequest,
)
from sartoriuslib.commands.metrology import (
    READ_CAPACITY,
    READ_INCREMENT,
    READ_TEMPERATURE,
    MetrologyRequest,
    TemperatureRequest,
)
from sartoriuslib.commands.parameters import (
    READ_PARAMETER,
    WRITE_PARAMETER,
    ReadParameterRequest,
    WriteParameterRequest,
)
from sartoriuslib.commands.status import STATUS_BLOCK, StatusRequest
from sartoriuslib.commands.system import (
    CONFIG_COUNTER,
    RELOAD_MENU,
    SAVE_MENU,
    SystemRequest,
)
from sartoriuslib.commands.tare import TARE, ZERO, TareRequest
from sartoriuslib.commands.weight import (
    READ_GROSS,
    READ_GROSS_HIRES,
    READ_NET,
    READ_NET_HIRES,
    READ_TARE_VALUE,
    ReadWeightHiresRequest,
    ReadWeightRequest,
)
from sartoriuslib.devices.capability import Capability
from sartoriuslib.devices.kind import BalanceFamily, classify_family
from sartoriuslib.devices.models import (
    BalanceStatus,
    CalRecord,
    DeviceInfo,
    ParameterEntry,
    Quantity,
    Reading,
    TemperatureReading,
)
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusCapabilityWarning,
    SartoriusConfirmationRequiredError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusValidationError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi.client import SbiProtocolClient
from sartoriuslib.protocol.xbpi import encode_tlv
from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient
from sartoriuslib.registry.aliases import (
    resolve_auto_zero,
    resolve_filter_mode,
    resolve_isocal_mode,
    resolve_menu_access,
    resolve_tare_behavior,
    resolve_unit,
)
from sartoriuslib.registry.parameters import PARAMETER_TABLE, get_parameter_spec
from sartoriuslib.registry.units import Unit

if TYPE_CHECKING:
    from types import TracebackType

    from sartoriuslib.devices.session import Session
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame
    from sartoriuslib.registry.modes import (
        AutoZeroMode,
        FilterMode,
        IsoCalMode,
        MenuAccessMode,
        TareBehavior,
    )
    from sartoriuslib.streaming.stream_session import StreamingSession, StreamMode
    from sartoriuslib.transport.base import Parity, SerialSettings, StopBits, Transport


__all__ = ["Balance", "DeviceSnapshot", "SartoriusDeviceSnapshot"]


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Cross-library identity + health snapshot.

    Built from cached state — :meth:`Balance.snapshot` never performs
    I/O. Sibling libraries (alicat, watlow, nidaq) expose the same
    base shape per unified spec §H so multi-adapter consumers can
    render every device's snapshot uniformly.

    Attributes:
        name: Device identifier (manager-style name; model fallback
            when the balance is not under a manager).
        model: Cached model string, or ``None`` if identify has not run.
        firmware: Cached firmware version string, or ``None``.
        serial: Cached serial / factory-number string, or ``None``.
        connected: Whether the underlying session is operational.
        last_error: Last error context the session attached to a
            failure, or ``None`` when no failure has been observed.
        recoverable_error_count: How many transient errors the session
            has retried through transparently since open.
        captured_at: Wall-clock instant the snapshot was taken (UTC,
            tz-aware).
    """

    name: str
    model: str | None
    firmware: str | None
    serial: str | None
    connected: bool
    last_error: ErrorContext | None
    recoverable_error_count: int
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class SartoriusDeviceSnapshot(DeviceSnapshot):
    """Sartorius-typed snapshot extras.

    Adds the family classification, capability bitmap, active protocol,
    and last-observed mode (``None`` if mode has never been observed
    on this session — :meth:`Balance.snapshot` does not probe to find
    out, by design).

    Attributes:
        family: Cached :class:`BalanceFamily` classification.
        capabilities: Bitmap of capabilities the session believes the
            balance has.
        protocol: Active wire protocol on the underlying session.
        mode: Last-observed application mode if the session has tracked
            one. ``None`` until something explicitly sets it; reserved
            for future mode-aware streaming.
    """

    family: BalanceFamily | None
    capabilities: Capability
    protocol: ProtocolKind
    mode: str | None


#: Family-defaults capability **priors** (design §5.1).
#:
#: These are advisory only — the session uses them in :meth:`_gate_priors`
#: to decide whether to emit a one-shot warning when a command's
#: ``capability_hints`` don't intersect what the family is expected to
#: have. Mismatches do **not** refuse pre-I/O in non-strict mode; the
#: device's actual response is the source of truth, not these tables.
#:
#: Capabilities that gate **dispatch behaviour** (e.g.
#: :attr:`Capability.CONFIG_COUNTER`, which controls whether
#: :meth:`Session.cached_execute` re-reads ``0xBA``) are deliberately
#: **not** pre-seeded here — they would otherwise act as hard contracts
#: on a per-family table we can't trust. Those capabilities get added to
#: a session's bitmap by runtime probes in
#: :meth:`Balance._probe_dispatch_capabilities`, so a Cubis variant that
#: actually lacks ``0xBA`` does not cause the cache layer to error on
#: every metrology read.
_FAMILY_DEFAULT_CAPABILITIES: dict[BalanceFamily, Capability] = {
    BalanceFamily.CUBIS: (
        Capability.XBPI_SUPPORT
        | Capability.HIRES_WEIGHT
        | Capability.PARAMETER_TABLE
        | Capability.TEMPERATURE_SENSORS
        | Capability.CAL_RECORD
        | Capability.INTERNAL_CAL
        | Capability.ISOCAL
        | Capability.EXTENDED_OPCODES
        | Capability.APP_MODES
        | Capability.BARGRAPH
    ),
    BalanceFamily.OEM_WEIGH_CELL: Capability.XBPI_SUPPORT | Capability.SBI_SUPPORT,
    BalanceFamily.BASIC_LAB: (Capability.XBPI_SUPPORT | Capability.PARAMETER_TABLE),
    BalanceFamily.UNKNOWN: Capability.XBPI_SUPPORT,
}


#: Default upper bound for :meth:`Balance.discover_temperature_sensors`.
#:
#: Cubis MSE captures show sensors stop at index 3 (index 4 returns
#: xBPI ``0x04``). 8 is a comfortable headroom for firmware revisions
#: we have not yet seen — runtime probing terminates on the device's
#: own ``0x04`` response, so a higher cap costs nothing on units with
#: fewer sensors. Callers who want a tighter or looser scan pass an
#: explicit ``max_index``.
_TEMPERATURE_DISCOVERY_MAX_INDEX: int = 8


#: Parameter-table indices with a typed accessor pair on this facade.
#: Keys mirror the ``ParameterSpec.name`` in
#: :mod:`sartoriuslib.registry.parameters`.
_TYPED_ACCESSOR_INDICES: dict[str, int] = {
    "filter_mode": 1,
    "display_unit": 7,
    "auto_zero": 6,
    "isocal_mode": 15,
    "tare_behavior": 5,
    "menu_access": 40,
}


def _cache_key_parameter(index: int) -> str:
    """Shared cache-key format for parameter-table reads."""
    return f"parameter:{index}"


_MAX_U8: Final[int] = 0xFF


class Balance:
    """Protocol-neutral balance facade.

    Construct via :func:`sartoriuslib.open_device`. Every method is a
    thin wrapper around :meth:`Session.execute` (or
    :meth:`Session.cached_execute` for repeat-read metrology /
    parameter accessors); the session owns the I/O lock and runs the
    pre-I/O safety / protocol / availability / prior gates
    (design §6.1).
    """

    def __init__(
        self,
        session: Session,
        info: DeviceInfo | None = None,
    ) -> None:
        self._session = session
        self._info = info

    # ------------------------------------------------------------------ props

    @property
    def session(self) -> Session:
        """Underlying :class:`Session` (for advanced use / gate inspection)."""
        return self._session

    @property
    def info(self) -> DeviceInfo | None:
        """Identity snapshot from the last :meth:`identify` call (or ``None``).

        Populated automatically by :func:`open_device` when
        ``identify=True``.
        """
        return self._info

    # ------------------------------------------------------------------ async-CM

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self._session.close()

    async def close(self) -> None:
        """Close the underlying transport. Idempotent."""
        await self._session.close()

    async def snapshot(self) -> SartoriusDeviceSnapshot:
        """Return a cached identity + health snapshot — **no I/O**.

        Builds the snapshot from :attr:`info` (the cached
        :class:`DeviceInfo`) and the underlying :class:`Session`
        counters. Safe to call any time, including from a hot path —
        the cost is the dataclass construction.

        ``family``, ``capabilities``, ``protocol`` are sourced from the
        session (so they reflect the live identity even when ``info``
        is ``None``). ``mode`` is reserved for a future mode-tracking
        hook; today it is always ``None`` (the snapshot is no-I/O by
        contract, so it cannot probe).
        """
        info = self._info
        session = self._session
        name = info.model if info is not None else "balance"
        return SartoriusDeviceSnapshot(
            name=name,
            model=info.model if info is not None else None,
            firmware=str(info.firmware) if info is not None and info.firmware is not None else None,
            serial=(info.serial if info is not None else None),
            connected=session.state.value == "operational",
            last_error=None,
            recoverable_error_count=session.recoverable_error_count,
            captured_at=datetime.now(UTC),
            family=info.family if info is not None else session.family,
            capabilities=info.capabilities if info is not None else session.capabilities,
            protocol=session.active_protocol,
            mode=None,
        )

    async def refresh_sbi_autoprint_state(self, *, timeout: float | None = None) -> bool:
        """Re-sniff whether an SBI session is currently in autoprint mode.

        Use this after changing autoprint from the balance front panel during
        an open session. A quiet sniff clears autoprint mode so command/reply
        SBI APIs become available again; observed output keeps the session in
        consume-only autoprint mode.
        """
        return await self._session.refresh_sbi_autoprint_state(timeout=timeout)

    # ------------------------------------------------------------------ weight reads

    async def poll(self) -> Reading:
        """Read the live net weight at standard resolution.

        Short-cut for :meth:`read_net` with no arguments. One-shot
        request/response; to stream at a cadence use
        :func:`sartoriuslib.streaming.record`.
        """
        if self._session.sbi_autoprint_active:
            return await self._session.read_sbi_autoprint_reading()
        return await self._session.execute(READ_NET, ReadWeightRequest())

    async def read_net(self, *, hires: int = 0) -> Reading:
        """Read the net weight.

        Arguments:
            hires: ``0`` = standard resolution (xBPI ``0x1E``),
                ``1`` = 10× resolution (``0x1F`` TLV-21 arg ``0x01``),
                ``2`` = 100× resolution (``0x1F`` TLV-21 arg ``0x02``).
        """
        if hires == 0:
            if self._session.sbi_autoprint_active:
                return await self._session.read_sbi_autoprint_reading()
            return await self._session.execute(READ_NET, ReadWeightRequest())
        return await self._session.execute(
            READ_NET_HIRES,
            ReadWeightHiresRequest(resolution=hires),
        )

    async def read_gross(self, *, hires: int = 0) -> Reading:
        """Read the gross weight."""
        if hires == 0:
            return await self._session.execute(READ_GROSS, ReadWeightRequest())
        return await self._session.execute(
            READ_GROSS_HIRES,
            ReadWeightHiresRequest(resolution=hires),
        )

    async def read_tare_value(self) -> Reading:
        """Read the stored tare value (the reference, not a live operation)."""
        return await self._session.execute(READ_TARE_VALUE, ReadWeightRequest())

    def stream(
        self,
        *,
        rate_hz: float | None = None,
        mode: StreamMode = "poll",
        temporary_autoprint: bool = False,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> StreamingSession:
        """Create a per-balance streaming session.

        ``mode="poll"`` is the default and requires ``rate_hz``.
        ``mode="autoprint"`` consumes existing SBI autoprint output without
        changing device settings. ``temporary_autoprint=True`` is reserved
        for the future "enable on entry, restore on exit" SBI parameter flow
        and currently raises :class:`NotImplementedError`.
        """
        from sartoriuslib.streaming.stream_session import StreamingSession  # noqa: PLC0415

        return StreamingSession(
            self,
            rate_hz=rate_hz,
            mode=mode,
            temporary_autoprint=temporary_autoprint,
            confirm=confirm,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ tare / zero

    async def tare(self) -> None:
        """Run the combined-tare command (xBPI ``0x14`` / SBI ``ESC T``)."""
        await self._session.execute(TARE, TareRequest())

    async def zero(self) -> None:
        """Run the zeroing command (xBPI ``0x18``)."""
        await self._session.execute(ZERO, TareRequest())

    # ------------------------------------------------------------------ status / identity

    async def status(self) -> BalanceStatus:
        """Read the full 8-byte status block (xBPI ``0x30``)."""
        return await self._session.execute(STATUS_BLOCK, StatusRequest())

    async def identify(self) -> DeviceInfo:
        """Read every identity opcode and compose a :class:`DeviceInfo`.

        Runs in sequence — the session's I/O lock keeps them serialised
        on the wire. The session carries the serial framing it was
        opened with (the device doesn't expose its own baud/parity), so
        :class:`DeviceInfo` reports those settings directly; sessions
        constructed without framing fall back to a placeholder.

        After the textual identity primitives, the factory probes
        capacity and increment via :meth:`capacity` / :meth:`increment`
        and writes them onto the returned :class:`DeviceInfo` for
        WZG-family balances where the metrology commands are known to
        respond. Failures are swallowed — a balance that refuses
        ``0x0C`` keeps the old behaviour of ``capacity=None``.
        """
        session = self._session
        req = IdentityRequest()
        info_settings = session.serial_settings or _placeholder_serial_settings()

        if session.active_protocol is ProtocolKind.SBI:
            model = await session.execute(READ_MODEL, req)
            serial_bytes = await session.execute(READ_FACTORY_NUMBER, req)
            software_bytes = await session.execute(READ_SW_VERSION, req)
            sbi_serial = _decode_ascii_identity(serial_bytes)
            sbi_software = _decode_ascii_identity(software_bytes)
            family = classify_family(model)
            caps_seed = _FAMILY_DEFAULT_CAPABILITIES[family] | Capability.SBI_SUPPORT
            session.update_identity(family=family, capabilities=caps_seed)
            info = DeviceInfo(
                manufacturer=None,
                model=model,
                serial=sbi_serial or None,
                factory_number=sbi_serial or serial_bytes or None,
                software=sbi_software or None,
                firmware=None,
                family=family,
                protocol=session.active_protocol,
                capacity=None,
                increment=None,
                sbn=None,
                serial_settings=info_settings,
                capabilities=caps_seed,
            )
            self._info = info
            return info

        model = await session.execute(READ_MODEL, req)
        manufacturer = await session.execute(READ_MANUFACTURER, req)
        software = await session.execute(READ_SW_VERSION, req)
        factory_number = await session.execute(READ_FACTORY_NUMBER, req)
        sbn = await session.execute(READ_SBN, req)

        family = classify_family(model)
        caps_seed = _FAMILY_DEFAULT_CAPABILITIES[family]
        # Capabilities that gate dispatch behaviour are runtime-probed
        # here so the family table cannot lie to the dispatch layer
        # (design §5.1: priors do not assert before observation).
        caps_seed |= await self._probe_dispatch_capabilities()
        # Propagate family + observed caps into the session so subsequent
        # prior gates (and the cache capability check in
        # :meth:`capacity`/:meth:`increment` below) see a real family.
        session.update_identity(family=family, capabilities=caps_seed)

        # Capability probes — a balance that refuses 0x0C / 0x0D leaves
        # the fields as None rather than failing identify(). Narrow to
        # SartoriusError so programmer bugs (KeyError from a registry
        # miss, TypeError in a decoder) still surface as identify
        # failures instead of silent ``capacity=None``. Suppress the
        # prior-mismatch warning the same way :meth:`_probe_dispatch_capabilities`
        # does — capacity()/increment() route through the parameter
        # table to resolve the display unit, which warns on families
        # without ``Capability.PARAMETER_TABLE`` and would be promoted
        # to an error under ``filterwarnings=error`` (e.g. WZA).
        capacity_q: Quantity | None = None
        increment_q: Quantity | None = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SartoriusCapabilityWarning)
            try:
                capacity_q = await self.capacity()
            except SartoriusError:
                capacity_q = None
            try:
                increment_q = await self.increment()
            except SartoriusError:
                increment_q = None

        info = DeviceInfo(
            manufacturer=manufacturer or None,
            model=model,
            serial=None,
            factory_number=factory_number,
            software=software.hex() if software else None,
            firmware=None,
            family=family,
            protocol=session.active_protocol,
            capacity=capacity_q,
            increment=increment_q,
            sbn=sbn,
            serial_settings=info_settings,
            capabilities=caps_seed,
        )
        self._info = info
        return info

    async def _probe_dispatch_capabilities(self) -> Capability:
        """Probe device for capabilities that gate :class:`Session` dispatch.

        Returns a :class:`Capability` bitmap of capabilities the device
        actually answers to. Only probes capabilities that **change the
        call path** when set:

        - :attr:`Capability.CONFIG_COUNTER` — controls whether
          :meth:`Session.cached_execute` reads ``0xBA`` before each
          cached call. Probing prevents a Cubis variant lacking ``0xBA``
          from breaking every cached metrology read.

        Capabilities that only seed soft priors (HIRES_WEIGHT,
        TEMPERATURE_SENSORS, CAL_RECORD, ...) are not probed here —
        they're cheap to assume and the session degrades gracefully when
        they turn out to be wrong (one warning + one ``0x04``-driven
        availability cache update).

        Failures are swallowed: a probe that raises leaves the
        capability unset, the same as a failed live call would. This is
        identify-time best-effort — the call site still works on a
        device that times out on every probe.

        The probe is silenced from the prior-mismatch warning machinery:
        the whole point of probing is that the capability prior isn't
        yet decided, so the noise would just be wrong.
        """
        if self._session.active_protocol is not ProtocolKind.XBPI:
            # CONFIG_COUNTER is xBPI-only (``0xBA``); SBI sessions never
            # carry it.
            return Capability(0)
        observed = Capability(0)
        with warnings.catch_warnings():
            # The CONFIG_COUNTER command carries
            # ``capability_hints=Capability.CONFIG_COUNTER`` — without
            # this guard the probe would emit its own
            # ``SartoriusCapabilityWarning`` chasing its own tail.
            warnings.simplefilter("ignore", SartoriusCapabilityWarning)
            try:
                await self._session.execute(CONFIG_COUNTER, SystemRequest())
            except SartoriusError:
                # 0x04 / timeout / parse error all leave the capability
                # unset. The session's availability cache already
                # records what happened on this call.
                return observed
        observed |= Capability.CONFIG_COUNTER
        return observed

    # ------------------------------------------------------------------ metrology

    async def capacity(self, area: int = 0) -> Quantity:
        """Read the weighing capacity for ``area`` (xBPI ``0x0C``).

        Cached by the session when :attr:`Capability.CONFIG_COUNTER`
        is present — the balance's ``0xBA`` counter bumps on display-
        accuracy changes (``p08``) which is the only thing that would
        move this value in practice.

        The wire's typed-float reply does **not** carry a unit byte
        (contrast the 8-byte measurement body's byte [6]). To return
        a complete :class:`Quantity` we read the current display unit
        (``p07``) and fold it in here. ``get_display_unit()`` is itself
        cached on ``0xBA`` via the parameter-table cache, so two
        successive ``capacity()`` calls only re-read ``0xBA`` (twice,
        once per call) and not ``0x0C`` or ``0x55``. If
        ``get_display_unit()`` itself fails (e.g. the parameter table
        is unreachable), the unit falls back to :attr:`Unit.UNKNOWN`
        and the numeric value still returns — fail-open is more useful
        than a hard error for a metadata read.
        """
        raw = await self._session.cached_execute(
            READ_CAPACITY,
            MetrologyRequest(area=area),
            cache_key=f"capacity:{area}",
        )
        return await self._resolve_metrology_unit(raw)

    async def increment(self, area: int = 0) -> Quantity:
        """Read the display increment for ``area`` (xBPI ``0x0D``).

        See :meth:`capacity` for the caching contract and the
        composite-unit fold.
        """
        raw = await self._session.cached_execute(
            READ_INCREMENT,
            MetrologyRequest(area=area),
            cache_key=f"increment:{area}",
        )
        return await self._resolve_metrology_unit(raw)

    async def _resolve_metrology_unit(self, raw: Quantity) -> Quantity:
        """Fold the current display unit into a metrology Quantity.

        The shared helper for capacity/increment — both opcodes return
        a typed-float body with no unit byte, so the only way to expose
        a complete :class:`Quantity` is to read ``p07`` (display unit)
        and combine. Fails open: if ``get_display_unit()`` raises, the
        original :attr:`Unit.UNKNOWN` survives so the numeric value is
        not lost. Refuses to overwrite a unit the wire actually carried
        (defensive — current decoders always set ``Unit.UNKNOWN`` here,
        but if a later decode ever resolves the unit on its own it
        would be authoritative).
        """
        if raw.unit is not Unit.UNKNOWN:
            return raw
        try:
            unit = await self.get_display_unit()
        except SartoriusError:
            return raw
        return Quantity(value=raw.value, unit=unit)

    async def temperature(self, sensor: int = 0) -> TemperatureReading:
        """Read one temperature sensor (xBPI ``0x76``).

        Returns a :class:`TemperatureReading` with ``celsius=None``
        when ``sensor`` is not installed (balance returns the
        ``7f ff ff ff`` sentinel per ``docs/protocol.md`` §9).

        Not cached — temperature changes continuously and callers
        expect a fresh read every call.

        Sensor indexing is **device-specific** — some firmwares are
        contiguous, some are sparse with reserved slots (the MSE1203S
        we tested has sensors at ``0/1/3`` and a sentinel at ``2``).
        Use :meth:`discover_temperature_sensors` to enumerate.
        ``READ_TEMPERATURE`` is :attr:`Command.parameterized`, so an
        out-of-range index raises
        :class:`SartoriusIndexOutOfRangeError` without poisoning the
        availability cache for in-range indices.
        """
        raw = await self._session.execute(
            READ_TEMPERATURE,
            TemperatureRequest(sensor=sensor),
        )
        # Variant decode can't see the request — fill the sensor
        # field here so the returned dataclass round-trips.
        return dataclasses.replace(raw, sensor=sensor)

    async def discover_temperature_sensors(
        self,
        *,
        max_index: int = _TEMPERATURE_DISCOVERY_MAX_INDEX,
    ) -> tuple[int, ...]:
        """Probe the device for installed temperature sensors at runtime.

        Iterates indices ``0..max_index`` calling :meth:`temperature`
        on each. Records every index that replies — both real
        readings (``celsius`` is a ``float``) **and** sentinel slots
        (``celsius`` is ``None`` because the firmware returned
        ``7f ff ff ff``). Stops early on
        :class:`SartoriusIndexOutOfRangeError`, which the device
        emits past the last valid index. Updates the cached
        :class:`DeviceInfo`'s
        :attr:`temperature_sensor_indices` with the discovered tuple
        and returns it.

        Device-agnostic by design — no family table is consulted, so
        a balance we have never tested still produces an honest
        sensor map.

        ``max_index`` is a safety cap, not a contract: 8 is the
        default headroom (Cubis MSE captures stop at 3). Probing a
        device with no sensors produces an empty tuple. Each probe
        is one round-trip; the result is not cached on ``0xBA``
        because per-call temperature reads are not cached either —
        callers re-discover on demand.
        """
        if max_index < 0:
            raise ValueError(f"max_index must be >= 0, got {max_index}")
        # Local import — lazy to avoid pulling errors module at
        # construction time. The class is intentionally module-level.
        from sartoriuslib.errors import (  # noqa: PLC0415
            SartoriusIndexOutOfRangeError,
            SartoriusUnsupportedCommandError,
        )

        discovered: list[int] = []
        for sensor in range(max_index + 1):
            try:
                reading = await self.temperature(sensor)
            except SartoriusIndexOutOfRangeError:
                # Past the last valid index — clean stop.
                break
            except SartoriusUnsupportedCommandError:
                # Some firmwares mis-report end-of-list as 0x04 instead
                # of 0x10. ``temperature`` is parameterized so the
                # availability cache is not poisoned for in-range
                # indices; we still treat it as the end signal here.
                break
            discovered.append(reading.sensor)
        result = tuple(discovered)
        if self._info is not None:
            self._info = dataclasses.replace(
                self._info,
                temperature_sensor_indices=result,
            )
        return result

    # ------------------------------------------------------------------ raw parameter I/O

    async def read_parameter(self, index: int) -> ParameterEntry:
        """Read one parameter-table entry (xBPI ``0x55``).

        Returns the ``(current, max)`` u8 pair untouched; typed
        accessors layer on top to decode via the
        :class:`sartoriuslib.registry.parameters.ParameterSpec` table.
        Cached on ``0xBA``.
        """
        entry = await self._session.cached_execute(
            READ_PARAMETER,
            ReadParameterRequest(index=index),
            cache_key=_cache_key_parameter(index),
        )
        return dataclasses.replace(entry, index=index)

    async def write_parameter(
        self,
        index: int,
        value: int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write one parameter-table entry (xBPI ``0x56``). ``PERSISTENT``.

        Requires ``confirm=True``. Invalidates the cached entry for
        ``index`` afterwards — conservative so the §6.3 caveat rows
        (``p13`` / ``p50``, whose writes don't bump ``0xBA``) stay
        consistent.
        """
        await self._session.execute(
            WRITE_PARAMETER,
            WriteParameterRequest(index=index, value=value),
            confirm=confirm,
        )
        self._session.invalidate_cache(_cache_key_parameter(index))

    # ------------------------------------------------------------------ typed parameter accessors

    async def _get_typed(self, index: int) -> int:
        """Read parameter ``index`` and return the raw ``current`` byte.

        Typed getters convert the returned u8 via their spec's
        :meth:`ParameterSpec.decode`. Keeps one shared round-trip
        path for every typed accessor.
        """
        entry = await self.read_parameter(index)
        return entry.current

    async def _set_typed(self, index: int, wire_value: int, *, confirm: bool) -> None:
        """Write parameter ``index`` with an already-encoded u8."""
        await self.write_parameter(index, wire_value, confirm=confirm)

    async def get_filter_mode(self) -> FilterMode:
        """Read ``p01`` (filter mode) as a :class:`FilterMode`."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["filter_mode"]]
        current = await self._get_typed(spec.index)
        # The spec's enum class is FilterMode — decode() is guaranteed
        # to return a FilterMode member (or FilterMode.UNKNOWN). The
        # signature is the union across all specs so we narrow here.
        return cast("FilterMode", spec.decode(current))

    async def set_filter_mode(
        self,
        mode: FilterMode | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p01``. Accepts :class:`FilterMode`, a fuzzy string, or wire int.

        Fuzzy strings (``"stable"`` / ``"very stable"`` / ``"vs"``)
        route through :func:`resolve_filter_mode`.
        """
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["filter_mode"]]
        resolved = resolve_filter_mode(mode)
        wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    async def get_display_unit(self) -> Unit:
        """Read ``p07`` (display unit) as a :class:`Unit`."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["display_unit"]]
        current = await self._get_typed(spec.index)
        # p07 is the only unit-valued spec; decode() returns a Unit.
        return cast("Unit", spec.decode(current))

    async def set_display_unit(
        self,
        unit: Unit | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p07``. Accepts :class:`Unit`, a fuzzy string, or wire code."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["display_unit"]]
        if isinstance(unit, int):
            # Raw wire code — encode does the range check.
            wire = spec.encode(unit)
        else:
            resolved = resolve_unit(unit)
            wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    async def get_auto_zero(self) -> AutoZeroMode:
        """Read ``p06`` (auto-zero tracking) as an :class:`AutoZeroMode`."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["auto_zero"]]
        current = await self._get_typed(spec.index)
        return cast("AutoZeroMode", spec.decode(current))

    async def set_auto_zero(
        self,
        mode: AutoZeroMode | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p06``. Accepts :class:`AutoZeroMode`, a string, or wire int."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["auto_zero"]]
        resolved = resolve_auto_zero(mode)
        wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    async def get_isocal_mode(self) -> IsoCalMode:
        """Read ``p15`` (isoCAL mode) as an :class:`IsoCalMode` (Cubis only)."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["isocal_mode"]]
        current = await self._get_typed(spec.index)
        return cast("IsoCalMode", spec.decode(current))

    async def set_isocal_mode(
        self,
        mode: IsoCalMode | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p15``."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["isocal_mode"]]
        resolved = resolve_isocal_mode(mode)
        wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    async def get_tare_behavior(self) -> TareBehavior:
        """Read ``p05`` (tare-on-stability behaviour) as :class:`TareBehavior`."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["tare_behavior"]]
        current = await self._get_typed(spec.index)
        return cast("TareBehavior", spec.decode(current))

    async def set_tare_behavior(
        self,
        mode: TareBehavior | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p05``."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["tare_behavior"]]
        resolved = resolve_tare_behavior(mode)
        wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    async def get_menu_access(self) -> MenuAccessMode:
        """Read ``p40`` (front-panel menu lock) as :class:`MenuAccessMode`."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["menu_access"]]
        current = await self._get_typed(spec.index)
        return cast("MenuAccessMode", spec.decode(current))

    async def set_menu_access(
        self,
        mode: MenuAccessMode | str | int,
        *,
        confirm: bool = False,
    ) -> None:
        """Write ``p40``."""
        spec = PARAMETER_TABLE[_TYPED_ACCESSOR_INDICES["menu_access"]]
        resolved = resolve_menu_access(mode)
        wire = spec.encode(resolved)
        await self._set_typed(spec.index, wire, confirm=confirm)

    # ------------------------------------------------------------------ EEPROM persistence

    async def save_menu(self, *, confirm: bool = False) -> None:
        """Persist the current runtime menu to EEPROM (xBPI ``0x47``)."""
        await self._session.execute(SAVE_MENU, SystemRequest(), confirm=confirm)
        # Any persistent write may change values the cache relied on;
        # flush everything defensively.
        self._session.invalidate_cache()

    async def reload_menu(self, *, confirm: bool = False) -> None:
        """Reload the saved menu from EEPROM (xBPI ``0x46``)."""
        await self._session.execute(RELOAD_MENU, SystemRequest(), confirm=confirm)
        self._session.invalidate_cache()

    # ------------------------------------------------------------------ calibration

    async def last_cal_record(self) -> CalRecord:
        """Read the last-calibration snapshot (xBPI ``0xB9``, §7.12)."""
        return await self._session.execute(LAST_CAL_RECORD, LastCalRecordRequest())

    async def internal_adjust(
        self,
        *,
        cal_type: int | None = None,
        confirm: bool = False,
    ) -> None:
        """Start an internal adjustment (xBPI ``0x28``). ``DANGEROUS``.

        ``cal_type`` defaults to the canonical internal-adjust selector
        (``0x78``) — see
        :data:`sartoriuslib.commands.calibration.INTERNAL_ADJUST_CAL_TYPE`.
        Callers can pass another value in the ``0x70..0x7B`` range to
        drive external cal / linearization variants per
        ``docs/protocol.md`` §7.7.
        """
        req = (
            InternalAdjustRequest()
            if cal_type is None
            else InternalAdjustRequest(cal_type=cal_type)
        )
        await self._session.execute(INTERNAL_ADJUST, req, confirm=confirm)

    # ------------------------------------------------------------------ lifecycle ops

    async def configure_protocol(
        self,
        target: ProtocolKind,
        *,
        baudrate: int | None = None,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> DeviceInfo:
        """Switch this balance's active wire protocol (and optionally framing).

        ``DANGEROUS`` — requires ``confirm=True``. The flip is purely
        host-side: per ``docs/protocol.md`` §2.1 the device's protocol
        mode changes via the front-panel menu, never via xBPI for the
        PC-USB port (verified empirically 2026-04-25 on MSE1203S — the
        PC-USB protocol selector is not in the xBPI parameter table at
        all; only `Device → PC-USB → Dat.Rec.` on the front panel
        flips it). On the 9-pin peripheral port, `p35` write +
        SAVE_MENU + cold boot does work programmatically, but most
        users are on PC-USB.

        This method reconciles the host with the user's front-panel
        change by closing the current protocol client, reopening the
        transport at the new serial framing (any ``None`` argument keeps
        the existing value), and building the new client. It then
        verifies via an identity probe and refreshes :attr:`info` from
        the new protocol.

        If the user has NOT actually flipped the front panel before
        calling this method, the post-switch identity probe will fail
        — typically with a :class:`SartoriusFrameError` ("bad marker
        byte 0x20") when xBPI is requested but the wire is still
        emitting SBI autoprint, or a timeout when SBI is requested
        but the wire is still on xBPI. Treat that error as a strong
        signal that the front-panel mode does not match ``target``.

        On any failure during the switch the method attempts to roll
        back to the original serial framing. If that rollback also
        fails the underlying :class:`Session` transitions to
        :attr:`SessionState.BROKEN` and a
        :class:`SartoriusConnectionError` is raised — the caller must
        close this balance and re-open via
        :func:`sartoriuslib.open_device` to recover.

        Same-protocol no-op: when ``target`` equals the current
        protocol and no framing override is supplied, the call returns
        the cached :class:`DeviceInfo` (or runs :meth:`identify` if
        none has been cached yet) without touching the transport.
        """
        if target is ProtocolKind.AUTO:
            raise SartoriusValidationError(
                "configure_protocol: target must be XBPI or SBI, not AUTO",
                context=ErrorContext(
                    command_name="configure_protocol",
                    extra={"target": "auto"},
                ),
            )
        if not confirm:
            raise SartoriusConfirmationRequiredError(
                "configure_protocol is DANGEROUS; pass confirm=True to execute",
                context=ErrorContext(
                    command_name="configure_protocol",
                    extra={"target": target.value},
                ),
            )

        session = self._session
        session.check_state()

        no_framing_change = baudrate is None and parity is None and stopbits is None
        if target is session.active_protocol and no_framing_change:
            return self._info if self._info is not None else await self.identify()

        transport = session.transport

        old_settings = session.serial_settings
        old_xbpi = session.xbpi_client
        old_sbi = session.sbi_client
        old_active = session.active_protocol
        active_lock = (
            old_xbpi.lock
            if old_active is ProtocolKind.XBPI and old_xbpi is not None
            else old_sbi.lock
            if old_sbi is not None
            else None
        )
        if active_lock is None:
            raise SartoriusError(
                "configure_protocol: active protocol has no client lock",
                context=ErrorContext(command_name="configure_protocol"),
            )

        t = timeout if timeout is not None else session.default_timeout

        async with active_lock:
            try:
                await transport.drain_input()
                await transport.reopen(
                    baudrate=baudrate,
                    parity=parity,
                    stopbits=stopbits,
                )
                new_xbpi: XbpiProtocolClient | None = None
                new_sbi: SbiProtocolClient | None = None
                if target is ProtocolKind.XBPI:
                    new_xbpi = XbpiProtocolClient(transport, default_timeout=t)
                else:
                    new_sbi = SbiProtocolClient(transport, default_timeout=t)
                    await new_sbi.detect_autoprint(timeout=min(t, 0.25))
                if not (
                    target is ProtocolKind.SBI and new_sbi is not None and new_sbi.autoprint_active
                ):
                    await _verify_identity_probe(
                        target,
                        new_xbpi,
                        new_sbi,
                        src_sbn=session.src_sbn,
                        dst_sbn=session.dst_sbn,
                        timeout=t,
                    )
                new_settings = _overlay_settings(
                    old_settings,
                    baudrate=baudrate,
                    parity=parity,
                    stopbits=stopbits,
                )
                session.replace_clients(
                    xbpi_client=new_xbpi,
                    sbi_client=new_sbi,
                    active_protocol=target,
                    serial_settings=new_settings,
                )
                _dispose_replaced_clients(
                    old_xbpi=old_xbpi,
                    old_sbi=old_sbi,
                    new_xbpi=new_xbpi,
                    new_sbi=new_sbi,
                )
            except Exception as switch_error:
                await self._rollback_configure_protocol(
                    transport=transport,
                    old_settings=old_settings,
                    target=target,
                    cause=switch_error,
                )
                raise

        # Identify outside the old client's lock — Session.execute will
        # acquire the new client's lock itself.
        return await self.identify()

    async def _rollback_configure_protocol(
        self,
        *,
        transport: Transport,
        old_settings: SerialSettings | None,
        target: ProtocolKind,
        cause: BaseException,
    ) -> None:
        """Restore the transport to the pre-switch framing or mark BROKEN.

        Called from :meth:`configure_protocol`'s except block. Tries to
        reopen the transport at ``old_settings``; on any failure marks
        the session :attr:`SessionState.BROKEN` and raises a wrapped
        :class:`SartoriusConnectionError` from ``cause``.
        """
        if old_settings is None:
            # No tracked framing to restore. The transport may be at the
            # new baud/parity already; the original clients are gone.
            self._session.mark_broken()
            return
        try:
            await transport.reopen(
                baudrate=old_settings.baudrate,
                parity=old_settings.parity,
                stopbits=old_settings.stopbits,
            )
        except Exception as rollback_error:
            self._session.mark_broken()
            err = SartoriusConnectionError(
                f"configure_protocol to {target.value} failed and the rollback "
                f"reopen at {old_settings.port!r} also failed; session is BROKEN. "
                "Close this balance and re-open via open_device(...) to recover.",
                context=ErrorContext(
                    command_name="configure_protocol",
                    port=old_settings.port,
                    extra={
                        "target_protocol": target.value,
                        "session_state": "broken",
                        "rollback_error": repr(rollback_error),
                    },
                ),
            )
            # ``cause`` is the original switch failure that triggered the
            # rollback. Preserve it as the explicit ``__cause__`` so callers
            # walking the chain see the real reason; the rollback failure is
            # captured in ``extra`` above and remains as ``__context__``.
            raise err from cause

    async def set_baud_rate(
        self,
        wire_code: int,
        *,
        baudrate: int,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> DeviceInfo:
        """Send xBPI ``0x5C`` and reopen the transport at the new baud.

        ``DANGEROUS`` — requires ``confirm=True``. ``wire_code`` is the
        device-side encoding from ``docs/protocol.md`` §7.10
        (``0x00=9600``, ``0x01=19200``, ``0x02=38400``, ``0x03=57600``);
        ``baudrate`` is the matching host-side baud (the transport's
        framing is reopened at this value). The library does not map
        between the two automatically because the encoding is
        documented as "different from p31 / p63" and we have no RE
        captures verifying the mapping yet — the caller passes both so
        the on-wire byte and the host framing stay explicit.

        After the device-side ACK the transport is reopened, identity
        is reprobed for verification, and the cached :class:`DeviceInfo`
        is refreshed. Verification failure rolls the transport back to
        the original baud; if rollback fails the session enters
        :attr:`SessionState.BROKEN`.

        SBI sessions are not supported here — the ``0x5C`` opcode is
        xBPI-only. Call :meth:`configure_protocol` first to switch to
        xBPI if needed.
        """
        if not confirm:
            raise SartoriusConfirmationRequiredError(
                "set_baud_rate is DANGEROUS; pass confirm=True to execute",
                context=ErrorContext(
                    command_name="set_baud_rate",
                    extra={"wire_code": wire_code, "baudrate": baudrate},
                ),
            )
        if self._session.active_protocol is not ProtocolKind.XBPI:
            raise SartoriusError(
                "set_baud_rate requires an xBPI session; "
                "call configure_protocol(ProtocolKind.XBPI, ...) first",
                context=ErrorContext(
                    command_name="set_baud_rate",
                    protocol=str(self._session.active_protocol.value),
                ),
            )
        if not 0 <= wire_code <= _MAX_U8:
            raise SartoriusValidationError(
                f"set_baud_rate: wire_code must be 0..0xFF, got {wire_code!r}",
                context=ErrorContext(
                    command_name="set_baud_rate",
                    extra={"wire_code": wire_code},
                ),
            )

        # Send the on-wire change at the OLD baud. The device ACKs at
        # the old baud, then takes the new baud effective. Some firmware
        # may swallow the ACK across the change — treat ACK timeout as
        # a non-fatal signal and proceed to reopen + verify.
        with contextlib.suppress(SartoriusError):
            await self._session.execute_raw_xbpi(
                0x5C,
                encode_tlv(0x21, wire_code),
                confirm=True,
                timeout=timeout,
            )

        # Reuse configure_protocol's reopen + verify + rollback machinery
        # by switching to the same protocol with new framing.
        return await self.configure_protocol(
            ProtocolKind.XBPI,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            confirm=True,
        )

    async def write_sbn_address(
        self,
        sbn: int,
        *,
        update_session_dst: bool = False,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> int:
        """Send xBPI ``0x72`` to change the balance's SBN address.

        ``DANGEROUS`` — requires ``confirm=True``. Returns the value
        read back via ``0x71`` after the write so the caller can
        verify. The session's ``dst_sbn`` is left unchanged by default
        because the balance accepts ``dst_sbn=0x09`` regardless of its
        configured SBN on a direct point-to-point link
        (``docs/protocol.md`` §2.2). Pass ``update_session_dst=True``
        on multidrop links where the new SBN must address the device
        going forward.
        """
        if not confirm:
            raise SartoriusConfirmationRequiredError(
                "write_sbn_address is DANGEROUS; pass confirm=True to execute",
                context=ErrorContext(
                    command_name="write_sbn_address",
                    extra={"sbn": sbn},
                ),
            )
        if not 0 <= sbn <= _MAX_U8:
            raise SartoriusValidationError(
                f"write_sbn_address: sbn must be 0..0xFF, got {sbn!r}",
                context=ErrorContext(
                    command_name="write_sbn_address",
                    extra={"sbn": sbn},
                ),
            )
        if self._session.active_protocol is not ProtocolKind.XBPI:
            raise SartoriusError(
                "write_sbn_address requires an xBPI session; "
                "call configure_protocol(ProtocolKind.XBPI, ...) first",
                context=ErrorContext(
                    command_name="write_sbn_address",
                    protocol=str(self._session.active_protocol.value),
                ),
            )

        await self._session.execute_raw_xbpi(
            0x72,
            encode_tlv(0x21, sbn),
            confirm=True,
            timeout=timeout,
        )
        readback = await self._session.execute(
            READ_SBN,
            IdentityRequest(),
            timeout=timeout,
        )
        if update_session_dst:
            self._session.set_dst_sbn(sbn)
        return readback

    # ------------------------------------------------------------------ escape hatch

    async def raw_xbpi(
        self,
        opcode: int,
        args: bytes = b"",
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> XbpiFrame:
        """Send an arbitrary xBPI opcode and return the raw reply frame.

        Opcodes in the built-in read-only safe-list
        (:data:`sartoriuslib.commands.raw.SAFE_READ_ONLY_OPCODES`) run
        freely; anything else requires ``confirm=True``. Intended for
        RE and one-off probes — typed commands are the preferred path
        for everything the library already models.
        """
        return await self._session.execute_raw_xbpi(
            opcode,
            args,
            confirm=confirm,
            timeout=timeout,
        )

    async def raw_sbi(
        self,
        command: bytes | str,
        *,
        confirm: bool = False,
        timeout: float | None = None,
        expect_lines: int = 1,
    ) -> SbiReply:
        """Send an arbitrary SBI command and return parsed line replies."""
        return await self._session.execute_raw_sbi(
            command,
            confirm=confirm,
            timeout=timeout,
            expect_lines=expect_lines,
        )


async def _verify_identity_probe(
    target: ProtocolKind,
    xbpi_client: XbpiProtocolClient | None,
    sbi_client: SbiProtocolClient | None,
    *,
    src_sbn: int,
    dst_sbn: int,
    timeout: float,
) -> None:
    """Send a single READ_MODEL through the new client to verify the switch.

    Used by :meth:`Balance.configure_protocol` after a transport reopen
    and before installing the new client on the session. Raises any
    transport / protocol error directly so the caller's ``except`` can
    drive rollback.
    """
    ctx = CommandContext(
        protocol=target,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
        firmware=None,
        family=BalanceFamily.UNKNOWN,
    )
    if target is ProtocolKind.XBPI:
        xbpi_variant = READ_MODEL.xbpi
        if xbpi_variant is None:  # pragma: no cover — READ_MODEL declares xbpi
            raise SartoriusError("_verify_identity_probe: READ_MODEL.xbpi is None")
        if xbpi_client is None:  # pragma: no cover — caller guards this
            raise SartoriusError("_verify_identity_probe: xbpi_client is None")
        await xbpi_client.execute(
            xbpi_variant.encode(ctx, IdentityRequest()),
            timeout=timeout,
            command_name="configure_protocol_verify",
            opcode=0x02,
        )
        return
    sbi_variant = READ_MODEL.sbi
    if sbi_variant is None:  # pragma: no cover — READ_MODEL declares sbi
        raise SartoriusError("_verify_identity_probe: READ_MODEL.sbi is None")
    if sbi_client is None:  # pragma: no cover — caller guards this
        raise SartoriusError("_verify_identity_probe: sbi_client is None")
    await sbi_client.execute(
        sbi_variant.encode(ctx, IdentityRequest()),
        timeout=timeout,
        command_name="configure_protocol_verify",
        sbi_token=sbi_variant.token,
        expect_lines=sbi_variant.expect_lines,
    )


def _overlay_settings(
    base: SerialSettings | None,
    *,
    baudrate: int | None,
    parity: Parity | None,
    stopbits: StopBits | None,
) -> SerialSettings | None:
    """Return ``base`` with non-``None`` overrides applied, or ``None``."""
    if base is None:
        return None
    new = base
    if baudrate is not None:
        new = dataclasses.replace(new, baudrate=baudrate)
    if parity is not None:
        new = dataclasses.replace(new, parity=parity)
    if stopbits is not None:
        new = dataclasses.replace(new, stopbits=stopbits)
    return new


def _dispose_replaced_clients(
    *,
    old_xbpi: XbpiProtocolClient | None,
    old_sbi: SbiProtocolClient | None,
    new_xbpi: XbpiProtocolClient | None,
    new_sbi: SbiProtocolClient | None,
) -> None:
    """Retire old clients after a successful protocol/framing replacement."""
    replacements = {id(client) for client in (new_xbpi, new_sbi) if client is not None}
    for client in (old_xbpi, old_sbi):
        if client is not None and id(client) not in replacements:
            client.dispose()


def _placeholder_serial_settings() -> SerialSettings:
    """Fallback :class:`SerialSettings` when the caller omitted them.

    :meth:`Balance.identify` doesn't otherwise know the transport's
    serial config (only the factory does). Produce a plausible
    "unknown port, 8-O-1 at 9600" placeholder so the dataclass stays
    populated.
    """
    from sartoriuslib.transport.base import SerialSettings as _Settings  # noqa: PLC0415

    return _Settings(port="<unknown>")


def _decode_ascii_identity(raw: bytes) -> str:
    """Decode an SBI identity line returned through a bytes-shaped command."""
    return raw.decode("ascii", errors="replace").strip()


def _validate_accessor_registry() -> None:
    """Eager sanity: every typed-accessor index has a :class:`ParameterSpec`.

    Catches bit-rot when registry indices are renamed. Runs at import
    time so a broken registry fails fast, before the first call.
    """
    missing = {
        name: idx
        for name, idx in _TYPED_ACCESSOR_INDICES.items()
        if get_parameter_spec(idx) is None
    }
    if missing:  # pragma: no cover — guarded by test_package_imports
        raise RuntimeError(
            f"typed-accessor indices not in ParameterSpec registry: {missing}",
        )


_validate_accessor_registry()
