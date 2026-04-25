"""Shared ``ProtocolClient`` Protocol and :class:`ProtocolKind` enum.

``ProtocolKind`` is named with the ``Kind`` suffix to avoid colliding with
:class:`typing.Protocol` at import sites. See design doc §7.

:class:`ProtocolClient` is the structural interface both
:class:`XbpiProtocolClient` and :class:`SbiProtocolClient` satisfy. A
session holds at most one client per protocol; both may be present
when ``AUTO`` detection resolves to one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import anyio


class ProtocolKind(StrEnum):
    """Which wire protocol is active on a session.

    ``AUTO`` is only valid at ``open_device`` call time; by the time a
    session exists, ``AUTO`` has resolved to ``XBPI`` or ``SBI``.
    """

    AUTO = "auto"
    XBPI = "xbpi"
    SBI = "sbi"


@runtime_checkable
class ProtocolClient[Reply_co](Protocol):
    """One request/response round-trip over a transport.

    Every implementation owns an :class:`anyio.Lock` (exposed as
    :attr:`lock`) that serialises calls on the shared transport. Session
    code does not hold its own lock — it piggybacks on the client's, so
    two sessions on the same port collapse to one serialized queue when
    they (exceptionally) share a client.

    Errors the device tells us about (xBPI subtype ``0x01``, SBI refusal
    lines) are raised as typed exceptions inside :meth:`execute` —
    callers decode only success replies. Transport-level failures
    (timeout, connection) surface as the corresponding
    :class:`sartoriuslib.errors.SartoriusTransportError` subclass with
    ``__cause__`` preserving the original.
    """

    @property
    def lock(self) -> anyio.Lock:
        """Shared serialisation lock. Held across one full request/response."""
        ...

    @property
    def disposed(self) -> bool:
        """Whether the client has been retired by a protocol reconfiguration."""
        ...

    def dispose(self) -> None:
        """Retire the client so queued waiters fail before writing bytes."""
        ...

    async def execute(
        self,
        request: bytes,
        *,
        timeout: float | None = None,
        command_name: str = "",
        opcode: int | None = None,
    ) -> Reply_co:
        """Send ``request`` and return the decoded reply.

        Raises:
            SartoriusTimeoutError: No response within ``timeout``.
            SartoriusConnectionError: Port closed or lost mid-exchange.
            SartoriusFrameError: Framing / checksum invalid.
            SartoriusCapabilityError (subclass): Device responded with a
                recognised refusal code (xBPI ``0x04``, ``0x06``,
                ``0x03``, ``0x07``, ``0x10``). Callers should catch
                specific subclasses and let others propagate.
            SartoriusCommandRejectedError: Refusal code outside the
                classified set.
        """
        ...


__all__ = ["ProtocolClient", "ProtocolKind"]
