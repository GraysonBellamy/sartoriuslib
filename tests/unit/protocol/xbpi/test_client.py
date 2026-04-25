"""Tests for :class:`sartoriuslib.protocol.xbpi.client.XbpiProtocolClient`."""

from __future__ import annotations

import anyio
import anyio.lowlevel
import pytest

from sartoriuslib.errors import (
    SartoriusCommandRejectedError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusFrameError,
    SartoriusIndexOutOfRangeError,
    SartoriusMissingArgsError,
    SartoriusOperationNotApplicableError,
    SartoriusTimeoutError,
    SartoriusUnsupportedCommandError,
    SartoriusValueOutOfRangeError,
)
from sartoriuslib.protocol.xbpi import XbpiProtocolClient, build_command, checksum
from sartoriuslib.transport import FakeTransport

# ---------------------------------------------------------------------------
# Helpers — build a scripted transport for one request / reply.
# ---------------------------------------------------------------------------


def _rx(subtype: int, body: bytes) -> bytes:
    """Build a well-formed balance→host frame."""
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


async def _open_client(script: dict[bytes, bytes]) -> tuple[XbpiProtocolClient, FakeTransport]:
    transport = FakeTransport(script)
    await transport.open()
    client = XbpiProtocolClient(transport, default_timeout=0.1)
    return client, transport


# ---------------------------------------------------------------------------
# Success path.
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    @pytest.mark.anyio
    async def test_returns_parsed_frame(self) -> None:
        """read_sbn — protocol.md §3.3 worked example."""
        tx = build_command(0x71)
        client, _ = await _open_client({tx: _rx(0x21, b"\x00")})
        frame = await client.execute(tx)
        assert frame.subtype == 0x21
        assert frame.body == b"\x00"

    @pytest.mark.anyio
    async def test_reads_variable_length_payload(self) -> None:
        """Exercise the length-prefix read path on a longer reply."""
        tx = build_command(0x1E)
        body = b"\xbb\xa3\xd7\x0a\x3d\x30\x82\x45"  # §3.3 measurement body
        client, _ = await _open_client({tx: _rx(0x48, body)})
        frame = await client.execute(tx)
        assert frame.subtype == 0x48
        assert frame.body == body

    @pytest.mark.anyio
    async def test_ack_reply(self) -> None:
        """Tare → subtype 0x00 ACK, no body."""
        tx = build_command(0x14)
        client, _ = await _open_client({tx: _rx(0x00, b"")})
        frame = await client.execute(tx)
        assert frame.subtype == 0x00
        assert frame.body == b""


# ---------------------------------------------------------------------------
# Error replies — subtype 0x01 maps to typed exceptions.
# ---------------------------------------------------------------------------


class TestExecuteErrorMapping:
    @pytest.mark.parametrize(
        ("code", "exc_class"),
        [
            (0x03, SartoriusValueOutOfRangeError),
            (0x04, SartoriusUnsupportedCommandError),
            (0x06, SartoriusOperationNotApplicableError),
            (0x07, SartoriusMissingArgsError),
            (0x10, SartoriusIndexOutOfRangeError),
            (0x11, SartoriusIndexOutOfRangeError),  # BCE variant
        ],
    )
    @pytest.mark.anyio
    async def test_code_maps_to_typed_exception(
        self,
        code: int,
        exc_class: type[SartoriusError],
    ) -> None:
        tx = build_command(0x1E)
        client, _ = await _open_client({tx: _rx(0x01, bytes([code]))})
        with pytest.raises(exc_class) as ei:
            await client.execute(tx, command_name="read_net_weight", opcode=0x1E)
        assert ei.value.context.command_name == "read_net_weight"
        assert ei.value.context.opcode == 0x1E
        assert ei.value.context.protocol == "xbpi"
        assert ei.value.context.extra["error_code"] == code

    @pytest.mark.anyio
    async def test_unknown_error_code_falls_back_to_generic(self) -> None:
        tx = build_command(0x1E)
        client, _ = await _open_client({tx: _rx(0x01, b"\x99")})
        with pytest.raises(SartoriusCommandRejectedError):
            await client.execute(tx)


# ---------------------------------------------------------------------------
# Transport / framing errors propagate.
# ---------------------------------------------------------------------------


class TestExecuteTransportErrors:
    @pytest.mark.anyio
    async def test_timeout_propagates(self) -> None:
        """Unscripted write → scripted reply missing → read times out."""
        tx = build_command(0x1E)
        client, _ = await _open_client({})  # empty script
        with pytest.raises(SartoriusTimeoutError):
            await client.execute(tx, timeout=0.02)

    @pytest.mark.anyio
    async def test_bad_checksum_raises_frame_error(self) -> None:
        tx = build_command(0x71)
        rx = bytearray(_rx(0x21, b"\x00"))
        rx[-1] ^= 0xFF  # corrupt the checksum
        client, _ = await _open_client({tx: bytes(rx)})
        with pytest.raises(SartoriusFrameError):
            await client.execute(tx)

    @pytest.mark.anyio
    async def test_framing_error_drains_input(self) -> None:
        """After a framing error the input buffer is cleared.

        Script a trailing byte *after* the bad-checksum reply. If drain
        runs, it gets removed; if it didn't, the next read would pick it
        up.
        """
        tx1 = build_command(0x71)
        rx_bad = bytearray(_rx(0x21, b"\x00"))
        rx_bad[-1] ^= 0xFF
        # Reply is the bad frame + one trailing byte; drain should swallow
        # the trailing byte after the framing error fires.
        client, transport = await _open_client({tx1: bytes(rx_bad) + b"\x99"})
        with pytest.raises(SartoriusFrameError):
            await client.execute(tx1)
        # drain_input was called → trailing byte was swallowed.
        assert await transport.read_available(idle_timeout=0.01) == b""


# ---------------------------------------------------------------------------
# Lock — single-in-flight semantics.
# ---------------------------------------------------------------------------


class TestLock:
    @pytest.mark.anyio
    async def test_lock_is_exposed(self) -> None:
        client, _ = await _open_client({})
        # Lock property exists and is an anyio.Lock
        assert client.lock is client.lock  # same object each time
        assert hasattr(client.lock, "acquire")
        assert hasattr(client.lock, "release")

    @pytest.mark.anyio
    async def test_concurrent_execute_calls_serialize(self) -> None:
        """Two concurrent :meth:`execute` calls must not interleave on the wire.

        Scripted replies for two distinct TX frames: both should complete,
        and the transport's write log must show them in order (either
        order, but strictly one after the other).
        """
        import anyio

        tx1 = build_command(0x71)
        tx2 = build_command(0x1E)
        script = {
            tx1: _rx(0x21, b"\x00"),
            tx2: _rx(0x00, b""),
        }
        client, transport = await _open_client(script)

        async with anyio.create_task_group() as tg:
            tg.start_soon(client.execute, tx1)
            tg.start_soon(client.execute, tx2)
        assert set(transport.writes) == {tx1, tx2}
        assert len(transport.writes) == 2

    @pytest.mark.anyio
    async def test_queued_execute_fails_cleanly_after_dispose(self) -> None:
        tx = build_command(0x71)
        client, transport = await _open_client({tx: _rx(0x21, b"\x00")})
        await client.lock.acquire()
        released = False
        errors: list[BaseException] = []

        async def _run() -> None:
            try:
                await client.execute(tx, command_name="read_sbn", opcode=0x71)
            except BaseException as exc:
                errors.append(exc)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_run)
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
