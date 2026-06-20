"""Tests for :class:`sartoriuslib.protocol.sbi.client.SbiProtocolClient`."""

from __future__ import annotations

import anyio
import anyio.lowlevel
import pytest

from sartoriuslib.errors import (
    SartoriusCommandRejectedError,
    SartoriusConnectionError,
    SartoriusTimeoutError,
)
from sartoriuslib.protocol.sbi import TOKEN_PRINT, SbiProtocolClient
from sartoriuslib.transport import FakeTransport


class TestSbiProtocolClient:
    @pytest.mark.anyio
    async def test_execute_reads_one_line(self) -> None:
        transport = FakeTransport({TOKEN_PRINT: b"+     0.00 g  \r\n"})
        await transport.open()
        client = SbiProtocolClient(transport, default_timeout=0.1)
        reply = await client.execute(TOKEN_PRINT, sbi_token=TOKEN_PRINT)
        assert reply.raw == b"+     0.00 g  \r\n"
        assert reply.lines[0].reading is not None
        assert transport.writes == (TOKEN_PRINT,)

    @pytest.mark.anyio
    async def test_no_response_command_only_writes(self) -> None:
        transport = FakeTransport()
        await transport.open()
        client = SbiProtocolClient(transport, default_timeout=0.1)
        reply = await client.execute(b"\x1bT", sbi_token=b"\x1bT", expect_lines=0)
        assert reply.lines == ()
        assert reply.raw == b""
        assert transport.writes == (b"\x1bT",)

    @pytest.mark.anyio
    async def test_refusal_raises_typed_error(self) -> None:
        transport = FakeTransport({TOKEN_PRINT: b"ERR\r\n"})
        await transport.open()
        client = SbiProtocolClient(transport, default_timeout=0.1)
        with pytest.raises(SartoriusCommandRejectedError, match="rejected"):
            await client.execute(TOKEN_PRINT, sbi_token=TOKEN_PRINT)

    @pytest.mark.anyio
    async def test_unscripted_reply_times_out(self) -> None:
        transport = FakeTransport()
        await transport.open()
        client = SbiProtocolClient(transport, default_timeout=0.01)
        with pytest.raises(SartoriusTimeoutError):
            await client.execute(TOKEN_PRINT, sbi_token=TOKEN_PRINT)

    @pytest.mark.anyio
    async def test_read_line_consumes_unsolicited_autoprint(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        client = SbiProtocolClient(transport, default_timeout=0.1)
        reply = await client.read_line(timeout=0.1)
        assert reply.lines[0].reading is not None

    @pytest.mark.anyio
    async def test_detect_autoprint_queues_observed_line(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        client = SbiProtocolClient(transport, default_timeout=0.1)
        assert await client.detect_autoprint(timeout=0.1)
        assert client.autoprint_active is True
        reply = await client.read_line(timeout=0.1)
        assert reply.raw == b"+     0.00 g  \r\n"
        assert reply.lines[0].reading is not None

    @pytest.mark.anyio
    async def test_detect_autoprint_skips_midline_fragment(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"0.090\r\nN     +    0.031    \r\n")
        client = SbiProtocolClient(transport, default_timeout=0.1)
        assert await client.detect_autoprint(timeout=0.1)
        first = await client.read_line(timeout=0.1)
        second = await client.read_line(timeout=0.1)
        assert first.lines[0].reading is None
        assert second.lines[0].reading is not None

    @pytest.mark.anyio
    async def test_refresh_autoprint_state_clears_on_quiet_line(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        client = SbiProtocolClient(transport, default_timeout=0.1)
        assert await client.detect_autoprint(timeout=0.1)
        assert await client.refresh_autoprint_state(timeout=0.01) is False
        assert client.autoprint_active is False
        with pytest.raises(SartoriusTimeoutError):
            await client.read_line(timeout=0.01)

    @pytest.mark.anyio
    async def test_refresh_autoprint_state_detects_and_queues_line(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        client = SbiProtocolClient(transport, default_timeout=0.1)
        assert await client.refresh_autoprint_state(timeout=0.1)
        reply = await client.read_line(timeout=0.1)
        assert reply.raw == b"N     +    0.031    \r\n"
        assert reply.lines[0].reading is not None

    @pytest.mark.anyio
    async def test_queued_execute_fails_cleanly_after_dispose(self) -> None:
        transport = FakeTransport({TOKEN_PRINT: b"+     0.00 g  \r\n"})
        await transport.open()
        client = SbiProtocolClient(transport, default_timeout=0.1)
        await client.lock.acquire()
        released = False
        errors: list[BaseException] = []

        async def _run() -> None:
            try:
                await client.execute(TOKEN_PRINT, command_name="print", sbi_token=TOKEN_PRINT)
            except BaseException as exc:
                errors.append(exc)

        try:
            async with anyio.create_task_group() as tg:
                _ = tg.start_soon(_run)
                await anyio.lowlevel.checkpoint()
                client.dispose()
                client.lock.release()
                released = True
        finally:
            if not released:
                client.lock.release()

        assert client.disposed is True
        assert len(errors) == 1
        assert isinstance(errors[0], SartoriusConnectionError)
        assert transport.writes == ()
