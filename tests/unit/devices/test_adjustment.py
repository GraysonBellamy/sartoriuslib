"""Waiting for / aborting an internal adjustment (issue #55).

Most tests drive :func:`sartoriuslib.devices.adjustment.wait_for_adjustment`
with :class:`AdjustmentSimulator` in virtual-clock mode: every status poll
advances simulated time by ``dt`` and the waiter reads the same clock, so
the measured MSE timeline (including its 0.2 s mid-adjust gap) is replayed
deterministically at any poll cadence. A second group goes through the
public ``Balance`` / ``SyncBalance`` surface in real (sped-up) time.
"""

from __future__ import annotations

import struct
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import pytest

from sartoriuslib import (
    AdjustmentResult,
    BalanceState,
    SartoriusAdjustmentAbortedError,
    SartoriusAdjustmentError,
    SartoriusAdjustmentInProgressError,
    SartoriusAdjustmentNotStartedError,
    SartoriusAdjustmentTimeoutError,
    SartoriusConfirmationRequiredError,
    SartoriusError,
    SartoriusProtocolUnsupportedError,
    SartoriusTimeoutError,
    SartoriusTransportError,
    open_device,
)
from sartoriuslib.commands.calibration import InternalAdjustRequest
from sartoriuslib.devices import adjustment as adj
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import build_command, checksum
from sartoriuslib.sync import Sartorius
from sartoriuslib.testing import (
    MSE_ADJUST_ABORT_TAIL_S,
    MSE_INTERNAL_ADJUST_DONE_AT_S,
    AdjustmentSimulator,
    FakeTransport,
    build_cal_record_body,
    build_identify_script,
    build_metrology_script,
)

if TYPE_CHECKING:
    from sartoriuslib import Balance

TX_ADJUST = build_command(0x28, b"\x21\x78")
TX_ABORT = build_command(0x29)
TX_STATUS = build_command(0x30)
TX_RECORD = build_command(0xB9)
OLD_STAMP = datetime(2026, 11, 24, 19, 5, 50)
NEW_STAMP = datetime(2026, 11, 27, 15, 56, 49)
NET_VALUE = 12.345


def _base_script() -> dict[bytes, bytes]:
    script = build_identify_script()
    script.update(build_metrology_script())
    return script


async def _open(
    sim: AdjustmentSimulator, *, latency_s: float = 0.0
) -> tuple[Balance, FakeTransport]:
    transport = FakeTransport(_base_script(), latency_s=latency_s)
    sim.install(transport)
    bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
    return bal, transport


def _virtual(sim: AdjustmentSimulator, **kw: Any) -> dict[str, Any]:
    """Waiter kwargs for virtual-clock mode."""
    return {"poll_interval": 0.0, "clock": sim.now, **kw}


async def _adjust(bal: Balance, sim: AdjustmentSimulator, **kw: Any) -> AdjustmentResult:
    return await adj.internal_adjust_and_wait(
        bal.session, InternalAdjustRequest(), confirm=True, **_virtual(sim, **kw)
    )


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


