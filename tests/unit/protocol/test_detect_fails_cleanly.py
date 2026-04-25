"""``detect_protocol`` raises a clear error when nothing on the wire answers.

Per design §4.3, the detector must "fail clearly" rather than fall back to
opcode sweeps, baud sweeps, or fuzzing. A silent device — one that does
not autoprint, does not reply to the xBPI ``0x02`` probe, does not reply
to ``ESC x1_``, and does not reply to the ``ESC P`` fallback — yields a
single :class:`SartoriusError` carrying the port label and probe
parameters in its :class:`ErrorContext`.
"""

from __future__ import annotations

import math

import pytest

from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.detect import detect_protocol
from sartoriuslib.protocol.sbi.tables import TOKEN_PRINT, TOKEN_TYPE
from sartoriuslib.testing import FakeTransport, canned_frames


@pytest.mark.anyio
async def test_silent_transport_raises_with_port_label() -> None:
    transport = FakeTransport(label="fake://silent")
    await transport.open()

    with pytest.raises(SartoriusError, match="auto-detect") as excinfo:
        await detect_protocol(transport, sniff_window=0.02, timeout=0.05)

    assert excinfo.value.context.port == "fake://silent"
    assert excinfo.value.context.command_name == "auto_detect"


@pytest.mark.anyio
async def test_failure_runs_all_probes_in_order() -> None:
    """Silence runs xBPI READ_MODEL, then SBI ``ESC x1_``, then SBI
    ``ESC P`` fallback (added because some firmwares silently ignore
    Format-2 identity tokens — Cubis MSE on hardware day)."""
    transport = FakeTransport()
    await transport.open()

    with pytest.raises(SartoriusError):
        await detect_protocol(transport, sniff_window=0.02, timeout=0.05)

    assert transport.writes == (
        canned_frames.TX_READ_MODEL,
        TOKEN_TYPE,
        TOKEN_PRINT,
    )


@pytest.mark.anyio
async def test_garbage_replies_to_xbpi_probe_do_not_fool_detection() -> None:
    """An SBI-only device might emit ``?\\r\\n`` (refusal) when fed a binary
    xBPI frame. That is *not* a valid xBPI frame — detection must reject
    it (parse_frame fails on the marker byte) and fall through."""
    transport = FakeTransport(script={canned_frames.TX_READ_MODEL: b"?\r\n"})
    await transport.open()

    with pytest.raises(SartoriusError, match="auto-detect"):
        await detect_protocol(transport, sniff_window=0.02, timeout=0.05)


@pytest.mark.anyio
async def test_failure_includes_probe_parameters_in_context() -> None:
    transport = FakeTransport()
    await transport.open()

    with pytest.raises(SartoriusError) as excinfo:
        await detect_protocol(transport, sniff_window=0.03, timeout=0.07)

    extra = excinfo.value.context.extra
    assert math.isclose(float(extra["sniff_window_s"]), 0.03, rel_tol=1e-6, abs_tol=1e-12)
    assert math.isclose(float(extra["probe_timeout_s"]), 0.07, rel_tol=1e-6, abs_tol=1e-12)
