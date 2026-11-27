"""Waiting for an internal adjustment to finish.

``0x28`` only starts an internal adjustment: the balance ACKs in a few
milliseconds, then cycles its internal weight for ~24 s. This module
watches that run to its end and classifies the outcome. The algorithm is
built on the bench behaviour recorded in ``docs/protocol.md`` §7.7.1
(MSE1203S):

* While adjusting, the Cubis status-block state byte carries bits
  ``0x01``/``0x40`` (:data:`~sartoriuslib.protocol.xbpi.STATE_ADJUSTING_BITS`).
  Both drop out for one ~0.2 s window mid-adjust, so a single idle sample
  proves nothing.
* ``0xB9`` (the cal record) is rewritten at the very end, just before the
  adjust bits clear, and never earlier.
* ``0x29`` mid-adjust makes the balance go idle ~2.5 s later *without*
  rewriting ``0xB9``.

Classification:

=====================================  =========================================
Observation                            Outcome
=====================================  =========================================
idle and ``0xB9`` changed              success (returned immediately)
idle for :data:`SETTLE_S`, no change   :class:`SartoriusAdjustmentAbortedError`
  … and adjusting never seen           :class:`SartoriusAdjustmentNotStartedError`
still adjusting at the deadline        :class:`SartoriusAdjustmentTimeoutError`
=====================================  =========================================

Each poll is one request/response run inside a shielded cancel scope, so
cancelling the waiter (a GUI closing mid-cal) can never leave half a reply
on the port. Cancellation and timeouts never send ``0x29``. Stopping the
balance is :func:`abort_adjustment`'s job, and only when asked.

When the status block does not expose the adjust bits (non-Cubis
encodings, ``adjusting is None``), completion falls back to ``0xB9``
alone: success when it changes, timeout otherwise.

:class:`~sartoriuslib.devices.balance.Balance` exposes this as
``internal_adjust(wait=True)``, ``wait_for_adjustment()`` and
``abort_adjustment()``; the functions here take a
:class:`~sartoriuslib.devices.session.Session` so tests can inject a
clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import anyio

from sartoriuslib._logging import get_logger
from sartoriuslib.commands.calibration import (
    ABORT_ADJUSTMENT,
    INTERNAL_ADJUST,
    LAST_CAL_RECORD,
    AbortAdjustmentRequest,
    InternalAdjustRequest,
    LastCalRecordRequest,
)
from sartoriuslib.commands.status import STATUS_BLOCK, StatusRequest
from sartoriuslib.devices.models import AdjustmentResult
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusAdjustmentAbortedError,
    SartoriusAdjustmentError,
    SartoriusAdjustmentInProgressError,
    SartoriusAdjustmentNotStartedError,
    SartoriusAdjustmentTimeoutError,
    SartoriusConfirmationRequiredError,
    SartoriusFrameError,
    SartoriusOperationNotApplicableError,
    SartoriusParseError,
    SartoriusProtocolUnsupportedError,
    SartoriusTimeoutError,
    SartoriusTransientTransportError,
)
from sartoriuslib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sartoriuslib.devices.models import BalanceStatus, CalRecord
    from sartoriuslib.devices.session import Session

__all__ = [
    "DEFAULT_ADJUST_POLL_INTERVAL_S",
    "DEFAULT_ADJUST_TIMEOUT_S",
    "SETTLE_S",
    "START_GRACE_S",
    "abort_adjustment",
    "internal_adjust_and_wait",
    "wait_for_adjustment",
]

_logger = get_logger("devices.adjustment")

#: Default overall deadline. Every completed bench run took 24.3–24.5 s
#: (``docs/protocol.md`` §7.7.1); the balance waits for stability between
#: phases, so a noisier bench takes longer. 120 s is ~5× the measured time.
DEFAULT_ADJUST_TIMEOUT_S: Final[float] = 120.0

#: Default status poll period. Each poll is ~4 ms of bus time, so this
#: interleaves comfortably with a 50 Hz weight stream on the same port.
DEFAULT_ADJUST_POLL_INTERVAL_S: Final[float] = 0.25

#: Continuous idle (with ``0xB9`` unchanged) needed before an adjustment
#: is declared over. The one mid-adjust idle window measured 0.202 s.
SETTLE_S: Final[float] = 1.0

#: After ``0x28``, how long "not adjusting yet" is tolerated before
#: concluding the balance never started. On the bench the bits were set
#: within 9 ms of the ACK.
START_GRACE_S: Final[float] = 2.0

#: Per-request timeout for the polls. Also bounds how long a shielded
#: poll can delay cancellation.
_POLL_TIMEOUT_S: Final[float] = 0.5

#: Poll failures that only mean "no usable answer this time".
_TRANSIENT_POLL_ERRORS: Final = (
    SartoriusTimeoutError,
    SartoriusTransientTransportError,
    SartoriusFrameError,
    SartoriusParseError,
    SartoriusOperationNotApplicableError,
)


async def _shielded[T](fn: Callable[[], Awaitable[T]]) -> T:
    """Run one exchange that cancellation must not split mid-frame."""
    with anyio.CancelScope(shield=True):
        return await fn()
    # A shielded scope never swallows an exception; this is for the type checkers.
    raise AssertionError("unreachable")  # pragma: no cover


async def _read_status(session: Session, timeout: float) -> BalanceStatus:
    return await _shielded(
        lambda: session.execute(STATUS_BLOCK, StatusRequest(), timeout=timeout),
    )


async def _read_record(session: Session, timeout: float) -> CalRecord:
    return await _shielded(
        lambda: session.execute(LAST_CAL_RECORD, LastCalRecordRequest(), timeout=timeout),
    )


def _require_xbpi(session: Session, op: str) -> None:
    if session.active_protocol is not ProtocolKind.XBPI:
        raise SartoriusProtocolUnsupportedError(
            f"{op}: adjustment tracking needs xBPI (session is "
            f"{session.active_protocol.value.upper()})",
            context=ErrorContext(command_name=op, protocol=session.active_protocol.value),
        )


@dataclass
class _Watch:
    """Classification state for one wait — pure, no I/O."""

    baseline: CalRecord
    start: float
    started_at: datetime
    settle: float
    start_grace: float
    polls: int = 0
    transient_errors: int = 0
    seen_adjusting: bool = False
    idle_since: float | None = None
    last_status: BalanceStatus | None = None

    def wants_record(self, status: BalanceStatus, now: float) -> bool:
        """Whether this idle-looking poll is worth a ``0xB9`` read."""
        if status.adjusting:
            return False
        return (
            self.seen_adjusting or status.adjusting is None or now - self.start >= self.start_grace
        )

    def on_failure(self) -> None:
        self.transient_errors += 1
        self.idle_since = None  # "idle" must be seen on consecutive good polls

    def on_poll(
        self,
        status: BalanceStatus,
        record: CalRecord | None,
        now: float,
    ) -> AdjustmentResult | None:
        """Fold one successful poll in; return the result once finished.

        Raises the aborted / not-started errors once idle has lasted
        ``settle`` seconds with the cal record unchanged.
        """
        if self.last_status is None or status.raw_state != self.last_status.raw_state:
            _logger.debug(
                "adjust.state",
                extra={"raw_state": status.raw_state, "t_s": round(now - self.start, 3)},
            )
        self.last_status = status
        if status.adjusting:
            self.seen_adjusting = True
            self.idle_since = None
            return None
        if self.idle_since is None:
            self.idle_since = now
        if record is None:
            return None
        if record.raw != self.baseline.raw:
            return AdjustmentResult(
                cal_record=record,
                previous_cal_record=self.baseline,
                started_at=self.started_at,
                finished_at=datetime.now(UTC),
                duration_s=now - self.start,
                polls=self.polls,
                transient_errors=self.transient_errors,
            )
        # Bits unknown (non-Cubis): only a changed 0xB9 can end the wait.
        idle_for = now - self.idle_since
        if status.adjusting is None or idle_for < self.settle:
            return None
        ctx = self.context(now, idle_for_s=round(idle_for, 3))
        if self.seen_adjusting:
            raise SartoriusAdjustmentAbortedError(
                "internal adjustment ended without writing a new cal record "
                "(aborted via 0x29 or the front panel, or failed on the balance)",
                context=ctx,
            )
        raise SartoriusAdjustmentNotStartedError("no internal adjustment is running", context=ctx)

    def context(self, now: float, **extra: object) -> ErrorContext:
        last = self.last_status
        return ErrorContext(
            command_name="wait_for_adjustment",
            extra={
                "polls": self.polls,
                "transient_errors": self.transient_errors,
                "elapsed_s": round(now - self.start, 3),
                "seen_adjusting": self.seen_adjusting,
                "raw_state": last.raw_state if last else None,
                "raw_status": last.raw_status if last else None,
                **extra,
            },
        )


async def wait_for_adjustment(
    session: Session,
    *,
    timeout: float = DEFAULT_ADJUST_TIMEOUT_S,
    poll_interval: float = DEFAULT_ADJUST_POLL_INTERVAL_S,
    baseline: CalRecord | None = None,
    start_grace: float = 0.0,
    settle: float = SETTLE_S,
    poll_timeout: float = _POLL_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    t0: float | None = None,
    started_at: datetime | None = None,
) -> AdjustmentResult:
    """Poll until the running adjustment has ended, then classify it.

    Arguments:
        session: The balance's session (xBPI).
        timeout: Overall deadline in seconds, measured from ``t0``.
        poll_interval: Seconds between status polls.
        baseline: The cal record from before ``0x28``. When ``None``
            (re-attaching to a run already in progress) it is read now —
            ``0xB9`` is only rewritten at the end, so this is still the
            pre-adjust record.
        start_grace: Seconds after ``t0`` during which "not adjusting"
            is not yet conclusive (set right after sending ``0x28``).
        settle: Continuous idle needed before "no new record" is
            believed.
        poll_timeout: Per-request timeout.
        clock: Monotonic clock (injectable for tests).
        t0: Clock value the deadline counts from (default: now).
        started_at: Wall-clock start for the result (default: now).

    Returns:
        :class:`AdjustmentResult` with the fresh cal record.

    Raises:
        SartoriusAdjustmentAbortedError: Ended without a new record.
        SartoriusAdjustmentNotStartedError: Never observed adjusting.
        SartoriusAdjustmentTimeoutError: Still adjusting (or unreachable)
            at the deadline. Nothing is sent to stop it.
        SartoriusProtocolUnsupportedError: Session is not on xBPI.
    """
    _require_xbpi(session, "wait_for_adjustment")
    start = clock() if t0 is None else t0
    deadline = start + timeout
    if baseline is None:
        baseline = await _read_record(session, poll_timeout)
    watch = _Watch(
        baseline=baseline,
        start=start,
        started_at=started_at or datetime.now(UTC),
        settle=settle,
        start_grace=start_grace,
    )
    last_exc: Exception | None = None

    while True:
        watch.polls += 1
        try:
            status = await _read_status(session, poll_timeout)
            record = (
                await _read_record(session, poll_timeout)
                if watch.wants_record(status, clock())
                else None
            )
        except _TRANSIENT_POLL_ERRORS as exc:
            last_exc = exc
            watch.on_failure()
            _logger.debug(
                "adjust.poll_failed",
                extra={"error": type(exc).__name__, "transient_errors": watch.transient_errors},
            )
            # Partial or late bytes must not leak into the next exchange.
            await _shielded(session.transport.drain_input)
        else:
            try:
                result = watch.on_poll(status, record, clock())
            except SartoriusAdjustmentError as exc:
                _logger.info("adjust.ended_without_record", extra=dict(exc.context.extra))
                raise
            if result is not None:
                _logger.info(
                    "adjust.completed",
                    extra={"duration_s": round(result.duration_s, 3), "polls": result.polls},
                )
                return result

        now = clock()
        if now >= deadline:
            ctx = watch.context(now)
            _logger.info("adjust.timeout", extra=dict(ctx.extra))
            raise SartoriusAdjustmentTimeoutError(
                f"internal adjustment still running after {timeout:g} s "
                "(not aborted; call abort_adjustment() to stop it)",
                context=ctx,
            ) from last_exc
        await anyio.sleep(max(0.0, min(poll_interval, deadline - now)))


async def internal_adjust_and_wait(
    session: Session,
    request: InternalAdjustRequest,
    *,
    confirm: bool,
    timeout: float = DEFAULT_ADJUST_TIMEOUT_S,
    poll_interval: float = DEFAULT_ADJUST_POLL_INTERVAL_S,
    settle: float = SETTLE_S,
    clock: Callable[[], float] = time.monotonic,
) -> AdjustmentResult:
    """Start an internal adjustment and wait for it to finish.

    The confirmation gate runs before any I/O. Then two read-only
    pre-flight reads: refuse with
    :class:`SartoriusAdjustmentInProgressError` if an adjustment (e.g. a
    balance-initiated isoCAL) is already running, and capture the
    baseline cal record. Then ``0x28`` and :func:`wait_for_adjustment`.
    The deadline counts from the moment ``0x28`` is sent.
    """
    _require_xbpi(session, "internal_adjust")
    if not confirm:
        # Same gate Session.execute applies, but before the pre-flight reads.
        raise SartoriusConfirmationRequiredError(
            f"internal_adjust: {INTERNAL_ADJUST.safety.name} commands require confirm=True",
            context=ErrorContext(
                command_name="internal_adjust",
                protocol=session.active_protocol.value,
                extra={"safety": INTERNAL_ADJUST.safety.name},
            ),
        )
    pre = await _read_status(session, _POLL_TIMEOUT_S)
    if pre.adjusting:
        raise SartoriusAdjustmentInProgressError(
            "an internal adjustment is already running; use wait_for_adjustment() to wait for it",
            context=ErrorContext(
                command_name="internal_adjust",
                extra={"raw_state": pre.raw_state, "raw_status": pre.raw_status},
            ),
        )
    baseline = await _read_record(session, _POLL_TIMEOUT_S)
    started_at = datetime.now(UTC)
    t0 = clock()
    await _shielded(lambda: session.execute(INTERNAL_ADJUST, request, confirm=confirm))
    _logger.info("adjust.started", extra={"cal_type": request.cal_type})
    return await wait_for_adjustment(
        session,
        timeout=timeout,
        poll_interval=poll_interval,
        baseline=baseline,
        start_grace=START_GRACE_S,
        settle=settle,
        clock=clock,
        t0=t0,
        started_at=started_at,
    )


async def abort_adjustment(session: Session) -> bool:
    """Send ``0x29``. ``True`` if a running adjustment was aborted.

    ``False`` when nothing was running (the balance answers err ``0x06``),
    so a Cancel button racing the natural end of the run needs no error
    handling. The balance takes ~2.5 s to unload its internal weight after
    the abort. A concurrent :func:`wait_for_adjustment` then raises
    :class:`SartoriusAdjustmentAbortedError`.
    """
    _require_xbpi(session, "abort_adjustment")
    try:
        await _shielded(lambda: session.execute(ABORT_ADJUSTMENT, AbortAdjustmentRequest()))
    except SartoriusOperationNotApplicableError:
        return False
    _logger.info("adjust.abort_sent")
    return True
