# Logging and acquisition

For continuous data capture, `sartoriuslib` provides a recorder that
samples a balance (or a `SartoriusManager` of balances) at a fixed rate,
plus first-party sinks that persist the resulting `Sample` stream. The
two halves connect through `pipe()`. See [Design](design.md) §10.

## Recorder

[`record(...)`](../src/sartoriuslib/streaming/recorder.py) is an async
context manager. It schedules ticks on absolute targets (drift-free) and
yields a `Mapping[device_name, Sample]` per tick.

```python
import anyio
from sartoriuslib import SartoriusManager
from sartoriuslib.streaming import record

async def main() -> None:
    async with SartoriusManager() as mgr:
        await mgr.add("bal1", "/dev/ttyUSB0")
        await mgr.add("bal2", "/dev/ttyUSB1")
        async with record(mgr, rate_hz=10, duration=60) as stream:
            async for batch in stream:
                for name, sample in batch.items():
                    if sample.error is None:
                        print(name, sample.reading.value, sample.reading.unit)

anyio.run(main)
```

`record(...)` accepts:

- A `Balance` for a single device.
- A `SartoriusManager` for multi-device acquisition.
- A custom [`PollSource`](api/streaming.md) — the protocol the recorder
  drives for adapters and tests.

Required: `rate_hz`. Optional: `duration` (seconds; `None` = unbounded),
`overflow_policy`, `tick_jitter_warn_s`. See [Design](design.md) §10 for
the full scheduling model.

## `Sample`

[`Sample`](../src/sartoriuslib/streaming/sample.py) wraps a single
balance's per-tick result with timing metadata.

| Field | Notes |
| --- | --- |
| `device` | Manager-assigned name. |
| `reading` | `Reading \| None` — `None` on tick error. |
| `requested_at` | When the recorder scheduled the tick (wall-clock). |
| `received_at` | When the reply landed. |
| `midpoint_at` | Half-way between the two — the canonical timestamp for plots. |
| `latency_s` | Poll round-trip time. |
| `protocol` | `Reading.protocol` on successful polls; on failed ticks, the protocol from `error.context.protocol` when available. |
| `error` | The exception caught for this tick, else `None`. Per-tick errors are recorded, not raised — so a flaky device doesn't kill a long run. |

## `AcquisitionSummary`

[`AcquisitionSummary`](api/streaming.md) is built and logged by `record()`
when the producer exits, and returned directly by `pipe()`.

| Field | Notes |
| --- | --- |
| `started_at` | Wall-clock at the first scheduled tick. |
| `finished_at` | Wall-clock at producer shutdown. |
| `samples_emitted` | For recorder summaries, per-tick batches pushed onto the receive stream. For `pipe()` summaries, individual samples handed to the sink. |
| `samples_late` | Ticks that missed their target slot or were dropped under the overflow policy. |
| `max_drift_ms` | Largest observed positive drift of an emitted batch, in milliseconds. |
| `target_total_samples` | Scheduled tick count for finite-duration recorder runs, or `None` for open-ended runs and `pipe()` summaries. Under overrun/drop conditions, `samples_emitted + samples_late` is the recorder target-count invariant unless the caller exits early. |

## Sinks

A sink persists `Sample`s. The protocol is small:

```python
class SampleSink(Protocol):
    async def open(self) -> None: ...
    async def write_many(self, samples: Sequence[Sample]) -> None: ...
    async def close(self) -> None: ...
    # plus async context-manager dunder methods
```

First-party sinks all conform:

| Sink | Module | Backing | Needs extra |
| --- | --- | --- | --- |
| `InMemorySink` | [sinks/memory.py](../src/sartoriuslib/sinks/memory.py) | list (test/debug) | — |
| `CsvSink` | [sinks/csv.py](../src/sartoriuslib/sinks/csv.py) | stdlib `csv` | — |
| `JsonlSink` | [sinks/jsonl.py](../src/sartoriuslib/sinks/jsonl.py) | stdlib `json` | — |
| `SqliteSink` | [sinks/sqlite.py](../src/sartoriuslib/sinks/sqlite.py) | stdlib `sqlite3` (WAL mode) | — |
| `ParquetSink` | [sinks/parquet.py](../src/sartoriuslib/sinks/parquet.py) | `pyarrow` | `sartoriuslib[parquet]` |
| `PostgresSink` | [sinks/postgres.py](../src/sartoriuslib/sinks/postgres.py) | `asyncpg` | `sartoriuslib[postgres]` |

