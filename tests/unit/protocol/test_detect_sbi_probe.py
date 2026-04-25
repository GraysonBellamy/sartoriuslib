"""``detect_protocol`` falls through to the SBI identity probe when xBPI is silent.

A balance configured for SBI command/reply (autoprint disabled) emits no
unsolicited output, so the passive sniff returns nothing. The detector
then sends the xBPI READ_MODEL probe — the SBI device has no idea what to
do with binary length-prefixed framing and stays silent. Finally the
detector sends ``ESC x1_`` and accepts any CRLF-terminated reply as proof
of SBI command/reply support, falling back to ``ESC P`` (print weight)
when ``ESC x1_`` is also silent — some firmwares (Cubis MSE on hardware
day) silently ignore Format-2 identity tokens but still answer Format-1
``ESC P``.
"""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.protocol.sbi.tables import TOKEN_PRINT, TOKEN_TYPE
from sartoriuslib.testing import FakeTransport, canned_frames


@pytest.mark.anyio
async def test_silent_sniff_then_sbi_identity_resolves_to_sbi() -> None:
    transport = FakeTransport(script={TOKEN_TYPE: b"WZA8202-N\r\n"})
    await transport.open()

    result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

    assert result.protocol is ProtocolKind.SBI
    # Identity probe is *not* autoprint — the device only spoke when asked.
    assert result.autoprint_active is False
    assert result.pending_lines == ()


@pytest.mark.anyio
async def test_xbpi_probe_runs_before_sbi_probe() -> None:
    """Both probe frames must be sent in order: xBPI first, then SBI."""
    transport = FakeTransport(script={TOKEN_TYPE: b"WZA8202-N\r\n"})
    await transport.open()

    await detect_protocol(transport, sniff_window=0.02, timeout=0.05)

    # xBPI probe was attempted (got no reply); SBI probe followed.
    assert transport.writes == (canned_frames.TX_READ_MODEL, TOKEN_TYPE)


@pytest.mark.anyio
async def test_short_identity_reply_still_counts() -> None:
    """Detection only requires *some* CRLF-terminated reply; the SBI parser
    decodes content separately."""
    transport = FakeTransport(script={TOKEN_TYPE: b"X\r\n"})
    await transport.open()

    result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

    assert result.protocol is ProtocolKind.SBI
    assert result.autoprint_active is False


class TestEscPFallback:
    """Cubis MSE on hardware day silently ignored ``ESC x1_/x2_/x3_`` while
    answering ``ESC P``. The detector falls back to a Format-1 print probe
    so we can still resolve to SBI on those firmwares."""

    @pytest.mark.anyio
    async def test_silent_x1_then_print_resolves_sbi(self) -> None:
        # Only ``ESC P`` has a scripted reply — ``ESC x1_`` falls
        # through silently. Detector should still land on SBI.
        transport = FakeTransport(script={TOKEN_PRINT: b"N     +    0.000 g  \r\n"})
        await transport.open()

        result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

        assert result.protocol is ProtocolKind.SBI
        assert result.autoprint_active is False
        # Both SBI probes were attempted in order, after the xBPI one.
        assert transport.writes == (
            canned_frames.TX_READ_MODEL,
            TOKEN_TYPE,
            TOKEN_PRINT,
        )

    @pytest.mark.anyio
    async def test_x1_succeeds_does_not_send_print(self) -> None:
        """The fallback only runs when the identity probe is silent —
        a healthy ``ESC x1_`` reply must keep ``ESC P`` off the wire."""
        transport = FakeTransport(script={TOKEN_TYPE: b"WZA8202-N\r\n"})
        await transport.open()

        result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

        assert result.protocol is ProtocolKind.SBI
        # ESC P was not sent.
        assert TOKEN_PRINT not in transport.writes
