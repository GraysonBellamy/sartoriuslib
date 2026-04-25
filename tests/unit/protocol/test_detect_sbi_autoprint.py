"""``detect_protocol`` resolves to SBI when passive sniffing observes autoprint.

A device left in SBI autoprint mode owns the wire and emits unsolicited
weight or status lines. Detection must:

- Recognise the unsolicited line and resolve to SBI without writing
  anything to the device.
- Set ``autoprint_active=True`` on the result so the caller can wire the
  SBI client into consume-only mode.
- Preserve the sniffed line in ``pending_lines`` so the first
  ``Balance.poll()`` does not lose the sample.
"""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.testing import FakeTransport


@pytest.mark.anyio
async def test_weight_autoprint_line_resolves_to_sbi() -> None:
    transport = FakeTransport()
    await transport.open()
    transport.feed(b"+     0.00 g  \r\n")

    result = await detect_protocol(transport, sniff_window=0.05, timeout=0.2)

    assert result.protocol is ProtocolKind.SBI
    assert result.autoprint_active is True
    assert result.pending_lines == (b"+     0.00 g  \r\n",)


@pytest.mark.anyio
async def test_passive_sniff_does_not_write_to_device() -> None:
    """Sniff is read-only — the design requires we never write during it."""
    transport = FakeTransport()
    await transport.open()
    transport.feed(b"N     +    0.031    \r\n")

    await detect_protocol(transport, sniff_window=0.05, timeout=0.2)

    assert transport.writes == ()


@pytest.mark.anyio
async def test_status_line_during_internal_calibration_counts() -> None:
    """``Stat ... Cal.Int.`` is unsolicited output and must trip detection
    even though it is not a weight reading."""
    transport = FakeTransport()
    await transport.open()
    transport.feed(b"Stat     Cal.Int.\r\n")

    result = await detect_protocol(transport, sniff_window=0.05, timeout=0.2)

    assert result.protocol is ProtocolKind.SBI
    assert result.autoprint_active is True
    assert result.pending_lines == (b"Stat     Cal.Int.\r\n",)


@pytest.mark.anyio
async def test_autoprint_short_circuits_before_xbpi_probe() -> None:
    """Detecting autoprint must skip the xBPI probe entirely — writing a
    binary frame to a balance owning the line in autoprint risks confusing
    the operator's downstream tooling."""
    transport = FakeTransport()
    await transport.open()
    transport.feed(b"+     0.00 g  \r\n")

    result = await detect_protocol(transport, sniff_window=0.05, timeout=0.2)

    assert result.protocol is ProtocolKind.SBI
    assert result.autoprint_active is True
    assert transport.writes == ()