Optional-extra sinks lazy-import their backend; importing
`sartoriuslib.sinks.parquet` without `pyarrow` installed raises
`SartoriusSinkDependencyError` with the install hint, not an opaque
`ImportError`.

## `pipe(stream, sink)`

[`pipe(...)`](../src/sartoriuslib/sinks/base.py) is the v1
acquisition glue. It reads per-tick batches from the recorder, buffers
them up to `batch_size` (or `flush_interval` seconds, whichever comes
first), and calls `sink.write_many` to flush. On stream exhaustion it
drains any remaining buffer and returns the `AcquisitionSummary`.

```python
async with (
    record(mgr, rate_hz=10, duration=60) as stream,
    SqliteSink("run.db") as sink,
):
    summary = await pipe(stream, sink, batch_size=64, flush_interval=1.0)
print(f"{summary.samples_emitted} samples, {summary.samples_late} late")
```

## Row schema

`sample_to_row(sample)` is the canonical flatten. Schema is stable across
samples (locked on first batch) so mixed success/error rows don't narrow the
schema. Tabular schema inference supports `float`, `int`, `str`, `bool`, and
`None`; the canonical `Reading` flags still render as `0` / `1` for SQLite
and CSV compatibility.

| Column | Type | Notes |
| --- | --- | --- |
| `device` | str | Manager-assigned name. |
| `requested_at` / `received_at` / `midpoint_at` | str (ISO 8601) | Wall-clock timing per tick. |
| `latency_s` | float | Poll round-trip seconds. |
| `value` | float \| null | `None` on overload/underload sentinel or on error rows. |
| `unit` | str | From the `Unit` enum. |
| `sign` | str | From `Sign`. |
| `stable` / `overload` / `underload` | int (0/1) \| null | Booleans render as integers so SQLite picks INTEGER affinity. |
| `decimals` | int \| null | xBPI byte[5]>>4 or SBI parsed. |
| `sequence` | int \| null | xBPI sequence; null on SBI. |
| `protocol` | str | `xbpi` / `sbi`. |
| `raw` | str (hex) \| null | Original frame bytes as hex. |
| `error_type` | str \| null | Fully-qualified exception class on error rows. |
| `error_message` | str \| null | `str(error)` on error rows. |

The schema is intentionally compatible with [alicatlib](https://github.com/GraysonBellamy/alicatlib)'s
sink schema where the columns make sense across both libraries.

## Backpressure

The recorder has a bounded internal queue. If a slow sink can't keep up,
the recorder drops ticks per the configured `OverflowPolicy`:

- `OverflowPolicy.BLOCK` — await the slow consumer (default). Silent
  drops are surprising in a data-acquisition setting, so the recorder
  blocks the producer rather than discarding samples. Effective sample
  rate drops to the consumer's drain rate; `samples_late` accrues once
  the consumer catches up.
- `OverflowPolicy.DROP_NEWEST` — drop the sample about to be enqueued.
  Counted on `samples_late`.
- `OverflowPolicy.DROP_OLDEST` — evict the oldest queued batch, then
  enqueue. Counted on `samples_late`.

Drops are counted on `AcquisitionSummary.samples_late` and emitted as
structured log events through `_logging.get_logger("streaming")`.

## See also

- [Streaming](streaming.md) — single-balance `Balance.stream(...)`.
- [Readings](data-frames.md) — `Reading` shape inside `Sample`.
- [Sinks API](api/sinks.md) — full reference.
- [Testing](testing.md) — `FakeTransport` for sink unit tests.
- [Design](design.md) §10 — scheduling, overflow, schema rationale.
