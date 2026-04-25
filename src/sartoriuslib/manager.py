"""Multi-balance orchestrator — :class:`SartoriusManager`.

The manager coordinates many :class:`~sartoriuslib.devices.balance.Balance`
instances across one or more serial ports. Operations across different
physical ports run concurrently through
:func:`anyio.create_task_group`; operations against the same port
serialise through that port's shared
:class:`~sartoriuslib.protocol.xbpi.client.XbpiProtocolClient` lock.

Port identity is **canonicalised** before comparison so a balance
referenced via both ``/dev/ttyUSB0`` and ``/dev/serial/by-id/...``
(or ``COM3`` and ``com3`` on Windows) collapses to one client —
critical for the single-in-flight invariant. Pre-built
:class:`Transport` sources use the object's :func:`id` as the key so
caller-owned transports aren't accidentally shared.

Error handling is controlled by :class:`ErrorPolicy`:

- :attr:`ErrorPolicy.RAISE` — manager collects all results, and if any
  balance failed, raises an :class:`ExceptionGroup` after the task
  group joins.
- :attr:`ErrorPolicy.RETURN` — every balance produces a
  :class:`DeviceResult` container; callers inspect ``.error``.

Resource lifecycle goes through an internal tracking structure that
unwinds LIFO on :meth:`close` or ``__aexit__``. Per-port clients are
ref-counted so the last :meth:`remove` on a shared port triggers the
transport close.

Design reference: ``docs/design.md`` §11.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from sartoriuslib._logging import get_logger
from sartoriuslib.devices.balance import Balance
from sartoriuslib.devices.session import Session
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusValidationError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi.client import SbiProtocolClient
from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from types import TracebackType
    from typing import Self

    from sartoriuslib.commands.base import Command
    from sartoriuslib.devices.models import Reading
    from sartoriuslib.transport.base import Transport

__all__ = [
    "BalanceManager",
    "DeviceResult",
    "ErrorPolicy",
    "SartoriusManager",
]


_logger = get_logger("manager")


class ErrorPolicy(Enum):
    """How the manager surfaces per-device failures.

    Under :attr:`RAISE`, the manager collects every balance's result
    and — if any call failed — raises an :class:`ExceptionGroup`
    containing the per-device exceptions after the task group joins.
    Under :attr:`RETURN`, each balance produces a :class:`DeviceResult`
    and the caller inspects ``.error`` per entry.
    """

    RAISE = "raise"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class DeviceResult[T]:
    """Per-device result container — value **or** error, never both.

    :attr:`protocol` is populated by :class:`SartoriusManager` from the
    balance's session so error samples from the
    :mod:`~sartoriuslib.streaming` layer can still record which
    protocol produced the failure. Non-manager
    :class:`~sartoriuslib.streaming.PollSource` stubs may leave it
    ``None``.
    """

    value: T | None
    error: SartoriusError | None
    protocol: ProtocolKind | None = None

    @property
    def ok(self) -> bool:
        """``True`` when the balance produced a value (``error is None``)."""
        return self.error is None


# ---------------------------------------------------------------------------
# Port canonicalization
# ---------------------------------------------------------------------------


_WINDOWS_DEVICE_PREFIX = "\\\\.\\"


def _canonical_port_key(port: str) -> str:
    r"""Collapse equivalent port names to a single key.

    POSIX: follows symlinks via :func:`os.path.realpath` so
    ``/dev/ttyUSB0`` and ``/dev/serial/by-id/...-if00-port0`` resolve
    to the same physical device. Falls back to the raw string if the
    path doesn't exist (useful under test fixtures).

    Windows: strips the ``\\.\`` device-namespace prefix and
    uppercases, so ``COM3`` / ``com3`` / ``\\.\COM3`` all match.

    Not used for pre-built :class:`~sartoriuslib.transport.base.Transport`
    sources — those use :func:`id` as the key (the caller has already
    expressed ownership).
    """
    if sys.platform == "win32":
        return port.removeprefix(_WINDOWS_DEVICE_PREFIX).upper()
    return os.path.realpath(port) if Path(port).exists() else port


# ---------------------------------------------------------------------------
# Internal tracking structures
# ---------------------------------------------------------------------------


def _empty_refs() -> set[str]:
    return set()


@dataclass(slots=True)
class _PortEntry:
    """Ref-counted per-port resources shared across balances on the bus.

    ``client`` is the shared protocol client; every balance on the same
    bus runs through it so its lock serialises I/O across balance objects.
    """

    key: str
    transport: Transport
    client: XbpiProtocolClient | SbiProtocolClient | None
    owns_transport: bool
    protocol: ProtocolKind | None = None
    refs: set[str] = field(default_factory=_empty_refs)


@dataclass(slots=True)
class _DeviceEntry:
    """One managed :class:`Balance` + its port ref.

    ``port_key`` is ``None`` when ``source`` was a pre-built
    :class:`Balance` and the caller retains full lifecycle ownership;
    the manager's teardown path is a no-op for those entries.
    """

    name: str
    balance: Balance
    port_key: str | None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SartoriusManager:
    """Coordinator for many balances across one or more serial ports.

    Operations run concurrently across different physical ports (via
    :func:`anyio.create_task_group`) and serialise on the same-port
    client lock. Per-balance failures are surfaced per
    :attr:`error_policy`:

    - :attr:`ErrorPolicy.RAISE`: the manager still collects results
      from every balance, then raises an :class:`ExceptionGroup` if
      any failed.
    - :attr:`ErrorPolicy.RETURN`: the mapping's values carry
      :class:`DeviceResult` containers with ``.value`` or ``.error``.

    Usage::

        async with SartoriusManager() as mgr:
            await mgr.add("bal1", "/dev/ttyUSB0")
            await mgr.add("bal2", "/dev/ttyUSB1")
            readings = await mgr.poll()
    """

    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None:
        self._error_policy = error_policy
        self._devices: dict[str, _DeviceEntry] = {}
        self._ports: dict[str, _PortEntry] = {}
        self._state_lock = anyio.Lock()
        self._closed = False

    # ------------------------------------------------------------------ props

    @property
    def error_policy(self) -> ErrorPolicy:
        """The :class:`ErrorPolicy` this manager was constructed with."""
        return self._error_policy

    @property
    def names(self) -> tuple[str, ...]:
        """Insertion-ordered tuple of managed balance names."""
        return tuple(self._devices.keys())

    @property
    def closed(self) -> bool:
        """``True`` once :meth:`close` has been called."""
        return self._closed

    # ----------------------------------------------------------- context manager

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()

    # ----------------------------------------------------------------- add/remove

    async def add(
        self,
        name: str,
        source: Balance | str | Transport,
        *,
        protocol: ProtocolKind = ProtocolKind.XBPI,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
        src_sbn: int = 0x01,
        dst_sbn: int = 0x09,
        strict: bool = False,
        identify: bool = True,
    ) -> Balance:
        """Register and open a balance under ``name``.

        The ``source`` discriminates lifecycle ownership:

        - :class:`Balance` — pre-built (via :func:`open_device` outside
          the manager). The manager only tracks the name mapping; it
          does *not* take lifecycle ownership.
        - ``str`` — serial port path (``"/dev/ttyUSB0"``, ``"COM3"``).
          The manager creates a :class:`SerialTransport`,
          canonicalises the port key, and shares the transport across
          balances on the same bus. Mixing xBPI and SBI sessions on a shared
          physical port is refused; one serial link has one active protocol.
        - :class:`Transport` — duck-typed transport. The manager
          invokes :func:`open_device` against it but does *not* take
          transport ownership.

        Args:
            name: Unique manager-level identifier.
            source: One of the three lifecycle shapes above.
            protocol: Which wire protocol to speak (per
                :func:`sartoriuslib.open_device`). Ignored when
                ``source`` is a pre-built :class:`Balance`.
            serial_settings: Override default serial framing. Only
                honoured when ``source`` is a port-string.
            timeout: Per-call default timeout.
            src_sbn: Host xBPI bus address.
            dst_sbn: Balance xBPI bus address.
            strict: Strict prior gating (see design §6.1).
            identify: Run identify on open and cache :class:`DeviceInfo`.

        Returns:
            The opened :class:`Balance`.

        Raises:
            SartoriusValidationError: ``name`` already exists or an
                invalid combination of kwargs was supplied.
            SartoriusConnectionError: Manager is closed.
        """
        async with self._state_lock:
            self._check_open()
            if name in self._devices:
                raise SartoriusValidationError(
                    f"manager: name {name!r} already in use",
                    context=ErrorContext(extra={"name": name}),
                )
            if serial_settings is not None and not isinstance(source, str):
                raise SartoriusValidationError(
                    "manager.add(serial_settings=...) only applies to string port "
                    "sources; pre-built Transport / Balance carry their own settings",
                    context=ErrorContext(extra={"name": name}),
                )

            port_key, port_entry, balance = await self._resolve_source(
                source,
                protocol=protocol,
                serial_settings=serial_settings,
                timeout=timeout,
                src_sbn=src_sbn,
                dst_sbn=dst_sbn,
                strict=strict,
                identify=identify,
            )

            self._devices[name] = _DeviceEntry(
                name=name,
                balance=balance,
                port_key=port_key,
            )
            if port_entry is not None:
                port_entry.refs.add(name)

            info = balance.info
            _logger.info(
                "manager.add",
                extra={
                    "device_name": name,
                    "port_key": port_key,
                    "model": info.model if info is not None else None,
                    "protocol": balance.session.active_protocol.value,
                },
            )
            return balance

    async def remove(self, name: str) -> None:
        """Unregister and close the balance named ``name``.

        If ``name`` was the last balance on a shared port, the
        transport for that port is closed too. A pre-built
        :class:`Balance` source is only dropped from the manager's
        registry — the caller retains lifecycle ownership.
        """
        async with self._state_lock:
            self._check_open()
            if name not in self._devices:
                raise SartoriusValidationError(
                    f"manager: no balance named {name!r}",
                    context=ErrorContext(extra={"name": name}),
                )
            entry = self._devices.pop(name)
            await self._teardown_device(entry)
            _logger.info("manager.remove", extra={"device_name": name})

    def get(self, name: str) -> Balance:
        """Return the balance registered under ``name``."""
        try:
            return self._devices[name].balance
        except KeyError:
            raise SartoriusValidationError(
                f"manager: no balance named {name!r}",
                context=ErrorContext(extra={"name": name}),
            ) from None

    async def close(self) -> None:
        """Tear down every managed balance and port (LIFO)."""
        async with self._state_lock:
            if self._closed:
                return
            for name in reversed(list(self._devices.keys())):
                entry = self._devices.pop(name)
                try:
                    await self._teardown_device(entry)
                except Exception as err:
                    _logger.warning(
                        "manager.close_device_failed",
                        extra={"device_name": name, "error": repr(err)},
                    )
            self._closed = True

    # --------------------------------------------------------------- concurrent I/O

    async def poll(
        self,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[Reading]]:
        """Poll every (or named) balance concurrently across ports.

        Returns a mapping from balance name to :class:`DeviceResult`
        even under :attr:`ErrorPolicy.RAISE` — but under that policy,
        any failed balance's error is re-raised as an
        :class:`ExceptionGroup` after all balances have completed.
        """
        targets = self._resolve_names(names)

        async def _poll(balance: Balance) -> Reading:
            return await balance.poll()

        return await self._dispatch("poll", targets, _poll)

    async def execute[Req, Resp](
        self,
        command: Command[Req, Resp],
        requests_by_name: Mapping[str, Req],
    ) -> Mapping[str, DeviceResult[Resp]]:
        """Dispatch a per-device ``Command`` across the requested names.

        ``requests_by_name`` chooses both which balances participate and
        what arguments each gets — supporting the common case of
        "same command, different argument per balance".
        """
        for name in requests_by_name:
            if name not in self._devices:
                raise SartoriusValidationError(
                    f"manager.execute: no balance named {name!r}",
                    context=ErrorContext(command_name=command.name, extra={"name": name}),
                )
        targets = tuple(requests_by_name.keys())
        name_by_balance_id = {id(entry.balance): entry.name for entry in self._devices.values()}

        async def _execute(balance: Balance) -> Resp:
            return await balance.session.execute(
                command,
                requests_by_name[name_by_balance_id[id(balance)]],
            )

        return await self._dispatch(command.name, targets, _execute)

    # ---------------------------------------------------------------- internals

    def _check_open(self) -> None:
        if self._closed:
            raise SartoriusConnectionError(
                "manager is closed",
                context=ErrorContext(extra={"closed": True}),
            )

    def _resolve_names(self, names: Sequence[str] | None) -> tuple[str, ...]:
        if names is None:
            return tuple(self._devices.keys())
        targets = tuple(names)
        unknown = [n for n in targets if n not in self._devices]
        if unknown:
            raise SartoriusValidationError(
                f"manager: unknown balance name(s) {sorted(unknown)!r}",
                context=ErrorContext(extra={"unknown": sorted(unknown)}),
            )
        return targets

    async def _resolve_source(
        self,
        source: Balance | str | Transport,
        *,
        protocol: ProtocolKind,
        serial_settings: SerialSettings | None,
        timeout: float,
        src_sbn: int,
        dst_sbn: int,
        strict: bool,
        identify: bool,
    ) -> tuple[str | None, _PortEntry | None, Balance]:
        """Map ``source`` to ``(port_key, port_entry, balance)``.

        - Pre-built :class:`Balance`: ``port_key`` / ``port_entry``
          are ``None`` — the manager does not own the balance's
          lifecycle.
        - ``str``: canonicalise the path, share or create the port's
          transport *and* :class:`XbpiProtocolClient`, and attach a
          fresh :class:`Balance` at the caller's SBN. Same-port
          balances inherit the one client so its lock serialises I/O
          across them.
        - :class:`Transport`: keyed by ``id``; no sharing; transport
          stays the caller's responsibility.
        """
        if isinstance(source, Balance):
            return None, None, source

        if isinstance(source, str):
            port_key = _canonical_port_key(source)
        else:
            # Duck-typed Transport.
            port_key = f"transport:{id(source)}"

        port_entry = self._ports.get(port_key)
        fresh_port = port_entry is None
        if port_entry is None:
            if isinstance(source, str):
                settings = (
                    serial_settings if serial_settings is not None else SerialSettings(port=source)
                )
                transport: Transport = SerialTransport(settings)
                owns_transport = True
            else:
                transport = source
                owns_transport = False
            port_entry = _PortEntry(
                key=port_key,
                transport=transport,
                client=None,
                owns_transport=owns_transport,
            )
            self._ports[port_key] = port_entry

        try:
            balance = await self._open_balance_on_port(
                port_entry,
                protocol=protocol,
                serial_settings=serial_settings if isinstance(source, str) else None,
                timeout=timeout,
                src_sbn=src_sbn,
                dst_sbn=dst_sbn,
                strict=strict,
                identify=identify,
            )
        except BaseException:
            if fresh_port and not port_entry.refs:
                await self._maybe_teardown_port(port_key, port_entry)
            raise
        return port_key, port_entry, balance

    async def _open_balance_on_port(
        self,
        port_entry: _PortEntry,
        *,
        protocol: ProtocolKind,
        serial_settings: SerialSettings | None,
        timeout: float,
        src_sbn: int,
        dst_sbn: int,
        strict: bool,
        identify: bool,
    ) -> Balance:
        """Build a :class:`Balance` against ``port_entry``'s shared client.

        Mirrors :func:`sartoriuslib.devices.factory.open_device` but
        reuses the per-port :class:`XbpiProtocolClient` so all balances
        on the bus share one I/O-serialising lock.
        """
        if protocol is ProtocolKind.AUTO:
            raise SartoriusValidationError(
                "manager.add: ProtocolKind.AUTO is not supported here; "
                "open the balance with open_device(..., protocol=AUTO) and "
                "register the resulting Balance via manager.add(name, balance)",
                context=ErrorContext(extra={"protocol": "auto"}),
            )

        transport = port_entry.transport
        if not transport.is_open:
            await transport.open()

        if port_entry.protocol is not None and port_entry.protocol is not protocol:
            raise SartoriusValidationError(
                "manager.add: cannot mix xBPI and SBI sessions on the same port",
                context=ErrorContext(
                    extra={
                        "existing_protocol": port_entry.protocol.value,
                        "requested_protocol": protocol.value,
                    },
                ),
            )

        if port_entry.client is None:
            if protocol is ProtocolKind.XBPI:
                port_entry.client = XbpiProtocolClient(
                    transport,
                    default_timeout=timeout,
                )
            else:
                port_entry.client = SbiProtocolClient(
                    transport,
                    default_timeout=timeout,
                )
            port_entry.protocol = protocol

        xbpi_client = (
            port_entry.client
            if protocol is ProtocolKind.XBPI and isinstance(port_entry.client, XbpiProtocolClient)
            else None
        )
        sbi_client = (
            port_entry.client
            if protocol is ProtocolKind.SBI and isinstance(port_entry.client, SbiProtocolClient)
            else None
        )
        session_settings = serial_settings or SerialSettings(port=transport.label)
        session = Session(
            xbpi_client=xbpi_client,
            sbi_client=sbi_client,
            active_protocol=protocol,
            src_sbn=src_sbn,
            dst_sbn=dst_sbn,
            strict=strict,
            default_timeout=timeout,
            serial_settings=session_settings,
        )
        balance = Balance(session)
        if identify:
            await balance.identify()
        return balance

    async def _teardown_device(self, entry: _DeviceEntry) -> None:
        """Release a balance's port ref, closing the transport on last ref.

        ``Balance.aclose()`` would close the underlying transport,
        which is shared across balances on one RS-485 bus. Instead of
        calling it per-balance, the manager releases the port ref and
        only closes the transport via :meth:`_maybe_teardown_port`
        once no balances remain on that port. Pre-built
        :class:`Balance` sources (``owns_lifecycle=False``) have no
        port entry — the caller keeps full lifecycle responsibility.
        """
        if entry.port_key is None:
            return
        port_entry = self._ports.get(entry.port_key)
        if port_entry is None:
            return
        port_entry.refs.discard(entry.name)
        if not port_entry.refs:
            await self._maybe_teardown_port(entry.port_key, port_entry)

    async def _maybe_teardown_port(self, port_key: str, port_entry: _PortEntry) -> None:
        if port_entry.owns_transport:
            try:
                if port_entry.transport.is_open:
                    await port_entry.transport.close()
            except Exception as err:
                _logger.warning(
                    "manager.close_port_failed",
                    extra={"port_key": port_key, "error": repr(err)},
                )
        self._ports.pop(port_key, None)

    async def _dispatch[T](
        self,
        label: str,
        names: Sequence[str],
        op: Callable[[Balance], Awaitable[T]],
    ) -> Mapping[str, DeviceResult[T]]:
        """Run ``op(balance)`` across ``names`` with port-aware concurrency."""
        results: dict[str, DeviceResult[T]] = {}
        errors: list[SartoriusError] = []
        groups: dict[str, list[str]] = {}
        for n in names:
            entry = self._devices[n]
            port_key = entry.port_key if entry.port_key is not None else f"solo:{n}"
            groups.setdefault(port_key, []).append(n)

        async def _run_group(member_names: list[str]) -> None:
            for member in member_names:
                entry = self._devices[member]
                balance = entry.balance
                protocol = balance.session.active_protocol
                try:
                    value: T = await op(balance)
                except SartoriusError as err:
                    results[member] = DeviceResult(value=None, error=err, protocol=protocol)
                    errors.append(err)
                else:
                    results[member] = DeviceResult(
                        value=value,
                        error=None,
                        protocol=protocol,
                    )

        async with anyio.create_task_group() as tg:
            for member_names in groups.values():
                tg.start_soon(_run_group, member_names)

        if self._error_policy is ErrorPolicy.RAISE and errors:
            raise ExceptionGroup(f"manager.{label}: one or more balances failed", errors)
        return results


# ``BalanceManager`` is a readable alias for ``SartoriusManager`` so
# callers can pick whichever name reads better at the call site.
BalanceManager = SartoriusManager
