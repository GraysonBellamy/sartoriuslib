"""Unified API spec acceptance tests (``UNIFIED_API_HANDOFF.md``).

These pin the cross-library contracts the spec sets up: the
``PollSourceAdapter`` shape, ``Balance.snapshot()`` no-I/O semantics,
``SartoriusTransientTransportError`` raise sites, ``to_pint`` coverage
over every ``Unit`` member, and ``Recording`` summary mutability.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

import sartoriuslib
from sartoriuslib import (
    DeviceResult,
    PollSourceAdapter,
    ProtocolKind,
    Recording,
    SartoriusError,
    SartoriusTimeoutError,
    SartoriusTransientTransportError,
    Unit,
    open_device,
)
from sartoriuslib.devices.balance import SartoriusDeviceSnapshot
from sartoriuslib.protocol.xbpi.framing import parse_frame
from sartoriuslib.streaming.recorder import AcquisitionSummary
from sartoriuslib.testing import FakeTransport, build_identify_script, canned_frames
from sartoriuslib.units import to_pint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from sartoriuslib import Reading

# ---------------------------------------------------------------------------
# §F — SartoriusTransientTransportError raise sites.
# ---------------------------------------------------------------------------


class TestTransientTransport:
    def test_framing_underrun_raises_transient_not_frame_error(self) -> None:
        # Frame too short (under MIN_FRAME_SIZE) -> transient
        with pytest.raises(SartoriusTransientTransportError):
            parse_frame(b"\x03\x41\x00")

    def test_framing_underrun_message_matches_capa_pattern(self) -> None:
        # capa used to string-match "frame too short" / "got N bytes"
        # — keep the message stable so the new typed error has the
        # same human-readable shape.
        with pytest.raises(SartoriusTransientTransportError, match="too short") as exc_info:
            parse_frame(b"\x02\x41")
        assert "got 2 bytes" in str(exc_info.value)

    def test_transient_is_a_transport_error_subclass(self) -> None:
        from sartoriuslib.errors import SartoriusTransportError

        assert issubclass(SartoriusTransientTransportError, SartoriusTransportError)
        assert issubclass(SartoriusTransientTransportError, SartoriusError)


# ---------------------------------------------------------------------------
# §E.0 / §E — DeviceResult factories + PollSourceAdapter.
# ---------------------------------------------------------------------------


class TestDeviceResultFactories:
    def test_success_factory(self) -> None:
        r: DeviceResult[int] = DeviceResult.success(7)
        assert r.ok is True
        assert r.value == 7
        assert r.error is None

    def test_failure_factory(self) -> None:
        err = SartoriusTimeoutError("nope")
        r: DeviceResult[int] = DeviceResult.failure(err)
        assert r.ok is False
        assert r.value is None
        assert r.error is err

    def test_keyword_construction_still_works(self) -> None:
        # Spec §E.0: the keyword-construction path stays valid.
        r: DeviceResult[float] = DeviceResult(value=1.5, error=None)
        assert r.ok is True


class TestPollSourceAdapter:
    @pytest.mark.anyio
    async def test_adapter_wraps_balance_poll(self) -> None:
        script = build_identify_script()
        script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            adapter = PollSourceAdapter("b1", bal)
            results = await adapter.poll()
            assert "b1" in results
            assert results["b1"].ok is True
            assert results["b1"].value is not None
        finally:
            await bal.close()

    @pytest.mark.anyio
    async def test_adapter_skips_when_name_not_in_filter(self) -> None:
        script = build_identify_script()
        script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            adapter = PollSourceAdapter("b1", bal)
            results = await adapter.poll(names=["b2", "b3"])
            assert results == {}
        finally:
            await bal.close()

    @pytest.mark.anyio
    async def test_adapter_wraps_sartorius_error_as_failure(self) -> None:
        script = build_identify_script()
        # No TX_READ_NET in script -> FakeTransport raises on poll.
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            adapter = PollSourceAdapter("b1", bal)
            results = await adapter.poll()
            assert "b1" in results
            assert results["b1"].ok is False
            assert results["b1"].error is not None
        finally:
            await bal.close()


# ---------------------------------------------------------------------------
# §H — Balance.snapshot() no-I/O semantics.
# ---------------------------------------------------------------------------


class TestSnapshot:
    @pytest.mark.anyio
    async def test_snapshot_returns_typed_subclass(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            snap = await bal.snapshot()
            assert isinstance(snap, SartoriusDeviceSnapshot)
            assert snap.protocol is ProtocolKind.XBPI
            assert snap.connected is True
            assert snap.recoverable_error_count == 0
            assert snap.captured_at.tzinfo is not None
        finally:
            await bal.close()

    @pytest.mark.anyio
    async def test_snapshot_does_no_io(self) -> None:
        # After identify the fake transport has nothing more scripted —
        # calling snapshot must not generate any writes.
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            writes_before = len(transport.writes)
            await bal.snapshot()
            assert len(transport.writes) == writes_before
        finally:
            await bal.close()


# ---------------------------------------------------------------------------
# §K — to_pint covers every Unit member.
# ---------------------------------------------------------------------------


class TestToPint:
    def test_every_unit_member_is_mapped(self) -> None:
        for member in Unit:
            value = to_pint(member)
            assert value is None or isinstance(value, str)

    def test_to_pint_accepts_string_value(self) -> None:
        assert to_pint("g") == "gram"
        assert to_pint("kg") == "kilogram"

    def test_to_pint_accepts_enum_value(self) -> None:
        assert to_pint(Unit.MG) == "milligram"

    def test_to_pint_unknown_string_returns_none(self) -> None:
        assert to_pint("not_a_unit") is None

    def test_to_pint_none_returns_none(self) -> None:
        assert to_pint(None) is None

    def test_exotic_units_are_lossy(self) -> None:
        # Hong Kong tael / Austrian carat / momme / tola — pint doesn't
        # model these; spec §K says return None rather than fake it.
        assert to_pint(Unit.TAEL_HK) is None
        assert to_pint(Unit.CT_AU) is None
        assert to_pint(Unit.MOMME) is None
        assert to_pint(Unit.TOLA) is None


# ---------------------------------------------------------------------------
# §M — Recording wrapper + AcquisitionSummary mutability.
# ---------------------------------------------------------------------------


class TestRecordingContract:
    def test_acquisition_summary_is_mutable(self) -> None:
        # Spec §M demands the recorder be the sole writer of a mutable
        # summary — assert it isn't frozen.
        now = datetime.now()
        summary = AcquisitionSummary(started_at=now)
        summary.samples_emitted = 5
        summary.samples_late = 2
        summary.max_drift_ms = 1.5
        summary.finished_at = now
        assert summary.samples_emitted == 5

    def test_recording_carries_rate_hz_and_summary(self) -> None:
        async def _empty_stream() -> AsyncIterator[int]:
            values: tuple[int, ...] = ()
            for value in values:
                yield value

        # Build a dummy iterator just for shape — Recording is a struct.
        summary = AcquisitionSummary(started_at=datetime.now())
        rec: Recording[int] = Recording(
            stream=_empty_stream(),
            summary=summary,
            rate_hz=10.0,
        )
        assert rec.rate_hz == 10.0
        assert rec.summary is summary
        assert rec.observed_rate_hz is None

    @pytest.mark.anyio
    async def test_record_yields_recording_with_live_summary(self) -> None:
        from sartoriuslib.streaming import record

        class _Source:
            async def poll(
                self,
                names: Sequence[str] | None = None,
            ) -> Mapping[str, DeviceResult[Reading]]:
                del names
                return {}

        async with record(_Source(), rate_hz=50.0, duration=0.06) as recording:
            assert isinstance(recording, Recording)
            assert recording.rate_hz == 50.0
            assert recording.summary.finished_at is None
            # Drain the empty stream to let the producer make progress.
            async for _ in recording.stream:
                pass
        # On exit the summary is finalised.
        assert recording.summary.finished_at is not None


# ---------------------------------------------------------------------------
# §G — ErrorContext.address property mirrors sbn_address.
# ---------------------------------------------------------------------------


class TestErrorContextAddress:
    def test_address_property_returns_sbn_address(self) -> None:
        from sartoriuslib.errors import ErrorContext

        ctx = ErrorContext(sbn_address=0x09)
        assert ctx.address == 0x09

    def test_address_is_none_when_sbn_address_is_none(self) -> None:
        from sartoriuslib.errors import ErrorContext

        ctx = ErrorContext()
        assert ctx.address is None


# ---------------------------------------------------------------------------
# Cross-lib import-symmetry smoke (§6 acceptance).
# ---------------------------------------------------------------------------


class TestCrossLibImportSymmetry:
    def test_unified_surface_importable_from_top_level(self) -> None:
        # The same imports must work on every sibling library.
        for name in (
            "open_device",
            "find_devices",
            "sample_to_row",
            "PollSourceAdapter",
            "Recording",
            "DeviceResult",
            "DiscoveryResult",
            "DiscoverySummary",
            "SartoriusTransientTransportError",
            "to_pint",
        ):
            assert hasattr(sartoriuslib, name), f"missing top-level export: {name!r}"
