"""Tests for :class:`sartoriuslib.transport.fake.FakeTransport`."""

from __future__ import annotations

import pytest
from anyserial import Parity, StopBits

from sartoriuslib.errors import SartoriusConnectionError, SartoriusTimeoutError
from sartoriuslib.transport import FakeTransport, Transport

# Sartorius-specific fixtures drawn from docs/protocol.md §3.3.
# Read SBN (request + reply, subtype 0x21 "short_data").
_READ_SBN_REQ = bytes.fromhex("04 01 09 71 7f".replace(" ", ""))
_READ_SBN_REPLY = bytes.fromhex("04 41 21 00 66".replace(" ", ""))
# Read net weight (request + reply, subtype 0x48 "measurement", 8-byte body).
_READ_NET_REQ = bytes.fromhex("04 01 09 1e 2c".replace(" ", ""))
_READ_NET_REPLY = bytes.fromhex(
    "0b 41 48 bb a3 d7 0a 3d 30 82 45 55".replace(" ", ""),
)
# SBI print reply (ASCII, \r\n-terminated).
_SBI_PRINT_LINE = b"+     0.00 g  \r\n"


# ---------------------------------------------------------------------------
# Static Protocol conformance.
# ---------------------------------------------------------------------------


def test_fake_transport_is_a_transport() -> None:
    """Structural check — if this fails, the Protocol contract shifted."""
    _: Transport = FakeTransport()


