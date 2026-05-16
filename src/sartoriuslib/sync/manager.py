"""Sync manager facade — portal-driven wrapper over :class:`SartoriusManager`.

:class:`SyncSartoriusManager` wraps the async
:class:`~sartoriuslib.manager.SartoriusManager` through a
:class:`~sartoriuslib.sync.portal.SyncPortal`. Every coroutine method
becomes a blocking method here; the synchronous :meth:`get` stays
synchronous and delegates directly.

Lifecycle mirrors the async side: the class is a ``with`` context
manager. By default each instance owns its own portal; callers that
need several facades to share one event loop can pass ``portal=`` to
reuse a long-lived :class:`SyncPortal`.

Design reference: ``docs/design.md`` §9 and §11.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Self

from sartoriuslib.manager import DeviceResult, ErrorPolicy, SartoriusManager
from sartoriuslib.sync.balance import SyncBalance, unwrap_sync_balance, wrap_balance
from sartoriuslib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

    from sartoriuslib.commands.base import Command
    from sartoriuslib.devices.balance import Balance
    from sartoriuslib.devices.models import Reading
    from sartoriuslib.protocol.base import ProtocolKind
    from sartoriuslib.transport.base import SerialSettings, Transport

__all__ = [
    "DeviceResult",
    "ErrorPolicy",
    "SyncSartoriusManager",
]


class SyncSartoriusManager:
    """Blocking facade over :class:`sartoriuslib.manager.SartoriusManager`."""

    def __init__(
        self,
        *,
        error_policy: ErrorPolicy = ErrorPolicy.RAISE,
        portal: SyncPortal | None = None,
    ) -> None:
        self._error_policy = error_policy
        self._portal_override = portal
        self._stack: ExitStack | None = None
        self._portal: SyncPortal | None = None
        self._mgr: SartoriusManager | None = None
        self._wrapped: dict[str, SyncBalance] = {}
        self._entered = False

    # --------------------------------------------------------------- properties

    @property
    def error_policy(self) -> ErrorPolicy:
        """The :class:`ErrorPolicy` this manager was constructed with."""
        return self._error_policy

    @property
    def names(self) -> tuple[str, ...]:
        """Insertion-ordered tuple of managed balance names."""
        mgr = self._mgr
        if mgr is None:
            return ()
        return mgr.names

    @property
    def closed(self) -> bool:
        """``True`` once :meth:`close` or ``__exit__`` has run."""
        mgr = self._mgr
        return mgr is None or mgr.closed

    @property
    def portal(self) -> SyncPortal:
        """The :class:`SyncPortal` this manager's coroutines run on."""
        portal = self._portal
        if portal is None:
            raise RuntimeError("SyncSartoriusManager is not open")
        return portal

    # --------------------------------------------------------------- lifecycle

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("SyncSartoriusManager is not reusable after exit")
        self._entered = True
        stack = ExitStack()
        try:
            portal = (
                self._portal_override
                if self._portal_override is not None
                else stack.enter_context(SyncPortal())
            )
            mgr = SartoriusManager(error_policy=self._error_policy)
            stack.enter_context(portal.wrap_async_context_manager(mgr))
            self._portal = portal
            self._mgr = mgr
            self._stack = stack
        except BaseException:
            stack.close()
            self._portal = None
            self._mgr = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        self._wrapped.clear()
        self._mgr = None
        self._portal = None
        if stack is not None:
            stack.__exit__(exc_type, exc, tb)

    # --------------------------------------------------------------- add/remove

    def add(
        self,
        name: str,
        source: SyncBalance | Balance | str | Transport,
        *,
        protocol: ProtocolKind | None = None,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
        src_sbn: int = 0x01,
        dst_sbn: int = 0x09,
        strict: bool = False,
        identify: bool = True,
    ) -> SyncBalance:
        """Blocking :meth:`SartoriusManager.add`.

        Accepts a :class:`SyncBalance` as ``source`` in addition to
        the async shapes — the wrapper is unwrapped to the underlying
        :class:`Balance` before delegation.
        """
        from sartoriuslib.protocol.base import ProtocolKind as _ProtocolKind  # noqa: PLC0415

        effective_protocol = protocol if protocol is not None else _ProtocolKind.XBPI
        mgr = self._require_mgr()
        async_source: Balance | str | Transport = unwrap_sync_balance(source)
        async_balance = self.portal.call(
            mgr.add,
            name,
            async_source,
            protocol=effective_protocol,
            serial_settings=serial_settings,
            timeout=timeout,
            src_sbn=src_sbn,
            dst_sbn=dst_sbn,
            strict=strict,
            identify=identify,
        )
        wrapped = wrap_balance(async_balance, self.portal)
        self._wrapped[name] = wrapped
        return wrapped

    def remove(self, name: str) -> None:
        """Blocking :meth:`SartoriusManager.remove`."""
        mgr = self._require_mgr()
        self._wrapped.pop(name, None)
        self.portal.call(mgr.remove, name)

    def get(self, name: str) -> SyncBalance:
        """Return the sync wrapper for the balance registered under ``name``."""
        cached = self._wrapped.get(name)
        if cached is not None:
            return cached
        mgr = self._require_mgr()
        async_balance = mgr.get(name)
        wrapped = wrap_balance(async_balance, self.portal)
        self._wrapped[name] = wrapped
        return wrapped

    def close(self) -> None:
        """Blocking :meth:`SartoriusManager.close` — idempotent."""
        self._wrapped.clear()
        mgr = self._mgr
        if mgr is None:
            return
        portal = self._portal
        if portal is None:
            return
        portal.call(mgr.close)

    # --------------------------------------------------------------- concurrent I/O

    def poll(
        self,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[Reading]]:
        """Blocking :meth:`SartoriusManager.poll`."""
        mgr = self._require_mgr()
        return self.portal.call(mgr.poll, names)

    def execute[Req, Resp](
        self,
        command: Command[Req, Resp],
        requests_by_name: Mapping[str, Req],
    ) -> Mapping[str, DeviceResult[Resp]]:
        """Blocking :meth:`SartoriusManager.execute`."""
        mgr = self._require_mgr()
        return self.portal.call(mgr.execute, command, requests_by_name)

    # --------------------------------------------------------------- internals

    def _require_mgr(self) -> SartoriusManager:
        mgr = self._mgr
        if mgr is None:
            raise RuntimeError("SyncSartoriusManager is not open")
        return mgr
