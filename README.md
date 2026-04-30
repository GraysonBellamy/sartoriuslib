# sartoriuslib

Async-first Python driver for [Sartorius](https://www.sartorius.com/) lab
balances over RS-232 / USB. Speaks both wire protocols the hardware exposes —
**xBPI** (binary, length-prefixed, checksum-protected, SBN-addressed) and
**SBI** (ASCII command/response and autoprint) — behind a single semantic
`Balance` API that decodes to the same typed `Reading` either way.

Built as a sibling to
[`alicatlib`](https://github.com/GraysonBellamy/alicatlib): the same async
core, sync facade, multi-device manager, fake transport, acquisition helpers,
and pluggable sinks.

> **Status: alpha.** Architecture is frozen and the public API is stable.
> Both protocol clients, the `Balance` facade, `SartoriusManager`, the
> recorder, first-party sink classes, the sync facade, and the `sarto-*`
> CLIs ship in the base install. Parquet and Postgres sinks lazy-load their
> optional backends. Hardware-coverage breadth and documentation polish are
> the active work; see the [CHANGELOG](CHANGELOG.md).

## Highlights

- **One protocol-neutral API.** `Balance.poll()`, `tare()`, `zero()`,
  `identify()`, `status()`, parameter R/W — the same calls work over xBPI
  and SBI, decoding to identical [`Reading`](src/sartoriuslib/devices/models.py)
  / [`BalanceStatus`](src/sartoriuslib/devices/models.py) /
  [`DeviceInfo`](src/sartoriuslib/devices/models.py) models.
- **Auto-detect.** `open_device(..., protocol=ProtocolKind.AUTO)` does
  passive autoprint sniff → xBPI probe → SBI probe and reports clearly when
  nothing answers.
- **Typed end to end.** `Unit.G`, `FilterMode.STABLE`,
  `Capability.HIRES_WEIGHT`, frozen-dataclass responses, `py.typed`,
  `mypy --strict` clean.
- **Typed errors.** `SartoriusError` root with structured `ErrorContext`;
  every xBPI `0x01` error subtype maps to a distinct exception.
- **Safety gates.** Persistent and destructive operations require
  `confirm=True`. Family/capability mismatches are soft by default
  (warn + attempt); opt in to `strict=True` for pre-I/O refusal.
- **Multi-device.** [`SartoriusManager`](src/sartoriuslib/manager.py) runs
  many balances concurrently — same-port requests serialize, different
  ports run in parallel.
- **Acquisition built in.** [`record(...)`](src/sartoriuslib/streaming/recorder.py)
  drives one or many devices on an absolute-target cadence into pluggable
  sinks: `InMemorySink`, `CsvSink`, `JsonlSink`, `SqliteSink` in core, plus
  `ParquetSink` and `PostgresSink` behind extras.
- **Swappable transports.** `SerialTransport` for hardware,
  `FakeTransport` for tests, fixture-backed transports for regression
  goldens.
- **Sync or async.** Async core on `anyio`; complete sync facade at
  `sartoriuslib.sync` via a blocking portal — every async method has a
  sync parity.
- **CLI tooling.** `sarto-read`, `sarto-discover`, `sarto-capture`,
  `sarto-raw`, `sarto-decode`, `sarto-configure`, and the `sarto-diag`
  reverse-engineering namespace.
- **Lean core.** `pip install sartoriuslib` pulls in `anyio` and
  `anyserial` — nothing else.

## Install

```bash
pip install sartoriuslib

# optional sinks
pip install 'sartoriuslib[parquet]'   # ParquetSink (pyarrow)
pip install 'sartoriuslib[postgres]'  # PostgresSink (asyncpg)
```

Requires **Python 3.13+**. Linux, macOS, BSD, and Windows are supported via
[`anyserial`](https://pypi.org/project/anyserial/). On Linux, the user
running `sarto-*` needs read/write access to the serial device — usually by
joining the `dialout` group.

## Quickstart (async)

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

Sartorius balances ship from the factory speaking **SBI**. Pass
`protocol=ProtocolKind.SBI` (or `ProtocolKind.AUTO`) on first contact;
xBPI is a configuration choice you make on the device. See the
[troubleshooting guide](docs/troubleshooting.md).

## Quickstart (sync)

```python
from sartoriuslib.sync import Sartorius

with Sartorius.open("/dev/ttyUSB0") as bal:
    print(bal.poll())
    bal.tare()
```

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

The recorder runs on an absolute target cadence (drift-free), batches
samples per tick, and reports send/receive timing on every `Sample`. See
[`examples/`](examples/) for a runnable script that streams an Alicat MFC
and a Sartorius balance into one shared SQLite database concurrently.

## Command-line tools

```bash
sarto-discover /dev/ttyUSB0                       # probe + identify
sarto-read /dev/ttyUSB0 --protocol auto           # one decoded poll
sarto-capture /dev/ttyUSB0 --rate 10 --duration 60 --out run.csv
sarto-decode --xbpi 02 02 48 ...                  # offline frame decode
sarto-raw /dev/ttyUSB0 --xbpi 0x02 --confirm      # raw escape hatch
sarto-configure switch-protocol /dev/ttyUSB0 --confirm
sarto-diag snapshot /dev/ttyUSB0 --out diag.json  # reverse-engineering aids
```

All CLIs accept `--fixture FILE` to drive a scripted `FakeTransport`, so
end-to-end tests and demos work without hardware.

## Documentation

Full docs live at <https://GraysonBellamy.github.io/sartoriuslib/>. Useful
entry points:

- [Async quickstart](docs/quickstart-async.md) /
  [Sync quickstart](docs/quickstart-sync.md)
- [Balances and capabilities](docs/devices.md)
- [Commands and safety tiers](docs/commands.md) / [Safety](docs/safety.md)
- [Streaming and acquisition](docs/streaming.md) /
  [Logging](docs/logging.md)
- [Wire protocol reference](docs/protocol.md)
- [Testing](docs/testing.md) — `FakeTransport`, fixtures, hardware tiers
- [Architecture (design doc)](docs/design.md)

## Development

[`uv`](https://docs.astral.sh/uv/) for env and lock management,
`hatchling` + `hatch-vcs` for builds, `ruff` for format and lint, `mypy
--strict` and `pyright` for types, AnyIO's pytest plugin for the test
suite (parametrised across `asyncio`, `asyncio+uvloop`, and `trio`).

```bash
uv sync --all-extras --dev
uv run pre-commit install
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

Hardware tests are gated behind `SARTORIUSLIB_ENABLE_STATEFUL_TESTS=1` and
`SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1` and require a connected balance.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and
[SECURITY.md](SECURITY.md) for the disclosure policy.

## License

MIT. See [LICENSE](LICENSE).
