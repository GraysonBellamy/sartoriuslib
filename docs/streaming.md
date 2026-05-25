---
description: Stream readings from Sartorius balances — cadenced request/response polling on xBPI and SBI plus device-driven SBI autoprint modes, producing a uniform Reading shape.
---

# Streaming

Sartorius balances support two acquisition shapes:

- **Request/response polling** — works on either xBPI or SBI. The session
  emits one read frame per tick at an absolute cadence.
- **Device-driven autoprint** — SBI-only. The balance owns the wire and
  emits weight lines continuously when parameter `p36` is set to
  `auto_wo` / `auto_w`.

Both paths produce the same [`Reading`](data-frames.md) shape. The differences
live entirely in `Balance.stream(...)`'s `mode=` argument. See [Design](design.md) §10.

## Cadenced poll (default)

```python
async with await open_device("/dev/ttyUSB0") as bal:
    async with bal.stream(rate_hz=10) as stream:
        async for reading in stream:
            print(reading.value, reading.unit)
```

[`Balance.stream(rate_hz=...)`](../src/sartoriuslib/devices/balance.py)
returns a [`StreamingSession`](../src/sartoriuslib/streaming/stream_session.py)
— an async context manager and async iterator. Ticks fire on absolute
targets (no drift accumulation) and the session never mutates device-side
state. Works on both protocols.

`rate_hz` is required for `mode="poll"`. The session raises
`ValueError` if it's missing or non-positive.

## Three SBI modes

xBPI does not autoprint in the decoded protocol; the xBPI `stream(...)`
convenience always polls. SBI is where stream modes matter, because
autoprint and command/reply share line framing but have incompatible
semantics — once the balance owns the wire, query tokens don't reliably
produce distinguishable replies.

`Balance.stream(...)` exposes three explicit modes so the caller's intent
is always visible:

```python
# 1. Poll — request/response cadence, no device-side mutation. Default.
async with bal.stream(mode="poll", rate_hz=10) as stream: ...

# 2. Consume already-enabled autoprint — no device-side mutation.
#    Fails on entry if autoprint is not already active.
async with bal.stream(mode="autoprint") as stream: ...

# 3. Configure autoprint for the lifetime of the stream, restore on exit.
#    Mutates parameter p36 — requires confirm=True.
async with bal.stream(
    mode="autoprint",
    temporary_autoprint=True,
    confirm=True,
) as stream: ...
```

!!! warning "Mode 3 is not implemented yet"
    `mode="autoprint", temporary_autoprint=True` raises
    `NotImplementedError` in the current release. The temporary-autoprint
    path needs a verified SBI parameter-write command. Until that lands,
    callers who want autoprint must enable `p36` from the front panel
    (or via `sarto-configure`) and use `mode="autoprint"` against the
    already-enabled stream.

### `mode="poll"` while SBI autoprint is active

If the SBI session detects autoprint already running and the caller asks
for `mode="poll"`, the stream entry raises `SartoriusAutoprintActiveError`
with guidance to use `mode="autoprint"` instead. The library refuses to
buffer continuous output behind a cadenced poll loop because stale
backlog reads silently introduce timing skew.

### Front-panel autoprint toggle mid-session

If the user enables autoprint on the front panel during an open session,
the next SBI command/reply call that sees unsolicited output marks the
session active, preserves the observed line for later consumption, and
raises `SartoriusAutoprintActiveError`. Conversely, if autoprint is
disabled mid-session, calls to `Balance.refresh_sbi_autoprint_state()`
let the session re-discover the state quietly.

## Multi-device acquisition with `record(...)`

For multiple balances or when you need a sample-stream with explicit
timing metadata, use the recorder:

```python
import anyio
from sartoriuslib import SartoriusManager
from sartoriuslib.streaming import record
from sartoriuslib.sinks import CsvSink, pipe

async def main() -> None:
    async with SartoriusManager() as mgr:
        await mgr.add("bal1", "/dev/ttyUSB0")
        await mgr.add("bal2", "/dev/ttyUSB1")
        async with (
            record(mgr, rate_hz=5, duration=30) as stream,
            CsvSink("run.csv") as sink,
        ):
            summary = await pipe(stream, sink)
        print(f"{summary.samples_emitted} samples, {summary.samples_late} late")

anyio.run(main)
```

[`record(...)`](../src/sartoriuslib/streaming/recorder.py) yields
`Mapping[device_name, Sample]` per tick. Each [`Sample`](api/streaming.md)
carries the wrapped `Reading`, requested timestamp, received timestamp,
elapsed seconds, protocol, and any per-device error caught during the
tick. The recorder logs its producer `AcquisitionSummary` on context exit;
`pipe(...)` returns an `AcquisitionSummary` for the samples it wrote.

See [Logging and acquisition](logging.md) for the full sink surface and
[Design](design.md) §10 for scheduling and overflow policy.

## Error handling inside a stream

The async iterator surfaces transport / protocol errors inline — the
stream stops on the first uncaught exception. For long-running runs
that should tolerate a flaky device, catch `SartoriusError` inside the
loop body. The `record()` recorder takes a different approach: per-tick
errors are recorded on `Sample.error` instead of stopping the stream.

## See also

- [Logging and acquisition](logging.md) — `record()`, sinks, `pipe()`.
- [Commands](commands.md) — what `poll()` actually sends.
- [Readings](data-frames.md) — `Reading` shape.
- [Safety](safety.md) — `confirm=True` and persistent-state mutation rules.
- [Design](design.md) §10 — scheduling, overflow, autoprint state machine.
