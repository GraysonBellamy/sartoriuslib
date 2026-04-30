"""Mid-session autoprint state transitions.

Validates the two asymmetric transition paths:

- **Enable mid-session** is detected *opportunistically* — the next SBI
  command/reply call that observes unsolicited autoprint output flips
  the session into consume-only mode and surfaces
  :class:`SartoriusAutoprintActiveError`. The library does not poll
  passively just to spot this transition; it reacts to the surprise
  reply when it happens.
- **Disable mid-session** is detected *explicitly* via
  :meth:`Balance.refresh_sbi_autoprint_state`. A quiet line clears the
  flag and SBI command/reply APIs become available again; observed
  output keeps the consume-only mode and preserves the line for the
  next read.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusAutoprintActiveError,
    open_device,
)
from sartoriuslib.testing import FakeTransport


class TestAutoprintEnableMidSession:
    @pytest.mark.anyio
    async def test_command_reply_flips_session_into_consume_mode(self) -> None:
        """An identify call that sees an unsolicited weight line raises and
        leaves the session in autoprint-active mode."""
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        assert bool(bal.session.sbi_autoprint_active) is False

        transport.feed(b"N     +    0.031    \r\n")
        with pytest.raises(SartoriusAutoprintActiveError, match="command replies"):
            await bal.identify()

        assert bool(bal.session.sbi_autoprint_active) is True
        # The surprise line is queued, so the next poll consumes it without
        # writing.
        reading = await bal.poll()
        assert reading.value == 0.031
        # identify wrote one ESC x1_ token before noticing the surprise.
        assert transport.writes == (b"\x1bx1_",)
        await bal.close()

    @pytest.mark.anyio
    async def test_subsequent_command_reply_blocked_pre_io(self) -> None:
        """Once the session knows autoprint is active, further command/reply
        calls refuse pre-I/O without writing.

        ``ESC P`` legitimately returns weight lines so it does not surprise;
        we use ``ESC x1_`` (read-model) for the initial transition, which
        carries the autoprint-detection contract for non-PRINT tokens.
        """
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        transport.feed(b"+     0.00 g  \r\n")
        with pytest.raises(SartoriusAutoprintActiveError):
            await bal.raw_sbi("ESC x1_")
        assert bool(bal.session.sbi_autoprint_active) is True
        # Drop the queued surprise line so the second attempt is purely a
        # pre-I/O refusal.
        await bal.poll()
        before = transport.writes
        with pytest.raises(SartoriusAutoprintActiveError, match="command replies"):
            await bal.raw_sbi("ESC x1_")
        assert transport.writes == before
        await bal.close()


class TestAutoprintDisableMidSession:
    @pytest.mark.anyio
    async def test_refresh_on_quiet_line_clears_flag(self) -> None:
        """A quiet refresh after the user disables autoprint clears the flag
        and re-enables SBI command/reply APIs."""
        transport = FakeTransport({b"\x1bP": b"+     1.23 g  \r\n"})
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        assert bool(bal.session.sbi_autoprint_active) is True

        # User disabled autoprint on the front panel; refresh sees a quiet
        # line and clears the flag.
        assert await bal.refresh_sbi_autoprint_state(timeout=0.01) is False
        assert bool(bal.session.sbi_autoprint_active) is False

        # Command/reply APIs work again.
        reply = await bal.raw_sbi("ESC P")
        assert reply.lines[0].reading is not None
        assert reply.lines[0].reading.value == 1.23
        assert transport.writes == (b"\x1bP",)
        await bal.close()

    @pytest.mark.anyio
    async def test_refresh_with_observed_line_keeps_consume_mode(self) -> None:
        """Refresh that still sees autoprint output preserves the line for
        consumption and keeps the consume-only flag set."""
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        transport.feed(b"N     +    0.031    \r\n")
        assert await bal.refresh_sbi_autoprint_state(timeout=0.1) is True
        assert bool(bal.session.sbi_autoprint_active) is True

        reading = await bal.poll()
        assert reading.value == 0.031
        assert transport.writes == ()
        await bal.close()
