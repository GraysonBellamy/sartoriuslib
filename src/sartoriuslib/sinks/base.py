"""Sink Protocol, sample → row helper, and the ``pipe()`` driver.

A :class:`SampleSink` is the minimal shape the recorder's downstream
consumer needs: :meth:`open`, :meth:`write_many`, :meth:`close`, and
the matching async context-manager methods. The in-tree sinks
(:class:`~sartoriuslib.sinks.memory.InMemorySink`,
:class:`~sartoriuslib.sinks.csv.CsvSink`,
:class:`~sartoriuslib.sinks.jsonl.JsonlSink`,
:class:`~sartoriuslib.sinks.sqlite.SqliteSink`,
:class:`~sartoriuslib.sinks.parquet.ParquetSink`, and
:class:`~sartoriuslib.sinks.postgres.PostgresSink`) all satisfy this
Protocol; third-party sinks can slot in without touching library code.

:func:`pipe` is the v1 acquisition glue. It reads per-tick batches out
of the recorder's receive stream, buffers them up to ``batch_size``
(or ``flush_interval`` seconds, whichever comes first), and calls
``sink.write_many`` to flush. On stream exhaustion it drains any
remaining buffer and returns an :class:`AcquisitionSummary` with
``samples_emitted`` reflecting the count actually handed to the sink.

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import anyio

from sartoriuslib._logging import get_logger
from sartoriuslib.streaming.recorder import AcquisitionSummary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from types import TracebackType
    from typing import Self

    from sartoriuslib.streaming.sample import Sample

__all__ = [
    "SampleSink",
    "pipe",
    "sample_to_row",
]


_logger = get_logger("sinks")


class SampleSink(Protocol):
    """Minimal shape of an acquisition sink.

    Sinks own their storage handle lifecycle. Concrete implementations
    typically follow this call sequence:

    1. ``await sink.open()`` — allocate file descriptors, DB connections,
       etc. Safe to call again on an already-open sink.
    2. ``await sink.write_many(samples)`` — one or more times. ``samples``
       is a :class:`~collections.abc.Sequence` so the sink knows the full
       batch up front (useful for CSV column inference, SQLite batched
       inserts).
    3. ``await sink.close()`` — flush and release the handle. Idempotent.

    The async context-manager methods provide an ``async with sink:``
    shape for the common case of "open → write → close" in one block.
    """

    async def open(self) -> None:
        """Allocate the sink's backing resource (file handle, DB conn, …)."""
        ...

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` to the sink."""
        ...

    async def close(self) -> None:
        """Flush and release the backing resource. Idempotent."""
        ...

    async def __aenter__(self) -> Self:
        """Open the sink and return ``self`` for chaining."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on exit."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def sample_to_row(sample: Sample) -> dict[str, float | int | str | bool | None]:
    """Flatten a :class:`Sample` into a single row dict for tabular sinks.

    Schema layout (stable across samples; design §10):

    - ``device`` — manager-assigned name.
    - ``requested_at`` / ``received_at`` / ``midpoint_at`` — ISO 8601.
    - ``elapsed_s`` — poll round-trip time, seconds.
    - *reading fields* — from :meth:`Reading.as_dict`: ``value``,
      ``unit``, ``sign``, ``stable``, ``overload``, ``underload``,
      ``decimals``, ``sequence``, ``protocol``, ``raw``. On error
      samples (``reading is None``) these all appear as ``None``.
    - ``error_type`` — fully qualified exception class on a failed
      sample, otherwise ``None``.
    - ``error_message`` — ``str(error)`` on a failed sample, otherwise
      ``None``.

    ``Reading.protocol`` is the authoritative protocol column on
    success rows; on error rows the row's ``protocol`` column falls
    back to :attr:`Sample.protocol` (populated by the manager from
    the session's active protocol) so sinks never see a missing
    column.
    """
    row: dict[str, float | int | str | bool | None] = {
        "device": sample.device,
        "requested_at": sample.requested_at.isoformat(),
        "received_at": sample.received_at.isoformat(),
        "midpoint_at": sample.midpoint_at.isoformat(),
        "elapsed_s": sample.elapsed_s,
    }
    reading = sample.reading
    if reading is not None:
        row.update(reading.as_dict())
    else:
        # Keep the schema stable on error rows so the first batch
        # of mixed results doesn't accidentally lock a narrower
        # schema when the first sample is a failure.
        row.update(
            {
                "value": None,
                "unit": None,
                "sign": None,
                "stable": None,
                "overload": None,
                "underload": None,
                "decimals": None,
                "sequence": None,
                "protocol": sample.protocol.value if sample.protocol is not None else None,
                "raw": None,
            }
        )
    err = sample.error
    if err is not None:
        cls = type(err)
        row["error_type"] = f"{cls.__module__}.{cls.__qualname__}"
        row["error_message"] = str(err)
    else:
        row["error_type"] = None
        row["error_message"] = None
    return row


# ---------------------------------------------------------------------------
# pipe() driver
# ---------------------------------------------------------------------------


async def pipe(
    stream: AsyncIterator[Mapping[str, Sample]],
    sink: SampleSink,
    *,
    batch_size: int = 64,
    flush_interval: float = 1.0,
) -> AcquisitionSummary:
    r"""Drain ``stream`` into ``sink`` with buffered flushes.

    Reads per-tick batches from the recorder and accumulates the
    individual :class:`Sample`\ s into a list. A flush happens when
    either threshold is first crossed:

    - the buffer reaches ``batch_size`` samples, or
    - ``flush_interval`` seconds have elapsed since the last flush.

    On stream exhaustion any leftover buffer is flushed before the
    summary is returned.

    Args:
        stream: The async iterator yielded by
            :func:`~sartoriuslib.streaming.record`.
        sink: Any :class:`SampleSink`. Must already be open.
        batch_size: Buffer threshold in samples (not batches).
        flush_interval: Seconds between flushes (wall-clock).

    Returns:
        An :class:`AcquisitionSummary` with ``samples_emitted`` set to
        the count actually handed to the sink.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size!r}")
    if flush_interval <= 0:
        raise ValueError(f"flush_interval must be > 0, got {flush_interval!r}")

    started_at = datetime.now(UTC)
    emitted = 0
    buffer: list[Sample] = []
    last_flush = anyio.current_time()

    async def _flush() -> None:
        nonlocal emitted
        if not buffer:
            return
        await sink.write_many(buffer)
        emitted += len(buffer)
        buffer.clear()

    async for batch in stream:
        buffer.extend(batch.values())
        now = anyio.current_time()
        if len(buffer) >= batch_size or (now - last_flush) >= flush_interval:
            await _flush()
            last_flush = now

    await _flush()
    finished_at = datetime.now(UTC)
    _logger.info(
        "sinks.pipe_done",
        extra={
            "sink": type(sink).__name__,
            "samples_emitted": emitted,
            "duration_s": (finished_at - started_at).total_seconds(),
        },
    )
    return AcquisitionSummary(
        started_at=started_at,
        finished_at=finished_at,
        samples_emitted=emitted,
        samples_late=0,
        max_drift_ms=0.0,
    )
