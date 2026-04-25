"""Forced-SBI open detects already-enabled autoprint.

Validates the passive autoprint sniff that runs at
:func:`open_device` time when ``protocol=ProtocolKind.SBI``:

- Detection sets :attr:`Session.sbi_autoprint_active` without writing.
- The sniffed line is preserved and consumed by the first
  :meth:`Balance.poll` (no lost samples).
- ``identify=True`` refuses pre-I/O when autoprint is detected.
- Command/reply APIs (``raw_sbi`` with ``expect_lines>0``) refuse with
  :class:`SartoriusAutoprintActiveError` while autoprint is active.
- No-reply control tokens (``ESC T`` / ``ESC V``) still go through —
  they have no reply line to be ambiguous about.
- Sniff handles both fully formatted lines and ``Stat …`` status lines.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusAutoprintActiveError,
    open_device,
)
from sartoriuslib.testing import FakeTransport


async def _open_sbi_with_feed(feed: bytes, *, identify: bool = False, timeout: float = 0.1):
    transport = FakeTransport()
    await transport.open()
    transport.feed(feed)
    await transport.close()
    bal = await open_device(
        transport,
        protocol=ProtocolKind.SBI,
        identify=identify,
        timeout=timeout,
    )
    return bal, transport


class TestAutoprintDetect:
    @pytest.mark.anyio
    async def test_sniff_marks_session_active_without_writing(self) -> None:
        bal, transport = await _open_sbi_with_feed(b"+     0.00 g  \r\n")
        assert bal.session.sbi_autoprint_active is True
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_sniffed_line_is_preserved_for_first_poll(self) -> None:
        bal, transport = await _open_sbi_with_feed(b"+     1.23 g  \r\n")
        reading = await bal.poll()
        assert reading.value == 1.23
        assert reading.protocol is ProtocolKind.SBI
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_identify_true_refuses_when_autoprint_detected(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        with pytest.raises(SartoriusAutoprintActiveError, match="identify"):
            await open_device(
                transport,
                protocol=ProtocolKind.SBI,
                identify=True,
                timeout=0.1,
            )

    @pytest.mark.anyio
    async def test_command_reply_refuses_while_autoprint_active(self) -> None:
        bal, transport = await _open_sbi_with_feed(b"+     0.00 g  \r\n")
        with pytest.raises(SartoriusAutoprintActiveError, match="command replies"):
            await bal.raw_sbi("ESC P")
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_no_reply_control_tokens_still_allowed(self) -> None:
        """``ESC T`` / ``ESC V`` have ``expect_lines=0`` — no ambiguous reply,
        so the autoprint gate lets them through under the normal safety tier."""
        bal, transport = await _open_sbi_with_feed(b"+     0.00 g  \r\n")
        await bal.tare()
        await bal.zero()
        assert transport.writes == (b"\x1bT", b"\x1bV")
        await bal.aclose()

    @pytest.mark.anyio
    async def test_status_line_counts_as_autoprint(self) -> None:
        """A ``Stat Cal.Int.`` line during internal calibration is unsolicited
        output that should flip the session into consume-only mode even though
        it is not a weight line."""
        bal, transport = await _open_sbi_with_feed(b"Stat     Cal.Int.\r\n")
        assert bal.session.sbi_autoprint_active is True
        assert transport.writes == ()
        await bal.aclose()
