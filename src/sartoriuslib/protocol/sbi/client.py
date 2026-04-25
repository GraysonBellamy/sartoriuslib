"""SBI protocol client — single-in-flight line request/response."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import anyio

from sartoriuslib._logging import get_logger
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusCommandRejectedError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusTimeoutError,
)
from sartoriuslib.protocol.sbi.framing import (
    LINE_TERMINATOR,
    is_autoprint_line,
    split_lines,
)
from sartoriuslib.protocol.sbi.parser import parse_reply
from sartoriuslib.protocol.sbi.tables import describe_token
from sartoriuslib.protocol.sbi.types import SbiLineKind, SbiReply

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport

__all__ = ["SbiProtocolClient"]

_logger = get_logger("protocol.sbi")


class SbiProtocolClient:
    """SBI request/response client over a :class:`Transport`.

    SBI replies are ASCII lines. Some control commands emulate front-panel
    keypresses and do not produce an acknowledgement; callers pass
    ``expect_lines=0`` for those and receive an empty :class:`SbiReply`.
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
        self._pending_lines: deque[bytes] = deque()
        self._autoprint_active = False
        self._disposed = False

    @property
    def transport(self) -> Transport:
        """The underlying transport."""
        return self._transport

    @property
    def lock(self) -> anyio.Lock:
        """Shared serialisation lock; held for one full exchange."""
        return self._lock

    @property
    def disposed(self) -> bool:
        """Whether this client has been retired by a protocol reconfiguration."""
        return self._disposed

    def dispose(self) -> None:
        """Retire this client after its owning session installs a replacement."""
        self._disposed = True

    @property
    def autoprint_active(self) -> bool:
        """Whether passive sniffing has observed unsolicited SBI output."""
        return self._autoprint_active

    def mark_autoprint_active(self, *, pending: bytes | None = None) -> None:
        """Record that callers have successfully consumed autoprint output."""
        self._autoprint_active = True
        if pending:
            self._queue_pending_front(pending)

    def mark_autoprint_inactive(self) -> None:
        """Return the client to SBI command/reply mode."""
        self._autoprint_active = False

    async def detect_autoprint(
        self,
        *,
        timeout: float | None = None,
        max_lines: int = 4,
    ) -> bool:
        """Passively sniff for unsolicited SBI autoprint/status lines.

        Any complete lines read during detection are queued so the first
        stream read still sees them. This keeps detection cheap and honest:
        it never writes to the balance and never loses the first sample.
        """
        t = timeout if timeout is not None else min(self._default_timeout, 0.25)
        if max_lines <= 0:
            raise ValueError(f"max_lines must be > 0, got {max_lines!r}")
        async with self._lock:
            self._raise_if_disposed(command_name="detect_autoprint", sbi_token=None)
            deadline = anyio.current_time() + t
            while len(self._pending_lines) < max_lines and anyio.current_time() < deadline:
                remaining = max(0.001, deadline - anyio.current_time())
                try:
                    raw = await self._transport.read_until(
                        LINE_TERMINATOR,
                        timeout=remaining,
                    )
                except SartoriusTimeoutError:
                    break
                self._pending_lines.append(raw)
                if is_autoprint_line(raw):
                    self._autoprint_active = True
                    break
        return self._autoprint_active

    async def refresh_autoprint_state(
        self,
        *,
        timeout: float | None = None,
        max_lines: int = 4,
    ) -> bool:
        """Re-sniff autoprint state after a user-side mode change.

        Unlike :meth:`detect_autoprint`, this is an explicit resync point:
        pending unsolicited lines are discarded before sniffing. A quiet line
        clears :attr:`autoprint_active`; observed autoprint/status output sets
        it and queues the observed line for later consumption.
        """
        t = timeout if timeout is not None else min(self._default_timeout, 0.25)
        if max_lines <= 0:
            raise ValueError(f"max_lines must be > 0, got {max_lines!r}")
        async with self._lock:
            self._raise_if_disposed(command_name="refresh_autoprint_state", sbi_token=None)
            self._pending_lines.clear()
            self._autoprint_active = False
            deadline = anyio.current_time() + t
            while len(self._pending_lines) < max_lines and anyio.current_time() < deadline:
                remaining = max(0.001, deadline - anyio.current_time())
                try:
                    raw = await self._transport.read_until(
                        LINE_TERMINATOR,
                        timeout=remaining,
                    )
                except SartoriusTimeoutError:
                    break
                self._pending_lines.append(raw)
                if is_autoprint_line(raw):
                    self._autoprint_active = True
                    break
        return self._autoprint_active

    async def execute(
        self,
        request: bytes,
        *,
        timeout: float | None = None,
        command_name: str = "",
        opcode: int | None = None,
        sbi_token: bytes | None = None,
        expect_lines: int = 1,
    ) -> SbiReply:
        """Write ``request`` and parse ``expect_lines`` SBI reply lines."""
        del opcode
        if expect_lines < 0:
            raise ValueError(f"expect_lines must be >= 0, got {expect_lines!r}")
        t = timeout if timeout is not None else self._default_timeout
        async with self._lock:
            return await self._execute_locked(
                request,
                timeout=t,
                command_name=command_name,
                sbi_token=sbi_token,
                expect_lines=expect_lines,
            )

    async def read_line(
        self,
        *,
        timeout: float | None = None,
        command_name: str = "sbi_autoprint",
    ) -> SbiReply:
        """Read and parse one unsolicited SBI line without writing first."""
        t = timeout if timeout is not None else self._default_timeout
        async with self._lock:
            self._raise_if_disposed(command_name=command_name, sbi_token=None)
            if self._pending_lines:
                raw = self._pending_lines.popleft()
            else:
                raw = await self._transport.read_until(LINE_TERMINATOR, timeout=t)
            return await self._parse_or_drain(
                raw,
                command_name=command_name,
                sbi_token=None,
            )

    async def _execute_locked(
        self,
        request: bytes,
        *,
        timeout: float,
        command_name: str,
        sbi_token: bytes | None,
        expect_lines: int,
    ) -> SbiReply:
        self._raise_if_disposed(command_name=command_name, sbi_token=sbi_token)
        await self._transport.write(request, timeout=timeout)
        if expect_lines == 0:
            return SbiReply(lines=(), raw=b"")

        chunks = [
            await self._transport.read_until(LINE_TERMINATOR, timeout=timeout)
            for _ in range(expect_lines)
        ]
        raw = b"".join(chunks)
        return await self._parse_or_drain(
            raw,
            command_name=command_name,
            sbi_token=sbi_token,
        )

    async def _parse_or_drain(
        self,
        raw: bytes,
        *,
        command_name: str,
        sbi_token: bytes | None,
    ) -> SbiReply:
        try:
            reply = parse_reply(raw)
        except SartoriusError:
            try:
                await self._transport.drain_input()
            except Exception as drain_err:
                _logger.debug(
                    "sbi.drain_after_parse_failed",
                    extra={"command_name": command_name, "error": repr(drain_err)},
                )
            raise

        for line in reply.lines:
            if line.kind is SbiLineKind.REFUSAL:
                token_name = describe_token(sbi_token or b"")
                raise SartoriusCommandRejectedError(
                    f"SBI {command_name or token_name}: device rejected command ({line.text})",
                    context=ErrorContext(
                        command_name=command_name or None,
                        sbi_token=sbi_token,
                        raw_response=raw,
                        protocol="sbi",
                        extra={"reason": line.text},
                    ),
                )
        return reply

    def _queue_pending_front(self, raw: bytes) -> None:
        if not raw:
            return
        # ``mark_autoprint_active`` is a best-effort signal — never let
        # malformed pending bytes throw out of it. If the buffer lacks a
        # terminator, queue it as a single chunk and let the next read
        # decide what to do with the partial.
        if not raw.endswith(b"\n"):
            self._pending_lines.appendleft(raw)
            return
        for line in reversed(split_lines(raw)):
            self._pending_lines.appendleft(line)

    def _raise_if_disposed(
        self,
        *,
        command_name: str,
        sbi_token: bytes | None,
    ) -> None:
        if not self._disposed:
            return
        raise SartoriusConnectionError(
            "SBI client was retired during protocol reconfiguration; "
            "retry the command against the active session",
            context=ErrorContext(
                command_name=command_name or None,
                sbi_token=sbi_token,
                protocol="sbi",
                extra={"client_disposed": True},
            ),
        )
