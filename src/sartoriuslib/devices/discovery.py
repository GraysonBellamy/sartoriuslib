"""Port discovery + :class:`DiscoveryResult` + :func:`find_devices`.

The unified spec (``UNIFIED_API_HANDOFF.md`` §B) defines a cross-library
``DiscoveryResult`` base shape that every sibling adapter (alicat,
watlow, nidaq, sartorius) returns one row of per probe attempt.
Sartorius-specific framing extras (parity, stopbits, SBI autoprint
state) live on :class:`SartoriusDiscoveryResult` — a subclass that
satisfies ``list[DiscoveryResult]`` for the cross-lib contract while
preserving sartoriuslib-specific metadata callers depend on.

Two layers, two helpers:

- :func:`discover_port` — open one port at one serial-settings
  configuration, run the conservative
  :func:`sartoriuslib.protocol.detect_protocol` probe, return a
  :class:`SartoriusDiscoveryResult`. The caller picks the framing.
- :func:`find_devices` — sweep a set of baudrates against one or more
  ports (default: every port :func:`anyserial.list_serial_ports`
  enumerates) and return one :class:`SartoriusDiscoveryResult` per
  *probe attempt*. Callers wanting a per-port best-hit answer use
  :func:`summarize_discovery` to fold attempts into
  :class:`DiscoverySummary` rows.

Both helpers are READ-ONLY — neither writes anything beyond what
:func:`detect_protocol` already sends (an xBPI ``READ_MODEL`` and
optionally ``ESC x1_`` / ``ESC P``). No tare, no zero, no autoprint
toggling.

Design reference: ``docs/design.md`` §4.3, §16 Q2.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import anyserial

from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sartoriuslib.devices.models import DeviceInfo
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.transport.base import Transport

__all__ = [
    "DEFAULT_DISCOVERY_BAUDRATES",
    "DiscoveryResult",
    "DiscoverySummary",
    "SartoriusDiscoveryResult",
    "discover_port",
    "find_devices",
    "summarize_discovery",
]


#: Default baud rates :func:`find_devices` sweeps when the caller doesn't
#: supply ``baudrates=``. Covers every Sartorius family the library has
#: captures for — BCE 9600, MSE 19200, WZA 1200 sits below the floor but
#: is reachable by passing it explicitly. Keep this constant authoritative
#: here so consumers (capa, scripts) don't fork their own copy.
DEFAULT_DISCOVERY_BAUDRATES: tuple[int, ...] = (9600, 19200, 38400, 57600, 115200)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Outcome of one probe attempt — the cross-library base shape.

    The unified spec (§B) pins these fields across sibling libraries.
    Use :class:`SartoriusDiscoveryResult` (a subclass) for the typed
    sartorius-specific extras; treat this base shape as the lowest
    common denominator multi-adapter consumers can rely on.

    Attributes:
        ok: ``True`` when the probe resolved to a wire protocol.
        port: The port label (path or pre-built transport's label).
        address: SBN address for xBPI hits, ``None`` for SBI (which is
            point-to-point) or for failed probes.
        baudrate: Effective baudrate during the probe; ``None`` when the
            port could not be opened at all.
        protocol: Resolved :class:`ProtocolKind` on success, ``None``
            when no responsive device was found.
        device_info: Identity snapshot from a successful probe.
            ``None`` for failures or for probes that resolved a wire
            protocol but did not run identify (e.g. SBI autoprint).
        error: The :class:`SartoriusError` captured on a failed probe,
            ``None`` on success.
        elapsed_s: Probe wall-clock duration in seconds.
    """

    ok: bool
    port: str
    address: str | int | None
    baudrate: int | None
    protocol: ProtocolKind | None
    device_info: DeviceInfo | None
    error: SartoriusError | None
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class SartoriusDiscoveryResult(DiscoveryResult):
    """Sartorius-typed probe result with serial-framing + autoprint extras.

    Adds the per-probe framing details and SBI autoprint state that the
    cross-lib base shape doesn't carry. Consumers reading the unified
    surface use ``DiscoveryResult`` fields uniformly; sartoriuslib
    callers (capa Discover dialog, ``sarto-discover``) read the
    subclass extras directly.

    Attributes:
        parity: Effective parity during the probe.
        stopbits: Effective stop bits during the probe (1, 1.5, 2).
        autoprint_active: ``True`` when the passive sniff window observed
            unsolicited SBI autoprint output.
        pending_lines: CRLF-terminated SBI lines consumed during the
            sniff that the caller may want to re-queue when opening a
            live SBI client (so the first autoprint sample is not lost).
    """

    parity: str = "O"
    stopbits: int = 1
    autoprint_active: bool = False
    pending_lines: tuple[bytes, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """Per-port roll-up of one or more :class:`SartoriusDiscoveryResult` probes.

    Returned by :func:`summarize_discovery`. The lowest-cost ergonomic
    "give me one row per port" shape for callers (Setup-editor Discover
    dialog, ``sarto-discover`` print output) that don't want to fold
    multi-baud attempts themselves.

    Attributes:
        port: The port label.
        ok: ``True`` when at least one probe attempt resolved a protocol.
        baudrate: First successful baudrate on a hit; the last attempted
            baudrate on a miss; ``None`` when no probe ran (port open
            failure short-circuited).
        protocol: Resolved :class:`ProtocolKind` on a hit, ``None``
            otherwise.
        autoprint_active: Carried from the winning probe on hits.
        error: First non-``None`` ``SartoriusError`` from the sweep —
            either the port-open exception (always wins) or the last
            per-baud miss reason.
        elapsed_s: Sum of every per-probe elapsed time for the port.
    """

    port: str
    ok: bool
    baudrate: int | None
    protocol: ProtocolKind | None
    autoprint_active: bool
    error: SartoriusError | None
    elapsed_s: float


async def discover_port(
    port: str | Transport,
    *,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    sniff_window: float = 0.25,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
) -> SartoriusDiscoveryResult:
    """Open ``port`` at ``serial_settings`` and run the conservative detect.

    Returns a :class:`SartoriusDiscoveryResult` capturing the chosen
    framing and the detector's verdict. The transport is closed before
    returning. Failures during ``detect_protocol`` (no responsive
    device, hard transport faults) surface in the result's ``error``
    field rather than being raised — discovery is meant to be safe to
    call against unknown ports without crashing the caller. Port-open
    failures (busy port, missing device) likewise return a non-``ok``
    result rather than raising.
    """
    transport, settings = _resolve_transport(port, serial_settings)
    label = transport.label
    start = anyio.current_time()
    try:
        if not transport.is_open:
            try:
                await transport.open()
            except SartoriusError as exc:
                return SartoriusDiscoveryResult(
                    ok=False,
                    port=label,
                    address=None,
                    baudrate=settings.baudrate,
                    protocol=None,
                    device_info=None,
                    error=exc,
                    elapsed_s=anyio.current_time() - start,
                    parity=settings.parity.value,
                    stopbits=int(settings.stopbits.value),
                )
        try:
            detection = await detect_protocol(
                transport,
                timeout=timeout,
                sniff_window=sniff_window,
                src_sbn=src_sbn,
                dst_sbn=dst_sbn,
            )
        except SartoriusError as exc:
            return SartoriusDiscoveryResult(
                ok=False,
                port=label,
                address=None,
                baudrate=settings.baudrate,
                protocol=None,
                device_info=None,
                error=exc,
                elapsed_s=anyio.current_time() - start,
                parity=settings.parity.value,
                stopbits=int(settings.stopbits.value),
            )
        return SartoriusDiscoveryResult(
            ok=True,
            port=label,
            address=dst_sbn if detection.protocol.value == "xbpi" else None,
            baudrate=settings.baudrate,
            protocol=detection.protocol,
            device_info=None,
            error=None,
            elapsed_s=anyio.current_time() - start,
            parity=settings.parity.value,
            stopbits=int(settings.stopbits.value),
            autoprint_active=detection.autoprint_active,
            pending_lines=detection.pending_lines,
        )
    finally:
        with contextlib.suppress(SartoriusError):
            await transport.close()


def _resolve_transport(
    port: str | Transport,
    serial_settings: SerialSettings | None,
) -> tuple[Transport, SerialSettings]:
    """Build a transport (or accept a pre-built one) plus effective settings."""
    if isinstance(port, str):
        settings = serial_settings or SerialSettings(port=port)
        return SerialTransport(settings), settings
    fallback = serial_settings or SerialSettings(port=port.label)
    return port, fallback


async def find_devices(
    *,
    ports: Sequence[str] | None = None,
    baudrates: Sequence[int] | None = None,
    per_probe_timeout_s: float = 0.5,
    sniff_window_s: float = 0.25,
) -> list[SartoriusDiscoveryResult]:
    """Probe local serial ports for Sartorius balances, sweeping baudrates.

    Returns one :class:`SartoriusDiscoveryResult` per *probe attempt* —
    one port × one baudrate. Callers wanting a per-port best-hit
    answer fold the list via :func:`summarize_discovery`.

    For each port in ``ports`` (or every port
    :func:`anyserial.list_serial_ports` enumerates when ``ports`` is
    ``None``), call :func:`discover_port` once per baudrate in
    ``baudrates`` (or :data:`DEFAULT_DISCOVERY_BAUDRATES` when ``None``)
    until either:

    - a probe reports ``ok=True`` (first hit wins for that port — the
      sweep short-circuits remaining bauds), or
    - a probe's port-open failure short-circuits the port (other bauds
      would fail the same way), or
    - every baud has been tried without a hit.

    The function is **read-only** and **never raises**: it only calls
    :func:`discover_port`, which only sends the conservative
    ``READ_MODEL`` / ``ESC x1_`` / ``ESC P`` probes, and captures every
    exception into a non-``ok`` result. No tare, no zero, no autoprint
    toggling.

    Arguments:
        ports: Explicit ports to probe. ``None`` enumerates via
            :func:`anyserial.list_serial_ports`. Order is preserved
            in the result.
        baudrates: Baudrates to sweep per port, in the order to try
            them. ``None`` uses :data:`DEFAULT_DISCOVERY_BAUDRATES`.
        per_probe_timeout_s: Per-probe :func:`detect_protocol`
            timeout. A wrong-baud probe times out fast at this bound,
            so a 5-baud × 5-port sweep is ~12.5 s wall-clock.
        sniff_window_s: Per-probe passive SBI autoprint sniff window.

    Returns:
        One :class:`SartoriusDiscoveryResult` per probe attempt, in
        port-then-baud order.
    """
    bauds = tuple(baudrates) if baudrates is not None else DEFAULT_DISCOVERY_BAUDRATES
    port_list = await _resolve_ports(ports)
    results: list[SartoriusDiscoveryResult] = []
    for port in port_list:
        for baud in bauds:
            settings = SerialSettings(port=port, baudrate=baud)
            result = await discover_port(
                port,
                serial_settings=settings,
                timeout=per_probe_timeout_s,
                sniff_window=sniff_window_s,
            )
            results.append(result)
            if result.ok:
                # First hit per port wins.
                break
            if _is_port_open_failure(result):
                # A port that fails to open at one baud will fail the
                # same way at every other baud — short-circuit.
                break
    return results


def _is_port_open_failure(result: SartoriusDiscoveryResult) -> bool:
    """Identify a port-open exception so the sweep can short-circuit."""
    if result.error is None:
        return False
    from sartoriuslib.errors import SartoriusConnectionError  # noqa: PLC0415

    return isinstance(result.error, SartoriusConnectionError)


async def _resolve_ports(ports: Sequence[str] | None) -> tuple[str, ...]:
    """Resolve ``ports`` to an explicit tuple of port labels."""
    if ports is not None:
        return tuple(ports)
    infos = await anyserial.list_serial_ports()
    return tuple(info.device for info in infos)


def summarize_discovery(
    results: Iterable[SartoriusDiscoveryResult],
) -> list[DiscoverySummary]:
    """Fold per-probe results into one :class:`DiscoverySummary` per port.

    Port order is preserved (first-appearance wins). For each port the
    summary picks the first ``ok`` probe as the winning row; if no probe
    succeeded the port's last probe contributes the failure reason.
    """
    by_port: dict[str, list[SartoriusDiscoveryResult]] = {}
    for r in results:
        by_port.setdefault(r.port, []).append(r)

    summaries: list[DiscoverySummary] = []
    for port, probes in by_port.items():
        elapsed = sum(p.elapsed_s for p in probes)
        hit = next((p for p in probes if p.ok), None)
        if hit is not None:
            summaries.append(
                DiscoverySummary(
                    port=port,
                    ok=True,
                    baudrate=hit.baudrate,
                    protocol=hit.protocol,
                    autoprint_active=hit.autoprint_active,
                    error=None,
                    elapsed_s=elapsed,
                ),
            )
            continue
        last = probes[-1]
        first_error = next((p.error for p in probes if p.error is not None), None)
        summaries.append(
            DiscoverySummary(
                port=port,
                ok=False,
                baudrate=last.baudrate,
                protocol=None,
                autoprint_active=False,
                error=first_error,
                elapsed_s=elapsed,
            ),
        )
    return summaries
