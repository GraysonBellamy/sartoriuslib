# sartoriuslib

Python library for [Sartorius](https://www.sartorius.com/) balances over serial.
Supports both wire protocols the hardware speaks: **xBPI** (binary,
length-prefixed, checksum-protected, SBN-addressed) and **SBI** (ASCII
command/response and autoprint).

Designed as a sibling to [`alicatlib`](https://github.com/GraysonBellamy/alicatlib):
the same async-first device facade, sync wrapper, manager, fake transport,
acquisition helpers, sinks, typed models, and explicit safety gates.

> **Status: pre-alpha skeleton.** The public architecture is frozen — see
> [docs/design.md](docs/design.md) — but most modules are unimplemented
> stubs. See §15 of the design doc for the slice order. First α milestone
> lands a usable xBPI driver; β adds the full async/sync acquisition path;
> 1.0 adds SBI parity.

## Highlights (target — not all implemented yet)

- **Protocol-neutral public API.** `Balance.poll()`, `tare()`, `zero()`,
  `identify()` — the same calls work over xBPI and SBI; decoded `Reading`
  objects are identical across protocols.
- **Typed end to end.** `Unit.G`, `FilterMode.STABLE`, `Capability.HIRES_WEIGHT`,
  frozen dataclass responses, `py.typed` shipped, `mypy --strict` passes.
- **Declarative commands.** One `Command` per semantic op with
  `XbpiVariant` / `SbiVariant` — adding a new command is ~50 lines.
- **Typed errors.** `SartoriusError` root with structured `ErrorContext`;
  xBPI subtype `0x01` codes map to distinct exceptions.
- **Safety.** Persistent and dangerous operations require `confirm=True`.
  Family/capability mismatches are soft by default (warn + attempt); opt in
  to `strict=True` for pre-I/O refusal.
- **Runtime capability verification.** Family tables seed priors; the
  device's own responses drive the per-command `Availability` cache.
- **Multi-device.** `SartoriusManager` runs many balances concurrently;
  same-port requests serialize, different ports run in parallel.
- **Acquisition built in.** `record()` drives devices at an
  absolute-cadence; sinks: `InMemorySink`, `CsvSink`, `JsonlSink`,
  `SqliteSink`, plus `ParquetSink` / `PostgresSink` behind extras.
- **Swappable transports.** `SerialTransport` for hardware, `FakeTransport`
  for tests, fixture-backed transports for regression goldens.
- **Sync or async.** Async core built on `anyio`; sync facade at
  `sartoriuslib.sync.Sartorius` wraps it via a blocking portal.
- **Lean core.** `pip install sartoriuslib` pulls in `anyio` and
  `anyserial` — nothing else.

## Install

```bash
pip install sartoriuslib
# optional sinks
pip install 'sartoriuslib[parquet]'
pip install 'sartoriuslib[postgres]'
```

Requires Python 3.13+.

## Quickstart (async)

```python
import anyio
from sartoriuslib import open_device

async def main():
    async with await open_device("/dev/ttyUSB0") as bal:
        reading = await bal.poll()
        print(reading.value, reading.unit)
        await bal.tare()

anyio.run(main)
```

## Quickstart (sync)

```python
from sartoriuslib.sync import Sartorius

with Sartorius.open("/dev/ttyUSB0") as bal:
    print(bal.poll())
    bal.tare()
```

## Development

Uses [`uv`](https://docs.astral.sh/uv/) for env and lock management,
`hatchling` for the build backend, `ruff` for format and lint, and
`mypy --strict` for types.

```bash
uv sync --all-extras --dev
uv run pre-commit install
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow, and
[docs/design.md](docs/design.md) for the architectural design.

## License

MIT. See [LICENSE](LICENSE).
