"""``detect_protocol`` resolves to xBPI when the device answers READ_MODEL.

The detector probes ``0x02`` (READ_MODEL) and accepts any well-formed
length-prefixed reply as proof of xBPI capability — even an
error-subtype reply still proves the device speaks the protocol.
"""

from __future__ import annotations

import pytest

from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.testing import FakeTransport, canned_frames


@pytest.mark.anyio
async def test_xbpi_probe_succeeds_on_well_formed_reply() -> None:
    transport = FakeTransport(
        script={canned_frames.TX_READ_MODEL: canned_frames.RX_MODEL_MSE},
    )
    await transport.open()

    result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

    assert result.protocol is ProtocolKind.XBPI
    assert result.autoprint_active is False
    assert result.pending_lines == ()


@pytest.mark.anyio
async def test_xbpi_probe_writes_only_the_probe_frame() -> None:
    """The xBPI win path must not fall through to the SBI probe."""
    transport = FakeTransport(
        script={canned_frames.TX_READ_MODEL: canned_frames.RX_MODEL_WZA},
    )
    await transport.open()

    await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

    assert transport.writes == (canned_frames.TX_READ_MODEL,)


@pytest.mark.anyio
async def test_xbpi_probe_drains_stale_input_before_writing() -> None:
    """Stale bytes (no CRLF, not autoprint) must not stall the length-prefix read."""
    transport = FakeTransport(
        script={canned_frames.TX_READ_MODEL: canned_frames.RX_MODEL_BCE},
    )
    await transport.open()
    transport.feed(b"\xff\x00\x42")  # stray bytes, no \r\n, not autoprint

    result = await detect_protocol(transport, sniff_window=0.02, timeout=0.2)

    assert result.protocol is ProtocolKind.XBPI


@pytest.mark.anyio
async def test_xbpi_probe_uses_custom_sbn_addresses() -> None:
    """``src_sbn`` and ``dst_sbn`` flow into the probe frame so non-default
    bus addresses still detect."""
    from sartoriuslib.protocol.xbpi.framing import build_command

    custom_tx = build_command(0x02, src_sbn=0x05, dst_sbn=0x0A)
    transport = FakeTransport(script={custom_tx: canned_frames.RX_MODEL_MSE})
    await transport.open()

    result = await detect_protocol(
        transport,
        sniff_window=0.02,
        timeout=0.2,
        src_sbn=0x05,
        dst_sbn=0x0A,
    )

    assert result.protocol is ProtocolKind.XBPI
    assert transport.writes == (custom_tx,)
