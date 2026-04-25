"""``open_device`` — primary async entry point for the library.

Supports forced xBPI, forced SBI, and ``ProtocolKind.AUTO`` opens.
``open_device`` is the name documented for cross-library uniformity
with ``alicatlib``; :func:`open_balance` is a friendly alias.

The factory owns the transport's *construction* and wires it through
the xBPI protocol client and the :class:`Session` into the returned
:class:`Balance`. The balance's async-context-manager exit closes the
transport. If any step between open and balance-construction fails,
the factory closes the transport before propagating the exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sartoriuslib.devices.balance import Balance
from sartoriuslib.devices.session import Session
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusAutoprintActiveError,
    SartoriusError,
    SartoriusValidationError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.detect import DetectionResult, detect_protocol
from sartoriuslib.protocol.sbi.client import SbiProtocolClient
from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport

__all__ = ["open_balance", "open_device"]


async def open_device(
    port: str | Transport,
    *,
    protocol: ProtocolKind = ProtocolKind.XBPI,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
    strict: bool = False,
    identify: bool = True,
) -> Balance:
    """Open a serial port, wire up the protocol stack, and return a :class:`Balance`.

    Arguments:
        port: Serial-port path (e.g. ``"/dev/ttyUSB0"``) or a pre-built
            :class:`Transport` (useful for tests — supply a
            :class:`FakeTransport` to drive a session without hardware).
        protocol: Which wire protocol to speak. :attr:`AUTO` runs the
            conservative detector from
            :func:`sartoriuslib.protocol.detect_protocol` (passive SBI
            autoprint sniff → xBPI ``0x02`` probe → SBI ``ESC x1_``
            probe → fail clearly) at the caller's serial settings.
        serial_settings: Override the default 8-O-1 @ 9600 baud
            configuration. Ignored when ``port`` is already a
            :class:`Transport`.
        timeout: Per-call default timeout for both transport I/O and
            :class:`XbpiProtocolClient` requests.
        src_sbn: Host xBPI bus address (default ``0x01``).
        dst_sbn: Balance xBPI bus address (default ``0x09`` — factory
            default).
        strict: If ``True``, family / capability prior mismatches refuse
            pre-I/O on the :class:`Session` (design §6.1).
        identify: Run the identify commands on open and cache
            :class:`DeviceInfo` on the balance. Propagates family +
            seeded capabilities back into the session for subsequent
            prior gating.

    Raises:
        SartoriusError: ``AUTO`` detection found no responsive xBPI or
            SBI device on the line.
        SartoriusConnectionError: Transport failed to open.

    Returns:
        A :class:`Balance` async-context-manager. Exiting the context
        closes the transport.
    """
    transport, settings = _resolve_transport(port, serial_settings)
    if not transport.is_open:
        await transport.open()

    try:
        resolved_protocol, detection = await _resolve_protocol(
            protocol,
            transport,
            timeout=timeout,
            src_sbn=src_sbn,
            dst_sbn=dst_sbn,
        )
        xbpi_client, sbi_client = _build_clients(
            resolved_protocol,
            transport,
            timeout=timeout,
        )
        if sbi_client is not None:
            await _prime_sbi_autoprint_state(
                sbi_client,
                detection,
                timeout=timeout,
            )
        session = Session(
            xbpi_client=xbpi_client,
            sbi_client=sbi_client,
            active_protocol=resolved_protocol,
            src_sbn=src_sbn,
            dst_sbn=dst_sbn,
            strict=strict,
            default_timeout=timeout,
            serial_settings=settings,
        )
        balance = Balance(session)
        if identify and session.sbi_autoprint_active:
            raise SartoriusAutoprintActiveError(
                "SBI autoprint is active; identify() replies are not reliable. "
                "Open with identify=False and use stream(mode='autoprint') or poll(), "
                "or disable autoprint on the balance before opening with identify=True.",
                context=ErrorContext(
                    command_name="identify",
                    protocol="sbi",
                    extra={"autoprint_active": True},
                ),
            )
        if identify:
            await balance.identify()
    except BaseException:
        await transport.close()
        raise
    return balance


async def open_balance(
    port: str | Transport,
    *,
    protocol: ProtocolKind = ProtocolKind.XBPI,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
    strict: bool = False,
    identify: bool = True,
) -> Balance:
    """Friendly alias for :func:`open_device`.

    Returns the same :class:`Balance` as :func:`open_device` with
    identical arguments. ``open_device`` is documented as primary for
    cross-library uniformity with ``alicatlib``; ``open_balance`` reads
    more naturally inside the sartoriuslib namespace.
    """
    return await open_device(
        port,
        protocol=protocol,
        serial_settings=serial_settings,
        timeout=timeout,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
        strict=strict,
        identify=identify,
    )


def _resolve_transport(
    port: str | Transport,
    serial_settings: SerialSettings | None,
) -> tuple[Transport, SerialSettings]:
    """Build (or accept) a :class:`Transport` and return the effective settings."""
    if isinstance(port, str):
        settings = serial_settings or SerialSettings(port=port)
        if settings.port != port:
            # Caller supplied both a port and a settings object — trust
            # the settings but verify the path matches to catch mistakes.
            raise SartoriusValidationError(
                f"open_device: port={port!r} does not match serial_settings.port={settings.port!r}",
                context=ErrorContext(extra={"port": port, "settings_port": settings.port}),
            )
        return SerialTransport(settings), settings
    # Pre-built Transport: we don't know its serial settings; use a
    # placeholder so DeviceInfo can still report *something*.
    fallback = serial_settings or SerialSettings(port=port.label)
    return port, fallback


async def _resolve_protocol(
    protocol: ProtocolKind,
    transport: Transport,
    *,
    timeout: float,
    src_sbn: int,
    dst_sbn: int,
) -> tuple[ProtocolKind, DetectionResult | None]:
    """Resolve ``ProtocolKind.AUTO`` via :func:`detect_protocol`, else pass through.

    The returned :class:`DetectionResult` is ``None`` for forced opens
    (caller already specified the protocol) and non-``None`` for
    ``AUTO``. The detector's pending sniffed lines flow back to the
    SBI client through :func:`_prime_sbi_autoprint_state`.
    """
    if protocol is not ProtocolKind.AUTO:
        return protocol, None
    detection = await detect_protocol(
        transport,
        timeout=timeout,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
    )
    return detection.protocol, detection


def _build_clients(
    protocol: ProtocolKind,
    transport: Transport,
    *,
    timeout: float,
) -> tuple[XbpiProtocolClient | None, SbiProtocolClient | None]:
    """Construct the client pair appropriate for the resolved ``protocol``."""
    if protocol is ProtocolKind.XBPI:
        return XbpiProtocolClient(transport, default_timeout=timeout), None
    if protocol is ProtocolKind.SBI:
        return None, SbiProtocolClient(transport, default_timeout=timeout)
    raise SartoriusError(
        f"open_device: unresolved ProtocolKind {protocol!r}",
        context=ErrorContext(extra={"protocol": str(protocol)}),
    )


async def _prime_sbi_autoprint_state(
    sbi_client: SbiProtocolClient,
    detection: DetectionResult | None,
    *,
    timeout: float,
) -> None:
    """Seed the SBI client's autoprint state for forced and AUTO opens.

    Forced SBI opens (``detection is None``) run the client's own
    passive sniff. AUTO opens that already resolved through a sniff
    re-queue the observed line via
    :meth:`SbiProtocolClient.mark_autoprint_active` so the first
    :meth:`Balance.poll` does not lose the sample. AUTO opens that
    resolved through the identity probe leave the client in plain
    command/reply mode.
    """
    if detection is None:
        await sbi_client.detect_autoprint(timeout=min(timeout, 0.25))
        return
    if detection.autoprint_active:
        for line in detection.pending_lines:
            sbi_client.mark_autoprint_active(pending=line)
