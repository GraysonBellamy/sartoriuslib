"""Port discovery + :class:`DiscoveryResult` + :func:`find_devices`.

Two layers, two helpers:

- :func:`discover_port` — open one port at one serial-settings
  configuration, run the conservative
  :func:`sartoriuslib.protocol.detect_protocol` probe, return a
  :class:`DiscoveryResult`. The caller picks the framing.
- :func:`find_devices` — sweep a set of baudrates against one or more
  ports (default: every port :func:`anyserial.list_serial_ports`
  enumerates) and return one :class:`FindResult` per port. Mirrors
  :func:`alicatlib.find_devices` so multi-adapter consumers can render
  every adapter's discovery rows uniformly.

Both helpers are READ_ONLY — neither writes anything beyond what
:func:`detect_protocol` already sends (an xBPI ``READ_MODEL`` and
optionally ``ESC x1_`` / ``ESC P``). No tare, no zero, no autoprint
toggling.

Design reference: ``docs/design.md`` §4.3, §16 Q2.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyserial

from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.transport.base import Transport

__all__ = [
    "DEFAULT_DISCOVERY_BAUDRATES",
    "DiscoveryResult",
    "FindResult",
    "discover_port",
    "find_devices",
]


#: Default baud rates :func:`find_devices` sweeps when the caller doesn't
#: supply ``baudrates=``. Covers every Sartorius family the library has
#: captures for — BCE 9600, MSE 19200, WZA 1200 sits below the floor but
#: is reachable by passing it explicitly. Keep this constant authoritative
#: here so consumers (capa, scripts) don't fork their own copy.
DEFAULT_DISCOVERY_BAUDRATES: tuple[int, ...] = (9600, 19200, 38400, 57600, 115200)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Outcome of probing one port at one serial-settings configuration.

    Attributes:
        port: The port label (path or pre-built transport's label).
        baudrate / parity / stopbits: Effective framing during the probe.
        protocol: Resolved :class:`ProtocolKind` on success, ``None``
            when no responsive device was found.
        autoprint_active: Whether the probe observed unsolicited SBI
            autoprint output during the passive sniff window.
        pending_lines: Sniffed autoprint lines (with CRLF) the caller
            may want to re-queue on a follow-up open.
        error: Human-readable error description when ``protocol is None``.
    """

    port: str
    baudrate: int
    parity: str
    stopbits: int
    protocol: ProtocolKind | None
    autoprint_active: bool = False
    pending_lines: tuple[bytes, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """``True`` when detection resolved to a wire protocol."""
        return self.protocol is not None


async def discover_port(
    port: str | Transport,
    *,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    sniff_window: float = 0.25,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
) -> DiscoveryResult:
    """Open ``port`` at ``serial_settings`` and run the conservative detect.

    Returns a :class:`DiscoveryResult` capturing the chosen framing and
    the detector's verdict. The transport is closed before returning.
    Failures during ``detect_protocol`` (no responsive device, hard
    transport faults) surface in the result's ``error`` field rather
    than being raised — discovery is meant to be safe to call against
    unknown ports without crashing the caller.
    """
    transport, settings = _resolve_transport(port, serial_settings)
    if not transport.is_open:
        await transport.open()
    try:
        try:
            detection = await detect_protocol(
                transport,
                timeout=timeout,
                sniff_window=sniff_window,
                src_sbn=src_sbn,
                dst_sbn=dst_sbn,
            )
        except SartoriusError as exc:
            return DiscoveryResult(
                port=transport.label,
                baudrate=settings.baudrate,
                parity=settings.parity.value,
                stopbits=int(settings.stopbits.value),
                protocol=None,
                error=str(exc),
            )
        return DiscoveryResult(
            port=transport.label,
            baudrate=settings.baudrate,
            parity=settings.parity.value,
            stopbits=int(settings.stopbits.value),
            protocol=detection.protocol,
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


@dataclass(frozen=True, slots=True)
class FindResult:
    """One per-port outcome from :func:`find_devices`.

    Mirrors :class:`alicatlib.FindResult` so multi-adapter consumers
    (capa's Setup-editor Discover dialog, ``capa hardware discover``)
    can render every adapter's discovery rows uniformly.

    On a hit, ``baudrate`` is the value that succeeded and ``protocol``
    is the resolved :class:`ProtocolKind`. On a miss, ``baudrate`` is
    the *last* baud the sweep tried before giving up (or the baud at
    which a port-open failure short-circuited), ``protocol`` is
    ``None``, and ``error`` carries the underlying exception when one
    was raised — port-open failures (e.g. busy port, missing device)
    appear as :class:`SartoriusConnectionError`; silent ports just
    leave ``error`` as ``None``.

    Attributes:
        port: The port label.
        baudrate: Effective baudrate (hit value on success; last tried
            on miss).
        protocol: Resolved :class:`ProtocolKind` on success, ``None``
            on miss.
        ok: ``True`` iff a wire protocol was identified.
        autoprint_active: Whether the probe observed unsolicited SBI
            autoprint output (only meaningful when ``ok=True``).
        error: Underlying exception when a probe raised; ``None``
            otherwise. Programmer-error (``TypeError``) is *not*
            captured here — those still escape.
    """

    port: str
    baudrate: int
    protocol: ProtocolKind | None
    ok: bool
    autoprint_active: bool = False
    error: Exception | None = None


async def find_devices(
    *,
    ports: Sequence[str] | None = None,
    baudrates: Sequence[int] | None = None,
    per_probe_timeout_s: float = 0.5,
    sniff_window_s: float = 0.25,
) -> list[FindResult]:
    """Probe local serial ports for Sartorius balances, sweeping baudrates.

    For each port in ``ports`` (or every port
    :func:`anyserial.list_serial_ports` enumerates when ``ports`` is
    ``None``), call :func:`discover_port` once per baudrate in
    ``baudrates`` (or :data:`DEFAULT_DISCOVERY_BAUDRATES` when ``None``)
    until either:

    - a probe reports ``ok=True`` (first hit wins for that port), or
    - a probe raises (e.g. port busy / device missing) — remaining
      bauds for that port are skipped, since opening the same port at a
      different baud will fail the same way, or
    - every baud has been tried without a hit.

    The function is **read-only**: it only calls :func:`discover_port`,
    which only ever sends the conservative ``READ_MODEL`` / ``ESC x1_``
    /``ESC P`` probes. No tare, no zero, no autoprint toggling.

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
        One :class:`FindResult` per port, in the same order as
        ``ports`` (or the order :func:`anyserial.list_serial_ports`
        produced).
    """
    bauds = tuple(baudrates) if baudrates is not None else DEFAULT_DISCOVERY_BAUDRATES
    port_list = await _resolve_ports(ports)
    return [
        await _find_one_port(
            port,
            bauds=bauds,
            per_probe_timeout_s=per_probe_timeout_s,
            sniff_window_s=sniff_window_s,
        )
        for port in port_list
    ]


async def _resolve_ports(ports: Sequence[str] | None) -> tuple[str, ...]:
    """Resolve ``ports`` to an explicit tuple of port labels.

    ``None`` enumerates via :func:`anyserial.list_serial_ports`.
    """
    if ports is not None:
        return tuple(ports)
    infos = await anyserial.list_serial_ports()
    return tuple(info.device for info in infos)


async def _find_one_port(
    port: str,
    *,
    bauds: tuple[int, ...],
    per_probe_timeout_s: float,
    sniff_window_s: float,
) -> FindResult:
    """Sweep ``bauds`` against ``port``. First hit wins; raises short-circuit.

    On a hit, returns immediately with ``ok=True``. On a raised
    exception (e.g. port-open failure), returns immediately with
    ``ok=False, error=<exc>`` and the baud at which the raise occurred.
    On a clean miss across every baud, returns ``ok=False`` with the
    final baud and the last reported error string (if any) wrapped in
    a :class:`SartoriusError`.
    """
    last_error: Exception | None = None
    last_baud = bauds[-1] if bauds else 0
    for baud in bauds:
        settings = SerialSettings(port=port, baudrate=baud)
        try:
            result = await discover_port(
                port,
                serial_settings=settings,
                timeout=per_probe_timeout_s,
                sniff_window=sniff_window_s,
            )
        except SartoriusError as exc:
            # discover_port already captures detect_protocol failures
            # into ``result.error``; the only way out is a hard
            # transport-open failure. Retrying other bauds against the
            # same port will fail the same way — short-circuit.
            return FindResult(
                port=port,
                baudrate=baud,
                protocol=None,
                ok=False,
                error=exc,
            )
        if result.ok and result.protocol is not None:
            return FindResult(
                port=port,
                baudrate=result.baudrate,
                protocol=result.protocol,
                ok=True,
                autoprint_active=result.autoprint_active,
            )
        last_baud = baud
        if result.error is not None:
            # Preserve the last per-baud miss reason as a typed error.
            last_error = SartoriusError(result.error)
    return FindResult(
        port=port,
        baudrate=last_baud,
        protocol=None,
        ok=False,
        error=last_error,
    )
