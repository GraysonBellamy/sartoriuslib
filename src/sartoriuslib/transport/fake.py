"""In-process fake transport for tests.

:class:`FakeTransport` implements the :class:`Transport` Protocol without
touching a serial port. Tests script the expected write→response mapping and
assert the recorded command bytes.

Re-exported from :mod:`sartoriuslib.testing` alongside fixture-parsing helpers.

Design reference: ``docs/design.md`` §8.2.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

import anyio

from sartoriuslib.errors import (
    ErrorContext,
    SartoriusConnectionError,
    SartoriusTimeoutError,
)

if TYPE_CHECKING:
    from anyserial import Parity, StopBits

__all__ = ["FakeTransport", "ScriptedReply"]


#: A scripted reply. Bytes are emitted verbatim; sequences are concatenated
#: in order; callables receive the exact write payload and return bytes or a
#: sequence of bytes (useful for fuzzier scripts).
type ScriptedReply = bytes | Sequence[bytes] | Callable[[bytes], bytes | Sequence[bytes]]


def _normalize_reply(reply: bytes | Sequence[bytes]) -> bytes:
    if isinstance(reply, bytes):
        return reply
    return b"".join(reply)


class FakeTransport:
    """Scripted :class:`Transport` for tests.

    Arguments:
        script: Mapping of ``write_bytes → reply``. Every scripted write
            queues the corresponding reply into the read buffer. Unknown
            writes are recorded but produce no reply — subsequent reads
            will then hit a timeout, which is the intended failure mode
            (tests see a real timeout if they forgot to script a
            command).
        label: Identifier used in errors.
        latency_s: Per-operation artificial delay, useful for simulating
            a slow device.
    """

    def __init__(
        self,
        script: Mapping[bytes, ScriptedReply] | None = None,
        *,
        label: str = "fake://test",
        latency_s: float = 0.0,
    ) -> None:
        self._script: dict[bytes, ScriptedReply] = dict(script or {})
        self._writes: list[bytes] = []
        self._read_buffer = bytearray()
        self._is_open = False
        self._label = label
        self._latency_s = latency_s
        self._force_read_timeout = False
        self._force_write_timeout = False
        # Track calls to ``reopen`` so protocol-flip tests can assert the
        # transport observed the reconfiguration. ``None`` until
        # :meth:`reopen` is called; then holds the last requested value
        # for each setting. ``reopen_count`` lets tests distinguish
        # "never called" from "called with the same settings".
        self._last_reopen_baud: int | None = None
        self._last_reopen_parity: Parity | None = None
        self._last_reopen_stopbits: StopBits | None = None
        self._reopen_count: int = 0
        self._force_reopen_error: bool = False

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        if self._is_open:
            raise SartoriusConnectionError(
                f"{self._label} is already open",
                context=ErrorContext(port=self._label),
            )
        self._is_open = True

    async def close(self) -> None:
        self._is_open = False

    async def reopen(
        self,
        *,
        baudrate: int | None = None,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
    ) -> None:
        """Simulate a serial-setting change — close, record, reopen.

        If ``force_reopen_error()`` has been called the reopen raises
        :class:`SartoriusConnectionError` after the close, leaving the
        transport closed. That is the "reopen wedged" path used by
        :meth:`Balance.configure_protocol` tests for the BROKEN-state
        transition.
        """
        await self.close()
        self._reopen_count += 1
        if baudrate is not None:
            self._last_reopen_baud = baudrate
        if parity is not None:
            self._last_reopen_parity = parity
        if stopbits is not None:
            self._last_reopen_stopbits = stopbits
        if self._force_reopen_error:
            raise SartoriusConnectionError(
                f"forced reopen error on {self._label}",
                context=ErrorContext(port=self._label),
            )
        await self.open()

    # ------------------------------------------------------------------ I/O

    async def write(self, data: bytes, *, timeout: float) -> None:
        self._ensure_open()
        if self._force_write_timeout:
            raise SartoriusTimeoutError(
                f"write on {self._label} timed out after {timeout}s (forced)",
                context=ErrorContext(port=self._label, extra={"phase": "write"}),
            )
        if self._latency_s:
            await anyio.sleep(self._latency_s)
        payload = bytes(data)
        self._writes.append(payload)
        reply = self._script.get(payload)
        if reply is None:
            return
        if callable(reply):
            produced = reply(payload)
            self._read_buffer.extend(_normalize_reply(produced))
        else:
            self._read_buffer.extend(_normalize_reply(reply))

    async def read_exact(self, n: int, *, timeout: float) -> bytes:
        self._ensure_open()
        if self._force_read_timeout:
            raise SartoriusTimeoutError(
                f"read_exact({n}) on {self._label} timed out after {timeout}s (forced)",
                context=ErrorContext(port=self._label, extra={"phase": "read"}),
            )
        if self._latency_s:
            await anyio.sleep(self._latency_s)
        if len(self._read_buffer) < n:
            raise SartoriusTimeoutError(
                f"read_exact({n}) on {self._label} timed out after {timeout}s",
                context=ErrorContext(port=self._label, extra={"phase": "read"}),
            )
        result = bytes(self._read_buffer[:n])
        del self._read_buffer[:n]
        return result

    async def read_until(self, separator: bytes, *, timeout: float) -> bytes:
        self._ensure_open()
        if self._force_read_timeout:
            raise SartoriusTimeoutError(
                f"read_until on {self._label} timed out after {timeout}s (forced)",
                context=ErrorContext(port=self._label, extra={"phase": "read"}),
            )
        if self._latency_s:
            await anyio.sleep(self._latency_s)
        idx = self._read_buffer.find(separator)
        if idx < 0:
            raise SartoriusTimeoutError(
                f"read_until({separator!r}) on {self._label} timed out after {timeout}s",
                context=ErrorContext(port=self._label, extra={"phase": "read"}),
            )
        end = idx + len(separator)
        result = bytes(self._read_buffer[:end])
        del self._read_buffer[:end]
        return result

    async def read_available(
        self,
        *,
        idle_timeout: float,
        max_bytes: int | None = None,
    ) -> bytes:
        self._ensure_open()
        if self._latency_s:
            await anyio.sleep(self._latency_s)
        if max_bytes is None or max_bytes >= len(self._read_buffer):
            result = bytes(self._read_buffer)
            self._read_buffer.clear()
        else:
            result = bytes(self._read_buffer[:max_bytes])
            del self._read_buffer[:max_bytes]
        return result

    async def drain_input(self) -> None:
        self._read_buffer.clear()

    # ------------------------------------------------------------------ props

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def label(self) -> str:
        return self._label

    # ------------------------------------------------------------------ test API

    @property
    def writes(self) -> tuple[bytes, ...]:
        """Every write payload recorded since construction, in order."""
        return tuple(self._writes)

    def feed(self, data: bytes) -> None:
        """Push unsolicited bytes into the read buffer.

        Useful for simulating a device that was left in SBI autoprint
        mode, or garbage on the line that the session must drain on
        recovery.
        """
        self._read_buffer.extend(data)

    def add_script(self, command: bytes, reply: ScriptedReply) -> None:
        """Register or overwrite a scripted reply for ``command``."""
        self._script[bytes(command)] = reply

    def force_read_timeout(self, enabled: bool = True) -> None:
        """Force the next read to raise ``SartoriusTimeoutError``."""
        self._force_read_timeout = enabled

    def force_write_timeout(self, enabled: bool = True) -> None:
        """Force the next :meth:`write` to raise ``SartoriusTimeoutError``."""
        self._force_write_timeout = enabled

    def force_reopen_error(self, enabled: bool = True) -> None:
        """Force the next :meth:`reopen` to raise ``SartoriusConnectionError``."""
        self._force_reopen_error = enabled

    @property
    def reopen_count(self) -> int:
        """Number of :meth:`reopen` calls since construction."""
        return self._reopen_count

    @property
    def last_reopen_baud(self) -> int | None:
        """Baud rate requested by the most recent :meth:`reopen`, or ``None``."""
        return self._last_reopen_baud

    @property
    def last_reopen_parity(self) -> Parity | None:
        """Parity requested by the most recent :meth:`reopen`, or ``None``."""
        return self._last_reopen_parity

    @property
    def last_reopen_stopbits(self) -> StopBits | None:
        """Stop bits requested by the most recent :meth:`reopen`, or ``None``."""
        return self._last_reopen_stopbits

    # ------------------------------------------------------------------ internals

    def _ensure_open(self) -> None:
        if not self._is_open:
            raise SartoriusConnectionError(
                f"{self._label} is not open",
                context=ErrorContext(port=self._label),
            )
