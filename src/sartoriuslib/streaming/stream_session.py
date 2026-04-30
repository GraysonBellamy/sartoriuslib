"""Per-balance streaming session used by :meth:`Balance.stream`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic_ns
from typing import TYPE_CHECKING, Literal, Self

import anyio

from sartoriuslib.devices.capability import SafetyTier
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusAutoprintActiveError,
    SartoriusConfirmationRequiredError,
    SartoriusParseError,
    SartoriusProtocolUnsupportedError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.streaming.sample import Sample

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance
    from sartoriuslib.devices.models import Reading

__all__ = ["StreamMode", "StreamingSession"]


type StreamMode = Literal["poll", "autoprint"]


class StreamingSession:
    """Async context manager + iterator for one balance.

    ``mode="poll"`` performs request/response polling at an absolute cadence.
    ``mode="autoprint"`` consumes already-enabled SBI autoprint lines and
    fails on entry if no line is available within ``timeout``. The
    ``temporary_autoprint=True`` path is reserved for a future persistent
    SBI parameter-write flow and currently raises :class:`NotImplementedError`.
    """

    def __init__(
        self,
        balance: Balance,
        *,
        rate_hz: float | None = None,
        mode: StreamMode = "poll",
        temporary_autoprint: bool = False,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> None:
        if mode not in ("poll", "autoprint"):
            raise ValueError(f"unknown stream mode {mode!r}")
        if mode == "poll" and (rate_hz is None or rate_hz <= 0):
            raise ValueError("poll stream requires rate_hz > 0")
        self._balance = balance
        self._rate_hz = rate_hz
        self._mode: StreamMode = mode
        self._temporary_autoprint = temporary_autoprint
        self._confirm = confirm
        self._timeout = timeout
        self._entered = False
        self._tick = 0
        self._start: float = 0.0
        self._pending: Sample | None = None

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("StreamingSession is already active")
        self._entered = True
        self._start = anyio.current_time()
        if self._mode == "autoprint":
            if self._balance.session.active_protocol is not ProtocolKind.SBI:
                raise SartoriusProtocolUnsupportedError(
                    "autoprint stream requires an SBI session",
                    context=ErrorContext(
                        protocol=self._balance.session.active_protocol.value,
                    ),
                )
            if self._temporary_autoprint:
                if not self._confirm:
                    raise SartoriusConfirmationRequiredError(
                        "temporary autoprint mutates persistent output settings; pass confirm=True",
                        context=ErrorContext(
                            protocol="sbi",
                            extra={"safety": SafetyTier.PERSISTENT.name},
                        ),
                    )
                raise NotImplementedError(
                    "temporary SBI autoprint configuration needs a verified "
                    "SBI parameter-write command; use mode='autoprint' with "
                    "autoprint already enabled",
                )
            self._pending = await self._read_autoprint_sample()
        elif self._balance.session.sbi_autoprint_active:
            raise SartoriusAutoprintActiveError(
                "SBI autoprint is active; stream(mode='poll') would leave the "
                "continuous output buffered. Use stream(mode='autoprint') to "
                "consume already-enabled autoprint.",
                context=ErrorContext(
                    protocol="sbi",
                    extra={"autoprint_active": True, "mode": self._mode},
                ),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        del exc_type, exc, tb
        self._entered = False
        self._pending = None

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Sample:
        if not self._entered:
            raise RuntimeError("StreamingSession must be entered before iteration")
        if self._pending is not None:
            sample = self._pending
            self._pending = None
            return sample
        if self._mode == "autoprint":
            return await self._read_autoprint_sample()
        return await self._poll_sample()

    async def _poll_sample(self) -> Sample:
        assert self._rate_hz is not None  # noqa: S101
        period = 1.0 / self._rate_hz
        target = self._start + self._tick * period
        if anyio.current_time() < target:
            await anyio.sleep_until(target)
        self._tick += 1
        requested_at = datetime.now(UTC)
        start_ns = monotonic_ns()
        reading = await self._balance.poll()
        received_at = datetime.now(UTC)
        end_ns = monotonic_ns()
        return _sample_from_reading(
            self._device_name(),
            reading,
            requested_at,
            received_at,
            start_ns,
            end_ns,
            mode="poll",
        )

    async def _read_autoprint_sample(self) -> Sample:
        requested_at = datetime.now(UTC)
        start_ns = monotonic_ns()
        deadline = None if self._timeout is None else anyio.current_time() + self._timeout
        while True:
            timeout = self._timeout
            if deadline is not None:
                timeout = max(0.001, deadline - anyio.current_time())
            try:
                reading = await self._balance.session.read_sbi_autoprint_reading(
                    timeout=timeout,
                )
            except SartoriusParseError:
                if deadline is not None and anyio.current_time() >= deadline:
                    raise
                continue
            received_at = datetime.now(UTC)
            end_ns = monotonic_ns()
            return _sample_from_reading(
                self._device_name(),
                reading,
                requested_at,
                received_at,
                start_ns,
                end_ns,
                mode="autoprint",
            )

    def _device_name(self) -> str:
        info = self._balance.info
        return info.model if info is not None else "balance"


def _sample_from_reading(
    device: str,
    reading: Reading,
    requested_at: datetime,
    received_at: datetime,
    start_ns: int,
    end_ns: int,
    *,
    mode: str,
) -> Sample:
    elapsed = (received_at - requested_at).total_seconds()
    midpoint_at = requested_at + timedelta(seconds=elapsed / 2.0)
    return Sample(
        device=device,
        reading=reading,
        requested_at=requested_at,
        received_at=received_at,
        midpoint_at=midpoint_at,
        monotonic_ns=(start_ns + end_ns) // 2,
        elapsed_s=elapsed,
        protocol=reading.protocol,
        metadata={"mode": mode},
    )