class TestOutcomes:
    @pytest.mark.anyio
    @pytest.mark.parametrize("dt", [0.5, 0.25, 0.1, 0.05, 0.02])
    async def test_success_at_any_poll_cadence(self, dt: float) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=dt, net_value=NET_VALUE)
        bal, transport = await _open(sim)
        res = await _adjust(bal, sim)
        assert res.previous_cal_record.timestamp == OLD_STAMP
        assert res.cal_record.timestamp == NEW_STAMP
        assert res.cal_record.temperature_celsius == pytest.approx(26.91, abs=1e-3)
        assert (
            MSE_INTERNAL_ADJUST_DONE_AT_S
            <= res.duration_s
            <= MSE_INTERNAL_ADJUST_DONE_AT_S + 2 * dt
        )
        assert res.transient_errors == 0
        assert res.finished_at >= res.started_at
        assert sim.completed == 1
        assert TX_ABORT not in transport.writes
        # The fresh record is what a follow-up read sees (no stale cache).
        assert (await bal.last_cal_record()).raw == res.cal_record.raw
        status = await bal.status()
        assert status.state is not BalanceState.ADJUSTING
        assert status.isocal_due is False  # a completed adjust clears isoCAL-due
        await bal.close()

    @pytest.mark.anyio
    async def test_mid_adjust_gap_needs_the_settle_window(self) -> None:
        """Regression for the bench finding: without debounce the 0.2 s gap
        (state 0x88, status bit 0x20) is misread as 'ended, no new record'."""
        sim = AdjustmentSimulator(step_per_status_poll=0.05)
        bal, _ = await _open(sim)
        with pytest.raises(SartoriusAdjustmentAbortedError) as ei:
            await _adjust(bal, sim, settle=0.0)
        assert ei.value.context.extra["raw_state"] == 0x88
        assert ei.value.context.extra["raw_status"] & 0x20
        assert sim.running  # the balance was still adjusting
        await bal.close()

    @pytest.mark.anyio
    async def test_abort_from_another_task(self) -> None:
        """capa's Cancel button: abort_adjustment() while the card awaits."""
        sim = AdjustmentSimulator(step_per_status_poll=0.05)
        bal, _ = await _open(sim)
        out: dict[str, Any] = {}

        async def waiter() -> None:
            try:
                await _adjust(bal, sim)
            except SartoriusAdjustmentAbortedError as exc:
                out["exc"] = exc

        async with anyio.create_task_group() as tg:
            tg.start_soon(waiter)
            while sim.now() < 8.0:
                await anyio.lowlevel.checkpoint()
            out["aborted"] = await adj.abort_adjustment(bal.session)

        assert out["aborted"] is True
        extra = out["exc"].context.extra
        assert extra["seen_adjusting"] is True
        assert extra["elapsed_s"] >= 8.0 + MSE_ADJUST_ABORT_TAIL_S + adj.SETTLE_S - 0.2
        assert sim.aborted == 1
        assert (await bal.last_cal_record()).timestamp == OLD_STAMP
        assert await adj.abort_adjustment(bal.session) is False  # nothing running now
        await bal.close()

    @pytest.mark.anyio
    async def test_timeout_leaves_the_balance_running(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25, outcome="hang")
        bal, transport = await _open(sim)
        with pytest.raises(SartoriusAdjustmentTimeoutError) as ei:
            await _adjust(bal, sim, timeout=60.0)
        assert ei.value.context.extra["seen_adjusting"] is True
        assert TX_ABORT not in transport.writes
        assert sim.running
        await bal.close()

    @pytest.mark.anyio
    async def test_ack_but_never_starts(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.1, outcome="ignore")
        bal, _ = await _open(sim)
        with pytest.raises(SartoriusAdjustmentNotStartedError):
            await _adjust(bal, sim)
        assert sim.now() >= adj.START_GRACE_S  # waited out the grace window
        await bal.close()

    @pytest.mark.anyio
    async def test_refuses_while_isocal_runs(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25)
        bal, transport = await _open(sim)
        sim.start()  # the balance began its own isoCAL
        with pytest.raises(SartoriusAdjustmentInProgressError):
            await _adjust(bal, sim)
        assert TX_ADJUST not in transport.writes
        await bal.close()

    @pytest.mark.anyio
    async def test_confirm_gate_before_any_io(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25)
        bal, transport = await _open(sim)
        n = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await adj.internal_adjust_and_wait(
                bal.session, InternalAdjustRequest(), confirm=False, **_virtual(sim)
            )
        assert len(transport.writes) == n
        await bal.close()

    def test_errors_are_not_transport_errors(self) -> None:
        """Connection-retry logic catching transport errors must not see these."""
        for cls in (
            SartoriusAdjustmentTimeoutError,
            SartoriusAdjustmentAbortedError,
            SartoriusAdjustmentNotStartedError,
            SartoriusAdjustmentInProgressError,
        ):
            assert issubclass(cls, SartoriusAdjustmentError)
            assert issubclass(cls, SartoriusError)
            assert not issubclass(cls, SartoriusTransportError)
            assert not issubclass(cls, SartoriusTimeoutError)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


