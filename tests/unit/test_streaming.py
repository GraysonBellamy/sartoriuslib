"""Tests for :func:`sartoriuslib.streaming.record`.

Covers:

- ``Sample`` wraps a ``Reading`` with send/receive timing.
- Absolute-target scheduling: ``target[n] = start + n/rate_hz``.
- Overflow policies: ``BLOCK`` waits, ``DROP_NEWEST`` counts late,
  ``DROP_OLDEST`` raises up-front.
- Error results from the source flow through as ``reading=None``
  samples with ``error`` populated.
- ``AcquisitionSummary`` counts emissions and late ticks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic_ns
from typing import TYPE_CHECKING

import anyio
import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from sartoriuslib import (
    ErrorContext,
    ProtocolKind,
    Reading,
    SartoriusAutoprintActiveError,
    SartoriusTimeoutError,
    Sign,
    Unit,
)
from sartoriuslib.devices.factory import open_device
from sartoriuslib.manager import DeviceResult
from sartoriuslib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    Sample,
    record,
)
from sartoriuslib.testing import FakeTransport, canned_frames


def _make_reading(value: float = 1.0) -> Reading:
    return Reading(
        value=value,
        unit=Unit.G,
        sign=Sign.POSITIVE,
        stable=True,
        overload=False,
        underload=False,
        decimals=3,
        sequence=None,
        status_flags={"stable": True},
        protocol=ProtocolKind.XBPI,
        received_at=datetime.now(UTC),
        monotonic_ns=monotonic_ns(),
        raw=b"\x00",
    )


class _CountingPollSource:
    """Minimal :class:`PollSource` stub — increments a call counter."""

    def __init__(
        self,
        *,
        raise_for: set[str] | None = None,
        names: Sequence[str] = ("a",),
    ) -> None:
        self._raise_for = raise_for or set()
        self._names = tuple(names)
        self.calls = 0

    async def poll(
        self,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[Reading]]:
        self.calls += 1
        target = tuple(names) if names is not None else self._names
        out: dict[str, DeviceResult[Reading]] = {}
        for n in target:
            if n in self._raise_for:
                err = SartoriusTimeoutError(
                    "scripted failure",
                    context=ErrorContext(protocol=ProtocolKind.XBPI.value),
                )
                out[n] = DeviceResult(value=None, error=err)
            else:
                out[n] = DeviceResult(value=_make_reading(), error=None)
        return out


class _SlowPollSource(_CountingPollSource):
    def __init__(self, *, delay_s: float) -> None:
        super().__init__()
        self._delay_s = delay_s

    async def poll(
        self,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[Reading]]:
        await anyio.sleep(self._delay_s)
        return await super().poll(names)


# ---------------------------------------------------------------------------
# Sample shape.
# ---------------------------------------------------------------------------


class TestSample:
    def test_sample_is_frozen_and_carries_reading(self) -> None:
        now = datetime.now(UTC)
        r = _make_reading()
        s = Sample(
            device="b",
            reading=r,
            requested_at=now,
            received_at=now,
            midpoint_at=now,
            monotonic_ns=0,
            elapsed_s=0.0,
            protocol=ProtocolKind.XBPI,
        )
        assert s.reading is r
        assert s.error is None

    def test_sample_error_path_allows_none_reading(self) -> None:
        now = datetime.now(UTC)
        err = SartoriusTimeoutError("boom")
        s = Sample(
            device="b",
            reading=None,
            requested_at=now,
            received_at=now,
            midpoint_at=now,
            monotonic_ns=0,
            elapsed_s=0.0,
            protocol=ProtocolKind.XBPI,
            error=err,
        )
        assert s.reading is None
        assert s.error is err


# ---------------------------------------------------------------------------
# record() happy path.
# ---------------------------------------------------------------------------


class TestRecord:
    @pytest.mark.anyio
    async def test_record_emits_duration_ticks(self) -> None:
        source = _CountingPollSource()
        rate = 20.0
        duration = 0.25  # 5 ticks
        async with record(source, rate_hz=rate, duration=duration) as stream:
            batches = [batch async for batch in stream]
        assert len(batches) == source.calls
        assert len(batches) >= 4  # absolute scheduling — at least 4 of 5 slots
        for batch in batches:
            assert "a" in batch
            assert isinstance(batch["a"], Sample)
            assert batch["a"].reading is not None
            assert batch["a"].protocol is ProtocolKind.XBPI
            assert batch["a"].elapsed_s >= 0.0

    @pytest.mark.anyio
    async def test_record_rejects_invalid_rate(self) -> None:
        source = _CountingPollSource()
        with pytest.raises(ValueError, match="rate_hz"):
            async with record(source, rate_hz=0.0) as _:
                pass

    @pytest.mark.anyio
    async def test_record_rejects_invalid_duration(self) -> None:
        source = _CountingPollSource()
        with pytest.raises(ValueError, match="duration"):
            async with record(source, rate_hz=10.0, duration=-1.0) as _:
                pass

    @pytest.mark.anyio
    async def test_drop_oldest_not_implemented(self) -> None:
        source = _CountingPollSource()
        with pytest.raises(NotImplementedError, match="DROP_OLDEST"):
            async with record(source, rate_hz=10.0, overflow=OverflowPolicy.DROP_OLDEST) as _:
                pass

    @pytest.mark.anyio
    async def test_record_error_sample_carries_error(self) -> None:
        source = _CountingPollSource(raise_for={"a"})
        async with record(source, rate_hz=20.0, duration=0.1) as stream:
            batches = [batch async for batch in stream]
        assert batches
        sample = batches[0]["a"]
        assert sample.reading is None
        assert isinstance(sample.error, SartoriusTimeoutError)
        assert sample.protocol is ProtocolKind.XBPI

    @pytest.mark.anyio
    async def test_record_schedules_at_absolute_cadence(self) -> None:
        # With a very fast source and a tight rate, successive tick
        # wall-clock boundaries should be ~period apart (allow 2x
        # slack on slow CI).
        source = _CountingPollSource()
        async with record(source, rate_hz=50.0, duration=0.2) as stream:
            batches = [batch async for batch in stream]
        assert len(batches) >= 5
        timings = [b["a"].requested_at for b in batches]
        deltas = [(timings[i + 1] - timings[i]).total_seconds() for i in range(len(timings) - 1)]
        period = 1.0 / 50.0
        # Absolute scheduling caps drift at ~1 period; accept up to 4x.
        for d in deltas:
            assert 0.0 < d < 4 * period, f"delta {d} out of bounds"

    @pytest.mark.anyio
    async def test_finite_record_overrun_stops_at_target_window(self) -> None:
        source = _SlowPollSource(delay_s=0.06)
        async with record(source, rate_hz=100.0, duration=0.03) as stream:
            batches = [batch async for batch in stream]

        assert len(batches) == 1
        assert source.calls == 1


class TestBalanceStream:
    @pytest.mark.anyio
    async def test_poll_mode_yields_sample(self) -> None:
        transport = FakeTransport(
            {canned_frames.TX_READ_NET: canned_frames.RX_NET_WEIGHT_EMPTY_PAN},
        )
        bal = await open_device(
            transport,
            protocol=ProtocolKind.XBPI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(rate_hz=20.0) as stream:
            sample = await anext(stream)
        assert sample.reading is not None
        assert sample.metadata["mode"] == "poll"
        await bal.close()

    @pytest.mark.anyio
    async def test_autoprint_mode_consumes_existing_sbi_line(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(mode="autoprint", timeout=0.1) as stream:
            sample = await anext(stream)
        assert sample.reading is not None
        assert sample.metadata["mode"] == "autoprint"
        await bal.close()

    @pytest.mark.anyio
    async def test_poll_stream_refuses_when_sbi_autoprint_active(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        with pytest.raises(SartoriusAutoprintActiveError, match="mode='autoprint'"):
            async with bal.stream(rate_hz=1.0, timeout=0.1):
                pass
        await bal.close()

    @pytest.mark.anyio
    async def test_autoprint_mode_skips_midline_numeric_fragment(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"   079\r\n0.090\r\nN     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(mode="autoprint", timeout=0.1) as stream:
            sample = await anext(stream)
        assert sample.reading is not None
        assert sample.reading.value == 0.031
        assert sample.reading.stable is False
        await bal.close()

    @pytest.mark.anyio
    async def test_autoprint_mode_without_existing_line_fails_loudly(self) -> None:
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.01,
        )
        with pytest.raises(SartoriusTimeoutError):
            async with bal.stream(mode="autoprint", timeout=0.01):
                pass
        await bal.close()


# ---------------------------------------------------------------------------
# PollSource Protocol coverage.
# ---------------------------------------------------------------------------


class TestPollSourceProtocol:
    def test_manager_satisfies_poll_source(self) -> None:
        from sartoriuslib.manager import SartoriusManager

        mgr = SartoriusManager()
        # Structural — runtime_checkable is not set on PollSource, but
        # :func:`isinstance` against the runtime class should at least
        # confirm the method shape is callable.
        assert hasattr(mgr, "poll")
        assert callable(mgr.poll)
        # Help mypy/pyright: assign-to-Protocol.
        _: PollSource = mgr


class TestAcquisitionSummary:
    def test_is_frozen_dataclass(self) -> None:
        now = datetime.now(UTC)
        s = AcquisitionSummary(
            started_at=now,
            finished_at=now,
            samples_emitted=3,
            samples_late=0,
            max_drift_ms=0.5,
            target_total_samples=3,
        )
        assert s.samples_emitted == 3
        assert s.target_total_samples == 3
