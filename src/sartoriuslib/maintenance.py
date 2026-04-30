"""Confirmed, port-level maintenance operations.

One-shot helpers that open a transport, perform a single confirmed
reconfiguration, verify the result, and close — for callers who do not
want to hold a live :class:`~sartoriuslib.devices.balance.Balance`
session. Off the normal :func:`~sartoriuslib.open_device` path; every
function here mutates persistent device state and refuses without
``confirm=True``.

Surfaces, per design doc §6 / §16.6:

- :func:`switch_protocol` — open at the current protocol, call
  :meth:`Balance.configure_protocol`, return the refreshed
  :class:`DeviceInfo`. Wraps the WZA-style host-side flip described in
  ``docs/protocol.md`` §2.1: the user has already changed the device's
  protocol mode (front-panel menu on WZA) and asks the library to
  reconcile the host side at the new framing.
- :func:`set_baud_rate` — send xBPI ``0x5C`` and reopen at the new
  baud, verifying via identity. xBPI-only.
- :func:`write_sbn_address` — send xBPI ``0x72`` and verify by
  reading ``0x71`` back. xBPI-only.

All three return a :class:`DeviceInfo` snapshot from the post-change
identify pass. On any verification failure the underlying
:class:`Balance` session may be left in
:attr:`~sartoriuslib.devices.session.SessionState.BROKEN`; the helper
still propagates the error and closes the transport before returning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sartoriuslib.devices.factory import open_device
from sartoriuslib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from sartoriuslib.devices.models import DeviceInfo
    from sartoriuslib.transport.base import Parity, SerialSettings, StopBits, Transport

__all__ = ["set_baud_rate", "switch_protocol", "write_sbn_address"]


async def switch_protocol(
    port: str | Transport,
    target: ProtocolKind,
    *,
    current_protocol: ProtocolKind = ProtocolKind.AUTO,
    serial_settings: SerialSettings | None = None,
    new_baudrate: int | None = None,
    new_parity: Parity | None = None,
    new_stopbits: StopBits | None = None,
    timeout: float = 1.0,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
    confirm: bool = True,
) -> DeviceInfo:
    """Open ``port``, switch to ``target``, return the refreshed identity.

    Wraps :meth:`Balance.configure_protocol` for callers who only need
    the one-shot reconfigure. The transport is opened at
    ``serial_settings`` (the *current* framing, before the switch),
    :meth:`configure_protocol` then reopens at the new framing and
    verifies. ``new_baudrate`` / ``new_parity`` / ``new_stopbits``
    default to ``None`` (keep the current value).

    ``current_protocol=ProtocolKind.AUTO`` lets
    :func:`~sartoriuslib.open_device` autodetect; pass
    :attr:`ProtocolKind.XBPI` or :attr:`ProtocolKind.SBI` explicitly to
    skip detection and assume the current mode.

    Returns the post-switch :class:`DeviceInfo` (refreshed by
    :meth:`Balance.configure_protocol`'s identify pass) and closes the
    transport before returning.
    """
    bal = await open_device(
        port,
        protocol=current_protocol,
        serial_settings=serial_settings,
        timeout=timeout,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
        # identify=False keeps the open lightweight; configure_protocol's
        # post-switch identify produces the authoritative DeviceInfo.
        identify=False,
    )
    try:
        return await bal.configure_protocol(
            target,
            baudrate=new_baudrate,
            parity=new_parity,
            stopbits=new_stopbits,
            timeout=timeout,
            confirm=confirm,
        )
    finally:
        await bal.close()


async def set_baud_rate(
    port: str | Transport,
    *,
    wire_code: int,
    baudrate: int,
    serial_settings: SerialSettings | None = None,
    parity: Parity | None = None,
    stopbits: StopBits | None = None,
    timeout: float = 1.0,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
    confirm: bool = True,
) -> DeviceInfo:
    """Open ``port`` at xBPI, send ``0x5C``, reopen at ``baudrate``, verify.

    ``wire_code`` is the device-side encoding from
    ``docs/protocol.md`` §7.10 (``0x00=9600``, ``0x01=19200``,
    ``0x02=38400``, ``0x03=57600``); ``baudrate`` is the matching
    host-side baud the transport reopens at after the on-wire write.

    Returns the post-change :class:`DeviceInfo`. Closes the transport
    before returning. xBPI-only — refuses on SBI sessions.
    """
    bal = await open_device(
        port,
        protocol=ProtocolKind.XBPI,
        serial_settings=serial_settings,
        timeout=timeout,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
        identify=False,
    )
    try:
        return await bal.set_baud_rate(
            wire_code,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            confirm=confirm,
        )
    finally:
        await bal.close()


async def write_sbn_address(
    port: str | Transport,
    sbn: int,
    *,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    src_sbn: int = 0x01,
    dst_sbn: int = 0x09,
    confirm: bool = True,
) -> int:
    """Open ``port`` at xBPI, send ``0x72``, return the readback SBN.

    The device's new SBN is read back via ``0x71`` for verification.
    The session's destination SBN is *not* updated — see
    :meth:`Balance.write_sbn_address` for the multidrop opt-in.
    Closes the transport before returning.
    """
    bal = await open_device(
        port,
        protocol=ProtocolKind.XBPI,
        serial_settings=serial_settings,
        timeout=timeout,
        src_sbn=src_sbn,
        dst_sbn=dst_sbn,
        identify=False,
    )
    try:
        return await bal.write_sbn_address(
            sbn,
            timeout=timeout,
            confirm=confirm,
        )
    finally:
        await bal.close()