class TestRobustness:
    @pytest.mark.anyio
    async def test_silent_polls_are_retried(self) -> None:
        sim = AdjustmentSimulator(
            step_per_status_poll=0.25, poll_faults=dict.fromkeys((5, 6, 7), "silent")
        )
        bal, _ = await _open(sim)
        res = await _adjust(bal, sim)
        assert res.transient_errors == 3
        await bal.close()

    @pytest.mark.anyio
    async def test_silence_across_the_end(self) -> None:
        """Balance stops answering for the last ~3 s and comes back idle + new record."""
        sim = AdjustmentSimulator(
            step_per_status_poll=0.25, poll_faults=dict.fromkeys(range(86, 99), "silent")
        )
        bal, _ = await _open(sim)
        res = await _adjust(bal, sim)
        assert res.transient_errors == 13
        assert res.cal_record.timestamp == NEW_STAMP
        await bal.close()

    @pytest.mark.anyio
    async def test_partial_frames_are_drained(self) -> None:
        sim = AdjustmentSimulator(
            step_per_status_poll=0.25,
            net_value=NET_VALUE,
            poll_faults={4: "partial", 30: "partial"},
        )
        bal, _ = await _open(sim)
        res = await _adjust(bal, sim)
        assert res.transient_errors == 2
        assert (await bal.poll()).value == pytest.approx(NET_VALUE)  # nothing left over
        await bal.close()

    @pytest.mark.anyio
    async def test_busy_replies_are_retried(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25, poll_faults={2: "busy", 9: "busy"})
        bal, _ = await _open(sim)
        res = await _adjust(bal, sim)
        assert res.transient_errors == 2
        await bal.close()

    @pytest.mark.anyio
    @pytest.mark.parametrize("attach_at", [3.0, 6.0, 20.0])  # 6.0 s is inside the gap
    async def test_reattach_without_baseline(self, attach_at: float) -> None:
        """GUI reopened mid-cal: wait_for_adjustment() reads its own baseline."""
        sim = AdjustmentSimulator(step_per_status_poll=0.05)
        bal, _ = await _open(sim)
        await bal.internal_adjust(confirm=True)
        while sim.now() < attach_at:
            await bal.status()
        res = await adj.wait_for_adjustment(bal.session, **_virtual(sim))
        assert res.previous_cal_record.timestamp == OLD_STAMP
        assert res.cal_record.timestamp == NEW_STAMP
        await bal.close()

    @pytest.mark.anyio
    async def test_wait_with_nothing_running(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25)
        bal, _ = await _open(sim)
        with pytest.raises(SartoriusAdjustmentNotStartedError) as ei:
            await adj.wait_for_adjustment(bal.session, **_virtual(sim))
        assert ei.value.context.extra["idle_for_s"] >= adj.SETTLE_S
        await bal.close()

    @pytest.mark.anyio
    async def test_consecutive_adjustments_each_get_a_new_record(self) -> None:
        sim = AdjustmentSimulator(step_per_status_poll=0.25)
        bal, _ = await _open(sim)
        first = await _adjust(bal, sim)
        second = await _adjust(bal, sim)
        assert second.previous_cal_record.raw == first.cal_record.raw
        assert second.cal_record.timestamp is not None
        assert first.cal_record.timestamp is not None
        assert second.cal_record.timestamp > first.cal_record.timestamp
        await bal.close()

    @pytest.mark.anyio
    async def test_weight_stream_keeps_running_and_flags_fake_readings(self) -> None:
        """capa keeps polling weight while the card awaits the cal. In-adjust
        readings (off-scale / fake 1300 g / 0 g) all have flags bit 0x04 clear."""
        sim = AdjustmentSimulator(step_per_status_poll=0.05, net_value=NET_VALUE)
        bal, _ = await _open(sim)
        seen: list[tuple[bool, int, float | None]] = []

        async def stream(stop: anyio.Event) -> None:
            while not stop.is_set():
                r = await bal.poll()
                seen.append((sim.running, r.raw[10], r.value))
                await anyio.lowlevel.checkpoint()

        stop = anyio.Event()
        res: AdjustmentResult | None = None
        async with anyio.create_task_group() as tg:
            tg.start_soon(stream, stop)
            res = await _adjust(bal, sim)
            stop.set()
        assert res is not None
        assert res.cal_record.timestamp == NEW_STAMP
        during = [(fl, v) for running, fl, v in seen if running]
        assert during
        assert all(not fl & 0x04 for fl, _ in during)
        assert {v for _, v in during} <= {None, 1300.0, 0.0}
        await bal.close()

    @pytest.mark.anyio
    async def test_unknown_status_encoding_falls_back_to_the_cal_record(self) -> None:
        """Non-Cubis status (no 0x80 base, adjusting=None): only 0xB9 can end the wait."""
        polls = 0

        def status(_: bytes) -> bytes:
            nonlocal polls
            polls += 1
            return _rx(0x48, bytes([0, 0, 0x81, 0x08, 0x20, 0x10, 0, polls & 0xFF]))

        old = build_cal_record_body(temperature_celsius=20.0, timestamp=OLD_STAMP)
        new = build_cal_record_body(temperature_celsius=20.5, timestamp=NEW_STAMP)
        transport = FakeTransport(_base_script())
        transport.add_script(TX_STATUS, status)
        transport.add_script(TX_RECORD, lambda _: _rx(0x51, new if polls >= 6 else old))
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        res = await adj.wait_for_adjustment(
            bal.session, poll_interval=0.0, settle=0.0, clock=lambda: float(polls)
        )
        assert res.cal_record.timestamp == NEW_STAMP
        await bal.close()

    @pytest.mark.anyio
    async def test_requires_xbpi(self) -> None:
        session: Any = SimpleNamespace(active_protocol=ProtocolKind.SBI)
        with pytest.raises(SartoriusProtocolUnsupportedError):
            await adj.wait_for_adjustment(session)
        with pytest.raises(SartoriusProtocolUnsupportedError):
            await adj.abort_adjustment(session)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    @pytest.mark.anyio
    async def test_cancel_mid_exchange_leaves_the_port_clean(self) -> None:
        """Cancel lands after a 0x30 write, before its reply is read. The poll is
        shielded, so the reply is consumed and the next command is not desynced."""
        sim = AdjustmentSimulator(step_per_status_poll=0.25, outcome="hang")
        bal, transport = await _open(sim, latency_s=0.01)
        await bal.internal_adjust(confirm=True)
        written = anyio.Event()
        sim.on_status_request = written.set

        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as tg:
                tg.start_soon(lambda: adj.wait_for_adjustment(bal.session, **_virtual(sim)))
                await written.wait()
                scope.cancel()

        reading = await bal.poll()
        assert reading.raw[4:6] != b"\x00\x81"  # not the orphaned status block ...
        assert reading.value in (None, 1300.0, 0.0)  # ... but a genuine in-adjust 0x1E reply
        assert TX_ABORT not in transport.writes
        assert sim.running  # cancelling the wait does not stop the balance
        await bal.close()


