"""Single-balance :class:`PollSourceAdapter` for :func:`record`.

The recorder consumes a :class:`PollSource` — the manager satisfies it
natively because its ``poll(names)`` already returns
``Mapping[str, DeviceResult[Reading]]``. Callers driving a single
:class:`Balance` (no manager) need a small shim that mirrors the same
shape; this is that shim.

Unified spec §E: ``PollSourceAdapter`` is the canonical name across
every sibling library; the method signatures match each library's
recorder Protocol. For sartoriuslib the signature is
``poll(names) -> Mapping[str, DeviceResult[Reading]]`` so the adapter
slots straight into :func:`record` without translation.

SBI autoprint is transparent — :meth:`Balance.poll` already branches
on ``session.sbi_autoprint_active`` and pulls from the unsolicited
stream when appropriate, so the adapter does not need to know about
autoprint mode at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sartoriuslib.errors import SartoriusError
from sartoriuslib.manager import DeviceResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sartoriuslib.devices.balance import Balance
    from sartoriuslib.devices.models import Reading


__all__ = ["PollSourceAdapter"]


class PollSourceAdapter:
    """Wrap one :class:`Balance` as a :class:`PollSource` for :func:`record`.

    Construction takes a ``name`` (the manager-style identifier the
    sample carries downstream) and the :class:`Balance` to poll. Every
    :meth:`poll` invocation returns either a single-entry mapping
    containing the poll outcome wrapped in :class:`DeviceResult`, or an
    empty mapping when ``names`` is supplied and does not include this
    adapter's name.

    Usage::

        adapter = PollSourceAdapter("bal1", balance)
        async with record(adapter, rate_hz=10) as recording:
            async for batch in recording.stream:
                ...
    """

    __slots__ = ("_device", "_name")

    def __init__(self, name: str, device: Balance) -> None:
        self._name = name
        self._device = device

    @property
    def name(self) -> str:
        """The manager-style identifier this adapter publishes samples under."""
        return self._name

    @property
    def device(self) -> Balance:
        """The wrapped :class:`Balance`."""
        return self._device

    async def poll(
        self,
        names: Iterable[str] | None = None,
    ) -> Mapping[str, DeviceResult[Reading]]:
        """Poll the wrapped balance and return a one-entry result mapping.

        When ``names`` is supplied and excludes :attr:`name`, returns
        an empty mapping (the consumer asked for a different device).
        Otherwise returns ``{name: DeviceResult.success(reading)}`` on
        success or ``{name: DeviceResult.failure(error)}`` on a typed
        sartoriuslib failure.
        """
        if names is not None and self._name not in set(names):
            return {}
        try:
            reading = await self._device.poll()
        except SartoriusError as exc:
            return {self._name: DeviceResult.failure(exc)}
        return {self._name: DeviceResult.success(reading)}
