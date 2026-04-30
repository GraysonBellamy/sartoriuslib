# Sync quickstart

The async core is canonical (see [Async quickstart](quickstart-async.md)).
The sync facade — [`sartoriuslib.sync`](../src/sartoriuslib/sync/__init__.py) —
wraps it through a per-context `BlockingPortal` for scripts, notebooks,
and REPL sessions. Every async method has a sync parity. See
[Design](design.md) §9.

## Single device

```python
from sartoriuslib.sync import Sartorius

with Sartorius.open("/dev/ttyUSB0") as bal:
    reading = bal.poll()
    print(reading.value, reading.unit)
    bal.tare()
```

[`Sartorius.open`](../src/sartoriuslib/sync/balance.py) is a context
manager that returns a [`SyncBalance`](../src/sartoriuslib/sync/balance.py)
wrapping the async [`Balance`](../src/sartoriuslib/devices/balance.py).
Same parameters as [`open_device`](../src/sartoriuslib/devices/factory.py) —
`port`, `protocol`, `serial_settings`, `timeout`, `src_sbn`, `dst_sbn`,
`strict`, `identify` — plus an optional `portal=` for sharing event loops.

## Multi-device acquisition

```python
from sartoriuslib.sync import (
    SyncSartoriusManager,
    SyncCsvSink,
    pipe,
    record,
)

with SyncSartoriusManager() as mgr:
    mgr.add("bal1", "/dev/ttyUSB0")
    mgr.add("bal2", "/dev/ttyUSB1")
    with (
        record(mgr, rate_hz=10, duration=60) as stream,
        SyncCsvSink("run.csv") as sink,
    ):
        summary = pipe(stream, sink)
    print(summary.samples_emitted, "samples written")
```

[`SyncSartoriusManager`](../src/sartoriuslib/sync/manager.py) is a plain
context manager that owns the shared portal and the wrapped async
[`SartoriusManager`](../src/sartoriuslib/manager.py). Port
canonicalisation and ref-counted port sharing are the manager's job, not
the caller's.

[`record()`](../src/sartoriuslib/sync/recording.py) and
[`pipe()`](../src/sartoriuslib/sync/recording.py) mirror their async
counterparts; the yielded `stream` is a blocking iterator of
`Mapping[device_name, Sample]` batches. Drift-free absolute-target
scheduling works the same way as the async recorder — see
[Logging and acquisition](logging.md).

## Streaming

```python
with Sartorius.open("/dev/ttyUSB0") as bal:
    with bal.stream(rate_hz=10) as stream:
        for reading in stream:
            print(reading.value, reading.unit)
```

`SyncBalance.stream(...)` returns a sync streaming session — both a sync
context manager and a sync iterator. Same semantics as the async variant
(absolute-cadence poll on either protocol; consume-only autoprint mode
on SBI when `mode="autoprint"` is set). See [Streaming](streaming.md)
for the three SBI modes.

## Discovery

```python
from sartoriuslib.sync import SyncPortal, run_sync
from sartoriuslib import discover_port

with SyncPortal() as portal:
    result = portal.call(discover_port, "/dev/ttyUSB0")
    if result.protocol is not None:
        print(result.protocol, result.model)
```

[`discover_port`](../src/sartoriuslib/devices/discovery.py) probes a
serial port for an answering balance and returns a
[`DiscoveryResult`](../src/sartoriuslib/devices/models.py) regardless of
outcome — the `protocol` and `model` fields are populated only when a
device responded. The sync facade exposes discovery through a portal
rather than a dedicated wrapper because port scanning is rarely a tight
loop. See [Troubleshooting](troubleshooting.md).

## Using a shared portal

The throwaway-portal default is right for one-off scripts. For code
that holds both a manager and standalone sinks, share a portal so they
run on the same event loop:

```python
from sartoriuslib.sync import (
    SyncSartoriusManager,
    SyncPortal,
    SyncSqliteSink,
    pipe,
    record,
)

with SyncPortal() as portal:
    with SyncSartoriusManager(portal=portal) as mgr:
        mgr.add("bal1", "/dev/ttyUSB0")
        with (
            record(mgr, rate_hz=10, duration=60, portal=portal) as stream,
            SyncSqliteSink("run.db", portal=portal) as sink,
        ):
            pipe(stream, sink, portal=portal)
```

Mixing portals works but costs an extra event-loop hop per method
call. Share when performance matters; don't bother for one-off runs.

## See also

- [Installation](installation.md) — core install and extras.
- [Async quickstart](quickstart-async.md) — the canonical surface.
- [Balances](devices.md) — `Balance`, families, capability flags.
- [Logging and acquisition](logging.md) — recorder, sinks, `pipe()`.
- [Safety](safety.md) — destructive commands and `confirm=True`.