# ---------------------------------------------------------------------------
# Public surface (real, sped-up time)
# ---------------------------------------------------------------------------


class TestBalanceSurface:
    @pytest.mark.anyio
    async def test_internal_adjust_wait_true(self) -> None:
        sim = AdjustmentSimulator(speed=400.0)
        bal, _ = await _open(sim)
        res = await bal.internal_adjust(confirm=True, wait=True, poll_interval=0.01)
        assert isinstance(res, AdjustmentResult)
        assert res.cal_record.timestamp == NEW_STAMP
        await bal.close()

    @pytest.mark.anyio
    async def test_internal_adjust_default_returns_at_the_ack(self) -> None:
        """wait=False keeps the old behaviour: one 0x28, no pre-flight reads."""
        sim = AdjustmentSimulator(speed=1.0)
        bal, transport = await _open(sim)
        n = len(transport.writes)
        await bal.internal_adjust(confirm=True)
        assert transport.writes[n:] == (TX_ADJUST,)
        assert sim.running
        await bal.close()

    @pytest.mark.anyio
    async def test_wait_for_adjustment_and_abort(self) -> None:
        sim = AdjustmentSimulator(speed=400.0)
        bal, _ = await _open(sim)
        await bal.internal_adjust(confirm=True)
        res = await bal.wait_for_adjustment(poll_interval=0.01)
        assert res.cal_record.timestamp == NEW_STAMP
        assert await bal.abort_adjustment() is False
        await bal.close()

    @pytest.mark.anyio
    async def test_abort_adjustment_needs_no_confirm(self) -> None:
        sim = AdjustmentSimulator(speed=1.0)
        bal, transport = await _open(sim)
        await bal.internal_adjust(confirm=True)
        assert await bal.abort_adjustment() is True
        assert TX_ABORT in transport.writes
        await bal.close()

    def test_sync_balance(self) -> None:
        sim = AdjustmentSimulator(speed=400.0)
        transport = FakeTransport(_base_script())
        sim.install(transport)
        with Sartorius.open(transport, timeout=0.1) as bal:
            res = bal.internal_adjust(confirm=True, wait=True, poll_interval=0.01)
            assert res is not None
            assert res.cal_record.timestamp == NEW_STAMP
            assert bal.abort_adjustment() is False
            bal.internal_adjust(confirm=True)
            assert bal.wait_for_adjustment(poll_interval=0.01).cal_record.raw != res.cal_record.raw


# ---------------------------------------------------------------------------
# Simulator helpers
# ---------------------------------------------------------------------------


class TestSimulatorHelpers:
    def test_cal_record_body_round_trips(self) -> None:
        from sartoriuslib.devices.models import CalRecord

        body = build_cal_record_body(temperature_celsius=22.4, timestamp=NEW_STAMP)
        assert len(body) == 17
        rec = CalRecord(
            temperature_celsius=struct.unpack(">f", body[:4])[0],
            signature=body[4:13],
            counters=body[13:16],
            padding=body[16],
            raw=body,
        )
        assert rec.timestamp == NEW_STAMP
        empty = build_cal_record_body(temperature_celsius=None, timestamp=None)
        assert empty == bytes(17)

    def test_speed_must_be_positive(self) -> None:
        from sartoriuslib.errors import SartoriusValidationError

        with pytest.raises(SartoriusValidationError):
            AdjustmentSimulator(speed=0.0)


def _rx(subtype: int, body: bytes) -> bytes:
    pre = bytes([1 + 1 + len(body) + 1, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])
