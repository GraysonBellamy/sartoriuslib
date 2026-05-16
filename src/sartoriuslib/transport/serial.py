"""Serial-port transport backed by :mod:`anyserial`.

:class:`SerialTransport` wraps :class:`anyserial.SerialPort`. Every I/O call
is bounded by :func:`anyio.fail_after` (reads, writes) or
:func:`anyio.move_on_after` (idle-timeout reads). Backend exceptions
normalize to :mod:`sartoriuslib.errors` types with ``__cause__`` preserved.

Design reference: ``docs/design.md`` §8.1.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
from anyserial import (
    FlowControl,
    PortBusyError,
    PortNotFoundError,
    SerialClosedError,
    SerialConfig,
    SerialDisconnectedError,
    SerialError,
    open_serial_port,
)

from sartoriuslib.errors import (
    ErrorContext,
    SartoriusConnectionError,
    SartoriusTimeoutError,
    SartoriusTransientTransportError,
    SartoriusTransportError,
)

if TYPE_CHECKING:
    from types import ModuleType

    from anyserial import Parity, SerialPort, StopBits

    from sartoriuslib.transport.base import SerialSettings

__all__ = ["SerialTransport"]

# Per-call read chunk. Bigger is fine — anyserial returns whatever the kernel
# has ready and never blocks waiting to fill the buffer.
_RECEIVE_CHUNK: int = 4096


def _port_open_error_types() -> tuple[type[BaseException], ...]:
    """Build the ``except`` tuple used by :meth:`SerialTransport.open`.

    ``termios.error`` is a bare :class:`Exception` on CPython (not an
    :class:`OSError` subclass), so it has to be listed alongside
    :class:`OSError` explicitly for phantom ``/dev/ttyS*`` UARTs that fail
    ``tcgetattr`` with EIO.
    """
    try:
        import termios  # noqa: PLC0415 — platform-gated optional import
    except ImportError:  # pragma: no cover — Windows has no termios module
        return (OSError,)
    termios_error = _module_exception_type(termios, "error")
    return (OSError, termios_error) if termios_error is not None else (OSError,)


def _module_exception_type(module: ModuleType, name: str) -> type[BaseException] | None:
    value: object = getattr(module, name, None)
    if isinstance(value, type) and issubclass(value, BaseException):
        return value
    return None


_PORT_OPEN_ERRORS: tuple[type[BaseException], ...] = _port_open_error_types()


class SerialTransport:
    """:class:`Transport` backed by a real serial port via ``anyserial``.

    Tests that don't need hardware can use
    :class:`sartoriuslib.transport.fake.FakeTransport` instead; the two
    conform to the same structural :class:`Transport` protocol.
    """

    def __init__(self, settings: SerialSettings) -> None:
        self._settings = settings
        self._port: SerialPort | None = None
        # Bytes read past a separator in :meth:`read_until` (or past ``n``
        # in :meth:`read_exact`) are held here so the next call sees them
        # first. Serial I/O is chunk-oriented — we can't hand the kernel
        # "give me up to separator" or "give me exactly n" without this.
        self._pushback = bytearray()

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        if self._port is not None:
            raise SartoriusConnectionError(
                f"{self.label} is already open",
                context=ErrorContext(port=self.label),
            )
        config = SerialConfig(
            baudrate=self._settings.baudrate,
            byte_size=self._settings.bytesize,
            parity=self._settings.parity,
            stop_bits=self._settings.stopbits,
            flow_control=FlowControl(
                xon_xoff=self._settings.xonxoff,
                rts_cts=self._settings.rtscts,
            ),
            exclusive=self._settings.exclusive,
        )
        try:
            self._port = await open_serial_port(self._settings.port, config)
        except (PortBusyError, PortNotFoundError, SerialDisconnectedError) as exc:
            raise SartoriusConnectionError(
                f"could not open {self.label}: {exc}",
                context=ErrorContext(port=self.label),
            ) from exc
        except SerialError as exc:
            raise SartoriusTransportError(
                f"backend error opening {self.label}: {exc}",
                context=ErrorContext(port=self.label),
            ) from exc
        except _PORT_OPEN_ERRORS as exc:
            # Lower-level kernel errors (``termios.error``, EIO, EACCES)
            # can leak past the anyserial typed wrappers — e.g. Linux
            # enumerates ``/dev/ttyS*`` phantom UARTs that fail
            # ``tcgetattr`` with EIO. Surface as
            # :class:`SartoriusConnectionError` so discovery (which
            # promises to never raise) can collect the failure.
            raise SartoriusConnectionError(
                f"could not open {self.label}: {exc}",
                context=ErrorContext(port=self.label),
            ) from exc

    async def close(self) -> None:
        port = self._port
        if port is None:
            return
        self._port = None
        self._pushback.clear()
        # Close is best-effort; we've already detached the port reference.
        with contextlib.suppress(SerialError):
            await port.aclose()

    async def reopen(
        self,
        *,
        baudrate: int | None = None,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
    ) -> None:
        """Close and reopen the port with any subset of overrides.

        Any argument left as ``None`` keeps the existing setting. Used for
        the WZA SBI→xBPI flip (swaps both baud and parity) and for
        ``set_baud_rate`` retuning after the device has already switched.
        The cached :class:`SerialSettings` is updated so the new settings
        survive subsequent close / open round-trips.

        If :meth:`open` fails on the new settings the transport is left
        closed; the caller is responsible for surfacing that as a
        ``BROKEN`` session state with recovery guidance.
        """
        await self.close()
        new_settings = self._settings
        if baudrate is not None:
            new_settings = replace(new_settings, baudrate=baudrate)
        if parity is not None:
            new_settings = replace(new_settings, parity=parity)
        if stopbits is not None:
            new_settings = replace(new_settings, stopbits=stopbits)
        self._settings = new_settings
        await self.open()

    # ------------------------------------------------------------------ I/O

    async def write(self, data: bytes, *, timeout: float) -> None:
        port = self._require_port()
        try:
            with anyio.fail_after(timeout):
                await port.send(data)
        except TimeoutError as exc:
            raise SartoriusTimeoutError(
                f"write on {self.label} timed out after {timeout}s",
                context=ErrorContext(port=self.label, extra={"phase": "write"}),
            ) from exc
        except (SerialClosedError, SerialDisconnectedError) as exc:
            raise SartoriusConnectionError(
                f"write on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "write"}),
            ) from exc
        except SerialError as exc:
            raise SartoriusTransportError(
                f"write on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "write"}),
            ) from exc

    async def read_exact(self, n: int, *, timeout: float) -> bytes:
        port = self._require_port()
        if n <= 0:
            return b""
        buf = bytearray(self._pushback)
        self._pushback.clear()
        try:
            with anyio.fail_after(timeout):
                while len(buf) < n:
                    chunk = await port.receive(_RECEIVE_CHUNK)
                    if not chunk:
                        continue
                    buf.extend(chunk)
        except TimeoutError as exc:
            # Preserve whatever we did read — the next call may pick up where
            # this one left off once the device sends the rest.
            received = len(buf)
            self._pushback.extend(buf)
            if received == 0:
                # Cold-open USB races land here: the device hasn't begun
                # replying yet. Surface as a typed transient so callers
                # (and ``open_device``'s identify retry loop) can retry
                # without reopening the port. Non-zero partial reads keep
                # the timeout classification because they represent a
                # device that started speaking and then went silent —
                # not the same root cause.
                raise SartoriusTransientTransportError(
                    f"read_exact({n}) on {self.label} got 0 bytes after {timeout}s",
                    context=ErrorContext(
                        port=self.label,
                        extra={"phase": "read", "requested": n, "received": 0},
                    ),
                ) from exc
            raise SartoriusTimeoutError(
                f"read_exact({n}) on {self.label} timed out after {timeout}s "
                f"(got {received}/{n} bytes)",
                context=ErrorContext(
                    port=self.label,
                    extra={"phase": "read", "requested": n, "received": received},
                ),
            ) from exc
        except (SerialClosedError, SerialDisconnectedError) as exc:
            raise SartoriusConnectionError(
                f"read on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "read"}),
            ) from exc
        except SerialError as exc:
            raise SartoriusTransportError(
                f"read on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "read"}),
            ) from exc

        result = bytes(buf[:n])
        leftover = bytes(buf[n:])
        if leftover:
            self._pushback.extend(leftover)
        return result

    async def read_until(self, separator: bytes, *, timeout: float) -> bytes:
        port = self._require_port()
        buf = bytearray(self._pushback)
        self._pushback.clear()
        try:
            with anyio.fail_after(timeout):
                while separator not in buf:
                    chunk = await port.receive(_RECEIVE_CHUNK)
                    if not chunk:
                        continue
                    buf.extend(chunk)
        except TimeoutError as exc:
            self._pushback.extend(buf)
            raise SartoriusTimeoutError(
                f"read_until({separator!r}) on {self.label} timed out after {timeout}s",
                context=ErrorContext(port=self.label, extra={"phase": "read"}),
            ) from exc
        except (SerialClosedError, SerialDisconnectedError) as exc:
            raise SartoriusConnectionError(
                f"read on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "read"}),
            ) from exc
        except SerialError as exc:
            raise SartoriusTransportError(
                f"read on {self.label} failed: {exc}",
                context=ErrorContext(port=self.label, extra={"phase": "read"}),
            ) from exc

        idx = buf.find(separator)
        end = idx + len(separator)
        result = bytes(buf[:end])
        leftover = bytes(buf[end:])
        if leftover:
            self._pushback.extend(leftover)
        return result

    async def read_available(
        self,
        *,
        idle_timeout: float,
        max_bytes: int | None = None,
    ) -> bytes:
        port = self._require_port()
        buf = bytearray(self._pushback)
        self._pushback.clear()
        cap = max_bytes if max_bytes and max_bytes > 0 else None
        while True:
            if cap is not None and len(buf) >= cap:
                break
            with anyio.move_on_after(idle_timeout) as scope:
                try:
                    chunk = await port.receive(_RECEIVE_CHUNK)
                except (SerialClosedError, SerialDisconnectedError):
                    break
                except SerialError:
                    break
                buf.extend(chunk)
            if scope.cancelled_caught:
                break
        if cap is not None and len(buf) > cap:
            leftover = bytes(buf[cap:])
            self._pushback.extend(leftover)
            return bytes(buf[:cap])
        return bytes(buf)

    async def drain_input(self) -> None:
        self._pushback.clear()
        port = self._port
        if port is None:
            return
        # Best-effort — a drain failure shouldn't propagate.
        with contextlib.suppress(SerialError):
            await port.reset_input_buffer()

    # ------------------------------------------------------------------ props

    @property
    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def label(self) -> str:
        return self._settings.port

    # ------------------------------------------------------------------ internals

    def _require_port(self) -> SerialPort:
        port = self._port
        if port is None:
            raise SartoriusConnectionError(
                f"{self.label} is not open",
                context=ErrorContext(port=self.label),
            )
        return port