# ---------------------------------------------------------------------------
# Lifecycle.
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.anyio
    async def test_starts_closed(self) -> None:
        t = FakeTransport()
        assert not t.is_open

    @pytest.mark.anyio
    async def test_open_then_close(self) -> None:
        t = FakeTransport()
        await t.open()
        assert t.is_open
        await t.close()
        assert not t.is_open

    @pytest.mark.anyio
    async def test_double_open_raises(self) -> None:
        t = FakeTransport()
        await t.open()
        with pytest.raises(SartoriusConnectionError):
            await t.open()

    @pytest.mark.anyio
    async def test_close_twice_is_safe(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.close()
        await t.close()  # idempotent

    @pytest.mark.anyio
    async def test_write_before_open_raises(self) -> None:
        t = FakeTransport()
        with pytest.raises(SartoriusConnectionError):
            await t.write(_READ_SBN_REQ, timeout=0.1)

    @pytest.mark.anyio
    async def test_read_exact_before_open_raises(self) -> None:
        t = FakeTransport()
        with pytest.raises(SartoriusConnectionError):
            await t.read_exact(1, timeout=0.1)

    @pytest.mark.anyio
    async def test_read_until_before_open_raises(self) -> None:
        t = FakeTransport()
        with pytest.raises(SartoriusConnectionError):
            await t.read_until(b"\r\n", timeout=0.1)


# ---------------------------------------------------------------------------
# Scripted replies.
# ---------------------------------------------------------------------------


class TestScriptedReplies:
    @pytest.mark.anyio
    async def test_xbpi_bytes_reply(self) -> None:
        """Binary xBPI frame flows through the scripted mapping byte-for-byte."""
        t = FakeTransport({_READ_SBN_REQ: _READ_SBN_REPLY})
        await t.open()
        await t.write(_READ_SBN_REQ, timeout=0.1)
        length_byte = await t.read_exact(1, timeout=0.1)
        assert length_byte == b"\x04"
        remainder = await t.read_exact(length_byte[0], timeout=0.1)
        assert length_byte + remainder == _READ_SBN_REPLY

    @pytest.mark.anyio
    async def test_sbi_line_reply(self) -> None:
        t = FakeTransport({b"\x1bP": _SBI_PRINT_LINE})
        await t.open()
        await t.write(b"\x1bP", timeout=0.1)
        reply = await t.read_until(b"\r\n", timeout=0.1)
        assert reply == _SBI_PRINT_LINE

    @pytest.mark.anyio
    async def test_list_reply_concatenates(self) -> None:
        """A sequence of byte strings is concatenated into one reply blob."""
        t = FakeTransport({_READ_NET_REQ: [_READ_NET_REPLY[:1], _READ_NET_REPLY[1:]]})
        await t.open()
        await t.write(_READ_NET_REQ, timeout=0.1)
        length_byte = await t.read_exact(1, timeout=0.1)
        body_and_chk = await t.read_exact(length_byte[0], timeout=0.1)
        assert length_byte + body_and_chk == _READ_NET_REPLY

    @pytest.mark.anyio
    async def test_callable_reply_sees_payload(self) -> None:
        def echo(cmd: bytes) -> bytes:
            return b"\x04\x41\x21" + cmd[-2:-1] + b"\x00"

        t = FakeTransport({_READ_SBN_REQ: echo})
        await t.open()
        await t.write(_READ_SBN_REQ, timeout=0.1)
        reply = await t.read_exact(5, timeout=0.1)
        assert reply[:3] == b"\x04\x41\x21"
        assert reply[3:4] == b"\x71"  # opcode byte of the request, echoed

    @pytest.mark.anyio
    async def test_unscripted_write_has_no_reply(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.write(_READ_SBN_REQ, timeout=0.1)
        assert t.writes == (_READ_SBN_REQ,)
        with pytest.raises(SartoriusTimeoutError):
            await t.read_exact(1, timeout=0.05)

    @pytest.mark.anyio
    async def test_add_script_after_open(self) -> None:
        t = FakeTransport()
        await t.open()
        t.add_script(_READ_SBN_REQ, _READ_SBN_REPLY)
        await t.write(_READ_SBN_REQ, timeout=0.1)
        length_byte = await t.read_exact(1, timeout=0.1)
        remainder = await t.read_exact(length_byte[0], timeout=0.1)
        assert length_byte + remainder == _READ_SBN_REPLY


# ---------------------------------------------------------------------------
# Read behaviour — byte exactness, pushback, idle-timeout reads.
# ---------------------------------------------------------------------------


class TestReadExact:
    @pytest.mark.anyio
    async def test_read_exact_returns_exactly_n_bytes(self) -> None:
        t = FakeTransport({_READ_NET_REQ: _READ_NET_REPLY})
        await t.open()
        await t.write(_READ_NET_REQ, timeout=0.1)
        # Emulate the xBPI framing read pattern: one byte for len, then len
        # more bytes (body + checksum).
        head = await t.read_exact(1, timeout=0.1)
        assert head == b"\x0b"
        rest = await t.read_exact(0x0B, timeout=0.1)
        assert len(rest) == 0x0B
        assert head + rest == _READ_NET_REPLY

    @pytest.mark.anyio
    async def test_read_exact_leaves_remainder_buffered(self) -> None:
        """Over-reading one frame's payload mustn't eat the next one."""
        t = FakeTransport()
        await t.open()
        t.feed(_READ_SBN_REPLY + _READ_NET_REPLY)
        first_len = (await t.read_exact(1, timeout=0.1))[0]
        first_body = await t.read_exact(first_len, timeout=0.1)
        assert first_len == 0x04
        assert bytes([first_len]) + first_body == _READ_SBN_REPLY
        second_len = (await t.read_exact(1, timeout=0.1))[0]
        second_body = await t.read_exact(second_len, timeout=0.1)
        assert bytes([second_len]) + second_body == _READ_NET_REPLY

    @pytest.mark.anyio
    async def test_read_exact_timeout_tags_phase_read(self) -> None:
        t = FakeTransport()
        await t.open()
        with pytest.raises(SartoriusTimeoutError) as ei:
            await t.read_exact(1, timeout=0.05)
        assert ei.value.context.extra.get("phase") == "read"

    @pytest.mark.anyio
    async def test_read_exact_zero_returns_empty(self) -> None:
        """Zero-length read is a no-op — don't block, don't consume buffer."""
        t = FakeTransport()
        await t.open()
        t.feed(b"keep me")
        got = await t.read_exact(0, timeout=0.1)
        assert got == b""
        remainder = await t.read_available(idle_timeout=0.01)
        assert remainder == b"keep me"


class TestReadUntil:
    @pytest.mark.anyio
    async def test_read_until_consumes_through_separator(self) -> None:
        t = FakeTransport({b"\x1bP": b"first\r\nsecond\r\n"})
        await t.open()
        await t.write(b"\x1bP", timeout=0.1)
        first = await t.read_until(b"\r\n", timeout=0.1)
        second = await t.read_until(b"\r\n", timeout=0.1)
        assert first == b"first\r\n"
        assert second == b"second\r\n"

    @pytest.mark.anyio
    async def test_read_until_timeout_tags_phase_read(self) -> None:
        t = FakeTransport()
        await t.open()
        t.feed(b"no terminator here")
        with pytest.raises(SartoriusTimeoutError) as ei:
            await t.read_until(b"\r\n", timeout=0.05)
        assert ei.value.context.extra.get("phase") == "read"


class TestReadAvailable:
    @pytest.mark.anyio
    async def test_returns_all_buffered(self) -> None:
        t = FakeTransport()
        await t.open()
        t.feed(_SBI_PRINT_LINE)
        got = await t.read_available(idle_timeout=0.05)
        assert got == _SBI_PRINT_LINE

    @pytest.mark.anyio
    async def test_honours_max_bytes(self) -> None:
        t = FakeTransport()
        await t.open()
        t.feed(b"1234567890")
        got = await t.read_available(idle_timeout=0.05, max_bytes=4)
        assert got == b"1234"
        remainder = await t.read_available(idle_timeout=0.05)
        assert remainder == b"567890"

    @pytest.mark.anyio
    async def test_returns_empty_when_idle(self) -> None:
        """No bytes + no writes = empty return, never raises."""
        t = FakeTransport()
        await t.open()
        got = await t.read_available(idle_timeout=0.01)
        assert got == b""


class TestDrainInput:
    @pytest.mark.anyio
    async def test_drain_input_clears_buffer(self) -> None:
        t = FakeTransport()
        await t.open()
        t.feed(b"garbage")
        await t.drain_input()
        got = await t.read_available(idle_timeout=0.01)
        assert got == b""


# ---------------------------------------------------------------------------
# Write recording.
# ---------------------------------------------------------------------------


class TestWriteRecording:
    @pytest.mark.anyio
    async def test_records_every_write_in_order(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.write(_READ_SBN_REQ, timeout=0.1)
        await t.write(_READ_NET_REQ, timeout=0.1)
        await t.write(b"\x1bP", timeout=0.1)
        assert t.writes == (_READ_SBN_REQ, _READ_NET_REQ, b"\x1bP")


# ---------------------------------------------------------------------------
# reopen() — multi-setting overrides (xBPI↔SBI flip on WZA swaps baud + parity).
# ---------------------------------------------------------------------------


class TestReopen:
    @pytest.mark.anyio
    async def test_reopen_records_baud(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.reopen(baudrate=19200)
        assert t.reopen_count == 1
        assert t.last_reopen_baud == 19200
        assert t.is_open

    @pytest.mark.anyio
    async def test_reopen_records_parity_and_stopbits(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.reopen(parity=Parity.ODD, stopbits=StopBits.ONE)
        assert t.last_reopen_parity is Parity.ODD
        assert t.last_reopen_stopbits is StopBits.ONE

    @pytest.mark.anyio
    async def test_reopen_with_no_overrides_still_counts(self) -> None:
        t = FakeTransport()
        await t.open()
        await t.reopen()
        assert t.reopen_count == 1
        assert t.last_reopen_baud is None

    @pytest.mark.anyio
    async def test_force_reopen_error_leaves_transport_closed(self) -> None:
        t = FakeTransport()
        await t.open()
        t.force_reopen_error()
        with pytest.raises(SartoriusConnectionError):
            await t.reopen(baudrate=19200)
        assert not t.is_open


# ---------------------------------------------------------------------------
# Forced-timeout knobs — used to exercise error paths without real hardware.
# ---------------------------------------------------------------------------


class TestForcedTimeouts:
    @pytest.mark.anyio
    async def test_force_write_timeout_sets_phase_write(self) -> None:
        t = FakeTransport()
        await t.open()
        t.force_write_timeout()
        with pytest.raises(SartoriusTimeoutError) as ei:
            await t.write(_READ_SBN_REQ, timeout=0.1)
        assert ei.value.context.extra.get("phase") == "write"

    @pytest.mark.anyio
    async def test_force_read_timeout_sets_phase_read_for_read_exact(self) -> None:
        t = FakeTransport({_READ_SBN_REQ: _READ_SBN_REPLY})
        await t.open()
        t.force_read_timeout()
        await t.write(_READ_SBN_REQ, timeout=0.1)
        with pytest.raises(SartoriusTimeoutError) as ei:
            await t.read_exact(1, timeout=0.1)
        assert ei.value.context.extra.get("phase") == "read"

    @pytest.mark.anyio
    async def test_force_read_timeout_sets_phase_read_for_read_until(self) -> None:
        t = FakeTransport({b"\x1bP": _SBI_PRINT_LINE})
        await t.open()
        t.force_read_timeout()
        await t.write(b"\x1bP", timeout=0.1)
        with pytest.raises(SartoriusTimeoutError) as ei:
            await t.read_until(b"\r\n", timeout=0.1)
        assert ei.value.context.extra.get("phase") == "read"


# ---------------------------------------------------------------------------
# Label — used in error messages.
# ---------------------------------------------------------------------------


class TestLabel:
    def test_default_label(self) -> None:
        assert FakeTransport().label == "fake://test"

    def test_custom_label(self) -> None:
        assert FakeTransport(label="fake://bal-A").label == "fake://bal-A"
