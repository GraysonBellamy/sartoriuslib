---
description: API reference for sartoriuslib, auto-generated from source docstrings via mkdocstrings-python.
---

# API reference

Auto-generated from source docstrings via
[mkdocstrings-python](https://mkdocstrings.github.io/python/). Every
public name on the guide pages ([Balances](../devices.md),
[Commands](../commands.md), [Streaming](../streaming.md), …) links
back to the relevant reference section here.

## Top-level

- [`sartoriuslib`](sartoriuslib.md) — top-level re-exports
  (`open_device`, `SartoriusManager`, `record`, errors, registries,
  `ProtocolKind`, `BalanceFamily`, `Capability`, …).

## Subpackages

- [`sartoriuslib.transport`](transport.md) — `Transport` Protocol,
  `SerialTransport`, `FakeTransport`, `SerialSettings`.
- [`sartoriuslib.protocol`](protocol.md) — `ProtocolKind`,
  `ProtocolClient`, `detect_protocol`, xBPI and SBI clients / parsers /
  framing / tables.
- [`sartoriuslib.commands`](commands.md) — `Command[Req, Resp]`,
  `XbpiVariant`, `SbiVariant`, the per-category command catalogue.
- [`sartoriuslib.devices`](devices.md) — `Balance`, `Session`, models
  (`Reading`, `BalanceStatus`, `DeviceInfo`, …), `BalanceFamily`,
  `Capability`, `SafetyTier`, `open_device`, discovery helpers.
- [`sartoriuslib.manager`](manager.md) — `SartoriusManager`,
  `BalanceManager`, `DeviceResult`, `ErrorPolicy`.
- [`sartoriuslib.streaming`](streaming.md) — `Sample`,
  `StreamingSession`, `StreamMode`, `record()`, `OverflowPolicy`,
  `AcquisitionSummary`, `PollSource`.
- [`sartoriuslib.sinks`](sinks.md) — `SampleSink` Protocol, `pipe()`,
  first-party sinks (InMemory / CSV / JSONL / SQLite / Parquet /
  Postgres).
- [`sartoriuslib.sync`](sync.md) — sync facade over the async core.
- [`sartoriuslib.registry`](registry.md) — `Unit`, `Sign`, parameter
  table, mode enums (`FilterMode`, `AutoZeroMode`, `IsoCalMode`, …).
- [`sartoriuslib.testing`](testing.md) — `FakeTransport`,
  `canned_frames`, fixture parsers, script builders.
- [`sartoriuslib.errors`](errors.md) — typed exception hierarchy and
  `ErrorContext`.
- [`sartoriuslib.firmware`](firmware.md) — `FirmwareVersion`.
- [`sartoriuslib.config`](config.md) — `SartoriusConfig`,
  `config_from_env`.
- [`sartoriuslib.maintenance`](maintenance.md) — port-level
  `switch_protocol`, `set_baud_rate`, `write_sbn_address` helpers.
