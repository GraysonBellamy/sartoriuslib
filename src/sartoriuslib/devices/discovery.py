"""Port discovery + :class:`DiscoveryResult`.

Wide baud/parity sweeps live here — **not** in ``open_device``. See
design doc §4.3 and §16 Q2.

A single helper, :func:`discover_port`, opens the caller's transport at
the given serial settings, runs the conservative
:func:`sartoriuslib.protocol.detect_protocol` probe, and returns a
:class:`DiscoveryResult`. Wider serial-settings probing is deferred —
the design doc explicitly leans toward "user supplies serial params"
(§16 Q2) and adding sweeps now would commit to a sweep order before
field evidence shapes it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.transport.base import Transport

__all__ = ["DiscoveryResult", "discover_port"]


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
