"""Shared :class:`Sample` builders for sink unit tests.

The Parquet and Postgres sink test modules need to synthesise the
same shape of :class:`~sartoriuslib.streaming.sample.Sample`, so the
factory lives here rather than being copy-pasted twice. The existing
``test_sinks.py`` keeps its private factory untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic_ns

from sartoriuslib import ProtocolKind, Reading, SartoriusTimeoutError, Sign, Unit
from sartoriuslib.streaming.sample import Sample


def make_reading(
    *,
    value: float | None = 1.25,
    overload: bool = False,
    underload: bool = False,
    received_at: datetime | None = None,
) -> Reading:
    """Build one :class:`Reading` with deterministic defaults."""
    when = received_at if received_at is not None else datetime.now(UTC)
    return Reading(
        value=value,
        unit=Unit.G,
        sign=Sign.POSITIVE,
        stable=True,
        overload=overload,
        underload=underload,
        decimals=3,
        sequence=None,
        status_flags={"stable": True},
        protocol=ProtocolKind.XBPI,
        received_at=when,
        monotonic_ns=monotonic_ns(),
        raw=b"\xab\xcd",
    )


def make_sample(
    device: str = "b1",
    *,
    value: float | None = 1.25,
    error: bool = False,
    at: datetime | None = None,
) -> Sample:
    """Build one :class:`Sample` with deterministic timing."""
    when = at if at is not None else datetime.now(UTC)
    if error:
        return Sample(
            device=device,
            reading=None,
            requested_at=when,
            received_at=when,
            midpoint_at=when,
            monotonic_ns=0,
            latency_s=0.001,
            protocol=ProtocolKind.XBPI,
            error=SartoriusTimeoutError("scripted failure"),
        )
    return Sample(
        device=device,
        reading=make_reading(value=value, received_at=when),
        requested_at=when,
        received_at=when + timedelta(milliseconds=5),
        midpoint_at=when + timedelta(milliseconds=2),
        monotonic_ns=0,
        latency_s=0.005,
        protocol=ProtocolKind.XBPI,
    )
