"""xBPI protocol client — single-in-flight request/response over a transport.

Holds an :class:`anyio.Lock` shared across one full exchange. Runs the
xBPI length-prefix read sequence (``read_exact(1)`` then
``read_exact(length)``), validates the frame via
:func:`sartoriuslib.protocol.xbpi.framing.parse_frame`, and maps
subtype-``0x01`` error replies to typed
:class:`sartoriuslib.errors.SartoriusError` subclasses.

Transport errors propagate unchanged. Framing errors and error-subtype
replies both trigger a best-effort :meth:`Transport.drain_input` before
re-raising, so the next call starts from a clean buffer.

Design reference: doc §4.1 (protocol-duality seam), §6.1.1
(response-to-availability mapping).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from sartoriuslib._logging import get_logger
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusCommandRejectedError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusIndexOutOfRangeError,
    SartoriusMissingArgsError,
    SartoriusOperationNotApplicableError,
    SartoriusUnsupportedCommandError,
    SartoriusValueOutOfRangeError,
)
from sartoriuslib.protocol.xbpi.framing import parse_frame
from sartoriuslib.protocol.xbpi.parser import decode_error_body
from sartoriuslib.protocol.xbpi.tables import ERROR_CODE_REASONS

_logger = get_logger("protocol.xbpi")

if TYPE_CHECKING:
    from sartoriuslib.protocol.xbpi.types import XbpiFrame
    from sartoriuslib.transport.base import Transport

__all__ = ["XbpiProtocolClient"]


#: Subtype byte that signals a device-side refusal.
_ERROR_SUBTYPE: int = 0x01

#: Error-code → exception class map, per design §6.1.1 and
#: ``docs/protocol.md`` §6. Codes outside this map surface as
#: :class:`SartoriusCommandRejectedError`.
_ERROR_CLASS_MAP: dict[int, type[SartoriusError]] = {
    0x03: SartoriusValueOutOfRangeError,
    0x04: SartoriusUnsupportedCommandError,
    0x06: SartoriusOperationNotApplicableError,
    0x07: SartoriusMissingArgsError,
    0x10: SartoriusIndexOutOfRangeError,
    0x11: SartoriusIndexOutOfRangeError,  # BCE variant — treat identically
}


def _make_refusal(
    code: int,
    frame: XbpiFrame,
    *,
    command_name: str,
    opcode: int | None,
) -> SartoriusError:
    reason = ERROR_CODE_REASONS.get(code, f"unknown (0x{code:02x})")
    cls = _ERROR_CLASS_MAP.get(code, SartoriusCommandRejectedError)
    name = command_name or f"opcode=0x{opcode:02x}" if opcode is not None else ""
    prefix = f"xBPI {name}: " if name else "xBPI "
    return cls(
        f"{prefix}device returned error 0x{code:02x} ({reason})",
        context=ErrorContext(
            command_name=command_name or None,
            opcode=opcode,
            raw_response=frame.raw,
            protocol="xbpi",
            extra={"error_code": code, "reason": reason},
        ),
    )


class XbpiProtocolClient:
    """xBPI request/response client over a :class:`Transport`.

    Single-in-flight via an internal :class:`anyio.Lock`. The lock is
    also exposed so :class:`sartoriuslib.devices.session.Session` (and
    the multi-device manager) can share serialisation with other
    machinery on the same port.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        default_timeout: float = 1.0,
    ) -> None:
        self._transport = transport
        self._default_timeout = default_timeout
        self._lock = anyio.Lock()
        self._disposed = False

    @property
    def transport(self) -> Transport:
        """The underlying :class:`Transport` this client writes and reads."""
        return self._transport

    @property
    def lock(self) -> anyio.Lock:
        """Shared serialisation lock; held for one full request/response."""
        return self._lock

    @property
    def disposed(self) -> bool:
        """Whether this client has been retired by a protocol reconfiguration."""
        return self._disposed

    def dispose(self) -> None:
        """Retire this client after its owning session installs a replacement."""
        self._disposed = True

    async def execute(
        self,
        request: bytes,
        *,
        timeout: float | None = None,
        command_name: str = "",
        opcode: int | None = None,
    ) -> XbpiFrame:
        """Send ``request`` and return the decoded reply frame.

        Holds :attr:`lock` for the full exchange. Drains the input
        buffer on framing errors and on error-subtype replies so the
        next call is not corrupted by a partial response.
        """
        t = timeout if timeout is not None else self._default_timeout
        async with self._lock:
            return await self._execute_locked(request, t, command_name, opcode)

    async def _execute_locked(
        self,
        request: bytes,
        timeout: float,
        command_name: str,
        opcode: int | None,
    ) -> XbpiFrame:
        self._raise_if_disposed(command_name=command_name, opcode=opcode)
        await self._transport.write(request, timeout=timeout)
        length_byte = await self._transport.read_exact(1, timeout=timeout)
        length = length_byte[0]
        rest = await self._transport.read_exact(length, timeout=timeout)
        raw = length_byte + rest

        try:
            frame = parse_frame(raw)
        except SartoriusError:
            try:
                await self._transport.drain_input()
            except Exception as drain_err:
                _logger.debug(
                    "xbpi.drain_after_parse_failed",
                    extra={"command_name": command_name, "error": repr(drain_err)},
                )
            raise

        if frame.subtype == _ERROR_SUBTYPE:
            err_body = decode_error_body(frame.body)
            raise _make_refusal(
                err_body.code,
                frame,
                command_name=command_name,
                opcode=opcode,
            )

        return frame

    def _raise_if_disposed(self, *, command_name: str, opcode: int | None) -> None:
        if not self._disposed:
            return
        raise SartoriusConnectionError(
            "xBPI client was retired during protocol reconfiguration; "
            "retry the command against the active session",
            context=ErrorContext(
                command_name=command_name or None,
                opcode=opcode,
                protocol="xbpi",
                extra={"client_disposed": True},
            ),
        )
