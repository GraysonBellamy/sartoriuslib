---
description: Async quickstart for sartoriuslib — open a Sartorius balance over serial, poll readings, tare, and stream samples with anyio.
---

# Async quickstart

```python
import anyio
from sartoriuslib import open_device

async def main() -> None:
    async with await open_device("/dev/ttyUSB0") as bal:
        reading = await bal.poll()
        print(reading.value, reading.unit, "stable" if reading.stable else "unstable")

        await bal.tare()

anyio.run(main)
```

`open_device` is a coroutine that returns a [`Balance`](api/devices.md);
the balance itself is the async context manager. Exiting the `async with`
closes the underlying transport. `open_device` defaults to xBPI; pass
`protocol=ProtocolKind.SBI` or `protocol=ProtocolKind.AUTO` to switch.
Sartorius balances ship from the factory in **SBI** — see
[Troubleshooting](troubleshooting.md) for first-contact guidance.

## Streaming weight

```python
import anyio
from sartoriuslib import open_device

async def main() -> None:
    async with await open_device("/dev/ttyUSB0") as bal:
        async with bal.stream(rate_hz=10) as stream:
            async for reading in stream:
                if reading.stable:
                    print(reading.value, reading.unit)
```

`Balance.stream(rate_hz=...)` polls at an absolute cadence on either
protocol. SBI also supports consuming an already-enabled autoprint feed —
see [Streaming](streaming.md) for the three SBI stream modes.

## Multi-device acquisition

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
            record(mgr, rate_hz=10, duration=60) as stream,
            CsvSink("run.csv") as sink,
        ):
            await pipe(stream, sink)

anyio.run(main)
```

See [Logging and acquisition](logging.md) for the sink surface, row
layout, and structured log events, and the [Design doc](design.md)
§10 for scheduling and overflow policy.
