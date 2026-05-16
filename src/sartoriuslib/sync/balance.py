"""Sync balance facade — portal-driven wrapper over :class:`Balance`.

Each :class:`SyncBalance` holds a reference to an async
:class:`~sartoriuslib.devices.balance.Balance` and a
:class:`~sartoriuslib.sync.portal.SyncPortal`; every public method is
a one-liner that hands the underlying coroutine to the portal.

The :class:`Sartorius` namespace exposes a ``Sartorius.open(...)``
context manager that drives the async
:func:`~sartoriuslib.devices.factory.open_device` through the portal.

Design reference: ``docs/design.md`` §9.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from sartoriuslib.devices.factory import open_device
from sartoriuslib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Generator

    from sartoriuslib.devices.balance import Balance, SartoriusDeviceSnapshot
    from sartoriuslib.devices.models import (
        BalanceStatus,
        CalRecord,
        DeviceInfo,
        ParameterEntry,
        Quantity,
        Reading,
        TemperatureReading,
    )
    from sartoriuslib.devices.session import Session
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame
    from sartoriuslib.registry.modes import (
        AutoZeroMode,
        FilterMode,
        IsoCalMode,
        MenuAccessMode,
        TareBehavior,
    )
    from sartoriuslib.registry.units import Unit
    from sartoriuslib.transport.base import Parity, SerialSettings, StopBits, Transport

__all__ = ["Sartorius", "SyncBalance"]


class SyncBalance:
    """Blocking facade over :class:`sartoriuslib.devices.balance.Balance`.

    Instances are produced by :meth:`Sartorius.open` or yielded by the
    sync manager; users do not call this constructor directly.
    """

    def __init__(self, balance: Balance, portal: SyncPortal) -> None:
        self._bal = balance
        self._portal = portal

    # ------------------------------------------------------------------ props

    @property
    def info(self) -> DeviceInfo | None:
        """Identity snapshot — passes through :attr:`Balance.info`."""
        return self._bal.info

    @property
    def session(self) -> Session:
        """Underlying async :class:`Session` (advanced escape-hatch)."""
        return self._bal.session

    @property
    def portal(self) -> SyncPortal:
        """The :class:`SyncPortal` this balance routes coroutines through."""
        return self._portal

    # ------------------------------------------------------------------ weight reads

    def poll(self) -> Reading:
        """Blocking :meth:`Balance.poll`."""
        return self._portal.call(self._bal.poll)

    def read_net(self, *, hires: int = 0) -> Reading:
        """Blocking :meth:`Balance.read_net`."""
        return self._portal.call(self._bal.read_net, hires=hires)

    def read_gross(self, *, hires: int = 0) -> Reading:
        """Blocking :meth:`Balance.read_gross`."""
        return self._portal.call(self._bal.read_gross, hires=hires)

    def read_tare_value(self) -> Reading:
        """Blocking :meth:`Balance.read_tare_value`."""
        return self._portal.call(self._bal.read_tare_value)

    def refresh_sbi_autoprint_state(self, *, timeout: float | None = None) -> bool:
        """Blocking :meth:`Balance.refresh_sbi_autoprint_state`."""
        return self._portal.call(
            self._bal.refresh_sbi_autoprint_state,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ tare / zero

    def tare(self) -> None:
        """Blocking :meth:`Balance.tare`."""
        self._portal.call(self._bal.tare)

    def zero(self) -> None:
        """Blocking :meth:`Balance.zero`."""
        self._portal.call(self._bal.zero)

    # ------------------------------------------------------------------ status / identity

    def status(self) -> BalanceStatus:
        """Blocking :meth:`Balance.status`."""
        return self._portal.call(self._bal.status)

    def identify(self) -> DeviceInfo:
        """Blocking :meth:`Balance.identify`."""
        return self._portal.call(self._bal.identify)

    def snapshot(self) -> SartoriusDeviceSnapshot:
        """Blocking :meth:`Balance.snapshot`."""
        return self._portal.call(self._bal.snapshot)

    # ------------------------------------------------------------------ metrology

    def capacity(self, area: int = 0) -> Quantity:
        """Blocking :meth:`Balance.capacity`."""
        return self._portal.call(self._bal.capacity, area)

    def increment(self, area: int = 0) -> Quantity:
        """Blocking :meth:`Balance.increment`."""
        return self._portal.call(self._bal.increment, area)

    def temperature(self, sensor: int = 0) -> TemperatureReading:
        """Blocking :meth:`Balance.temperature`."""
        return self._portal.call(self._bal.temperature, sensor)

    def discover_temperature_sensors(self, *, max_index: int = 8) -> tuple[int, ...]:
        """Blocking :meth:`Balance.discover_temperature_sensors`."""
        return self._portal.call(
            self._bal.discover_temperature_sensors,
            max_index=max_index,
        )

    # ------------------------------------------------------------------ parameters

    def read_parameter(self, index: int) -> ParameterEntry:
        """Blocking :meth:`Balance.read_parameter`."""
        return self._portal.call(self._bal.read_parameter, index)

    def write_parameter(self, index: int, value: int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.write_parameter`."""
        self._portal.call(self._bal.write_parameter, index, value, confirm=confirm)

    # ------------------------------------------------------------------ typed parameter accessors

    def get_filter_mode(self) -> FilterMode:
        """Blocking :meth:`Balance.get_filter_mode`."""
        return self._portal.call(self._bal.get_filter_mode)

    def set_filter_mode(self, mode: FilterMode | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_filter_mode`."""
        self._portal.call(self._bal.set_filter_mode, mode, confirm=confirm)

    def get_display_unit(self) -> Unit:
        """Blocking :meth:`Balance.get_display_unit`."""
        return self._portal.call(self._bal.get_display_unit)

    def set_display_unit(self, unit: Unit | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_display_unit`."""
        self._portal.call(self._bal.set_display_unit, unit, confirm=confirm)

    def get_auto_zero(self) -> AutoZeroMode:
        """Blocking :meth:`Balance.get_auto_zero`."""
        return self._portal.call(self._bal.get_auto_zero)

    def set_auto_zero(self, mode: AutoZeroMode | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_auto_zero`."""
        self._portal.call(self._bal.set_auto_zero, mode, confirm=confirm)

    def get_isocal_mode(self) -> IsoCalMode:
        """Blocking :meth:`Balance.get_isocal_mode`."""
        return self._portal.call(self._bal.get_isocal_mode)

    def set_isocal_mode(self, mode: IsoCalMode | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_isocal_mode`."""
        self._portal.call(self._bal.set_isocal_mode, mode, confirm=confirm)

    def get_tare_behavior(self) -> TareBehavior:
        """Blocking :meth:`Balance.get_tare_behavior`."""
        return self._portal.call(self._bal.get_tare_behavior)

    def set_tare_behavior(self, mode: TareBehavior | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_tare_behavior`."""
        self._portal.call(self._bal.set_tare_behavior, mode, confirm=confirm)

    def get_menu_access(self) -> MenuAccessMode:
        """Blocking :meth:`Balance.get_menu_access`."""
        return self._portal.call(self._bal.get_menu_access)

    def set_menu_access(self, mode: MenuAccessMode | str | int, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.set_menu_access`."""
        self._portal.call(self._bal.set_menu_access, mode, confirm=confirm)

    # ------------------------------------------------------------------ EEPROM persistence

    def save_menu(self, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.save_menu`."""
        self._portal.call(self._bal.save_menu, confirm=confirm)

    def reload_menu(self, *, confirm: bool = False) -> None:
        """Blocking :meth:`Balance.reload_menu`."""
        self._portal.call(self._bal.reload_menu, confirm=confirm)

    # ------------------------------------------------------------------ calibration

    def last_cal_record(self) -> CalRecord:
        """Blocking :meth:`Balance.last_cal_record`."""
        return self._portal.call(self._bal.last_cal_record)

    def internal_adjust(
        self,
        *,
        cal_type: int | None = None,
        confirm: bool = False,
    ) -> None:
        """Blocking :meth:`Balance.internal_adjust`."""
        self._portal.call(
            self._bal.internal_adjust,
            cal_type=cal_type,
            confirm=confirm,
        )

    # ------------------------------------------------------------------ lifecycle ops

    def configure_protocol(
        self,
        target: ProtocolKind,
        *,
        baudrate: int | None = None,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> DeviceInfo:
        """Blocking :meth:`Balance.configure_protocol`."""
        return self._portal.call(
            self._bal.configure_protocol,
            target,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            confirm=confirm,
        )

    def set_baud_rate(
        self,
        wire_code: int,
        *,
        baudrate: int,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> DeviceInfo:
        """Blocking :meth:`Balance.set_baud_rate`."""
        return self._portal.call(
            self._bal.set_baud_rate,
            wire_code,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            confirm=confirm,
        )

    def write_sbn_address(
        self,
        sbn: int,
        *,
        update_session_dst: bool = False,
        timeout: float | None = None,
        confirm: bool = False,
    ) -> int:
        """Blocking :meth:`Balance.write_sbn_address`."""
        return self._portal.call(
            self._bal.write_sbn_address,
            sbn,
            update_session_dst=update_session_dst,
            timeout=timeout,
            confirm=confirm,
        )

    # ------------------------------------------------------------------ escape hatch

    def raw_xbpi(
        self,
        opcode: int,
        args: bytes = b"",
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> XbpiFrame:
        """Blocking :meth:`Balance.raw_xbpi`."""
        return self._portal.call(
            self._bal.raw_xbpi,
            opcode,
            args,
            confirm=confirm,
            timeout=timeout,
        )

    def raw_sbi(
        self,
        command: bytes | str,
        *,
        confirm: bool = False,
        timeout: float | None = None,
        expect_lines: int = 1,
    ) -> SbiReply:
        """Blocking :meth:`Balance.raw_sbi`."""
        return self._portal.call(
            self._bal.raw_sbi,
            command,
            confirm=confirm,
            timeout=timeout,
            expect_lines=expect_lines,
        )


def wrap_balance(balance: Balance, portal: SyncPortal) -> SyncBalance:
    """Return a :class:`SyncBalance` wrapping ``balance`` on ``portal``.

    Package-private helper used by :class:`SyncSartoriusManager`.
    """
    return SyncBalance(balance, portal)


def unwrap_sync_balance[T](source: T | SyncBalance) -> T | Balance:
    """Return the async :class:`Balance` inside ``source`` if wrapped.

    Package-private helper used by :class:`SyncSartoriusManager`.
    """
    if isinstance(source, SyncBalance):
        return source._bal  # pyright: ignore[reportPrivateUsage]
    return source


class Sartorius:
    """Namespace for the sync balance entry point.

    Use :meth:`Sartorius.open` as a context manager::

        from sartoriuslib.sync import Sartorius

        with Sartorius.open("/dev/ttyUSB0") as bal:
            print(bal.poll())
    """

    @staticmethod
    @contextmanager
    def open(
        port: str | Transport,
        *,
        protocol: ProtocolKind | None = None,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
        src_sbn: int = 0x01,
        dst_sbn: int = 0x09,
        strict: bool = False,
        identify: bool = True,
        portal: SyncPortal | None = None,
    ) -> Generator[SyncBalance]:
        """Open a sync :class:`SyncBalance` scoped to a ``with`` block.

        Mirrors :func:`sartoriuslib.open_device` parameter-for-
        parameter (modulo the portal plumbing). The sync CM drives
        the async factory through a :class:`SyncPortal`; the portal
        is created per-call unless one is passed in via ``portal=``.
        """
        # Local import keeps the ProtocolKind value available at runtime
        # (the top-level import is guarded by TYPE_CHECKING).
        from sartoriuslib.protocol.base import ProtocolKind as _ProtocolKind  # noqa: PLC0415

        effective_protocol = protocol if protocol is not None else _ProtocolKind.XBPI

        with ExitStack() as stack:
            active_portal = portal if portal is not None else stack.enter_context(SyncPortal())
            balance = active_portal.call(
                open_device,
                port,
                protocol=effective_protocol,
                serial_settings=serial_settings,
                timeout=timeout,
                src_sbn=src_sbn,
                dst_sbn=dst_sbn,
                strict=strict,
                identify=identify,
            )
            try:
                yield wrap_balance(balance, active_portal)
            finally:
                # Close the underlying transport through the portal;
                # the Balance's close closes the transport it was
                # constructed against.
                active_portal.call(balance.close)
