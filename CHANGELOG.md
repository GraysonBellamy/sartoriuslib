# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] - 2026-05-30

### Fixed

- **`discover_temperature_sensors()` no longer crashes on firmwares that
  signal an absent sensor slot with xBPI `0x03` (value out of range)**
  instead of the `7f ff ff ff` sentinel. Observed on a WZ8202 OEM weigh
  cell (sensors at indices 0/1, `0x03` at 2+): the
  `SartoriusValueOutOfRangeError` used to propagate and abort the walk.
  The `0x03` slot is now treated as empty and skipped, with probing
  continuing to `max_index` so a sparse map is not truncated at the
  first gap. `0x10` (`SartoriusIndexOutOfRangeError`) remains the
  authoritative end-of-list stop.

### Changed

- **`OEM_WEIGH_CELL` family capability prior expanded** from
  `XBPI_SUPPORT | SBI_SUPPORT` to the set cross-confirmed on two distinct
  OEM cells — a live WZ8202 and the recorded WZA8202-N — adding
  `HIRES_WEIGHT`, `PARAMETER_TABLE`, `TEMPERATURE_SENSORS`, and
  `BARGRAPH`. `ISOCAL` (p15), `AUTO_OUTPUT` (p36), and `RAW_ADC` (0x75)
  are present on the WZ8202 but absent or unconfirmed on the WZA8202-N,
  so they stay live-probe decisions rather than family priors.
  Non-breaking — capability priors are soft (a mismatch costs one
  warning, never an error).

### Added

- **`build_temperature_script(..., value_out_of_range_at=...)`** in
  `sartoriuslib.testing` — scripts xBPI `0x03` (value out of range)
  replies for the given sensor indices, for exercising sparse
  temperature layouts.

### Documentation

- `docs/protocol.md` §14.2.1 records the WZ8202 as a second OEM weigh-cell
  data point and its divergences from the WZA8202-N (richer parameter
  table incl. p15/p36/p40; temperature sensors at idx 0/1 vs 0/3; `0x75`
  raw-ADC present; `0x28` internal-adjust returns `0x06` not-applicable).

## [0.4.0] - 2026-05-15

### Unified cross-library API (`UNIFIED_API_HANDOFF.md`)

This release aligns sartoriuslib's public surface with the v1 unified
cross-library spec shared with `alicatlib`, `watlowlib`, and `nidaqlib`.
The clean-break rule applies: every renamed symbol is gone — no
deprecation shims, no compatibility aliases.

#### Breaking

- **Removed** `open_balance(...)`. Use `open_device(...)`.
- **Removed** `BalanceManager` alias. Use `SartoriusManager`. (Sync
  facade: `SyncBalanceManager` removed; use `SyncSartoriusManager`.)
- **Discovery types normalized.** `find_devices()` now returns one
  `SartoriusDiscoveryResult` per **probe attempt** (port × baudrate),
  not one per port. The result type matches the cross-library
  `DiscoveryResult` base shape: `ok`, `port`, `address`, `baudrate`,
  `protocol`, `device_info`, `error`, `elapsed_s`. Sartorius-specific
  framing details (`parity`, `stopbits`, `autoprint_active`,
  `pending_lines`) live on the `SartoriusDiscoveryResult` subclass.
  The old per-port `FindResult` summary becomes `DiscoverySummary`,
  exposed via the new `summarize_discovery(results)` helper.
- **`Sample` timestamp contract.** Three canonical fields per spec §C:
  `t_mono_ns` (was `monotonic_ns`), `t_utc` (was `midpoint_at`), plus a
  new optional `t_midpoint_mono_ns`. I/O provenance fields
  (`requested_at`, `received_at`, `latency_s`, `metadata`) are
  unchanged. Sink column names in `sample_to_row` follow the rename.
- **`record()` yields `Recording[T]`** instead of a bare async stream.
  Consume `recording.stream` to iterate, `recording.summary` for the
  live counters, `recording.rate_hz` / `recording.observed_rate_hz`
  for the cadence (spec §M).
- **`AcquisitionSummary` is mutable.** The recorder is the sole writer
  and updates counters in place during the run. `finished_at` is
  `None` until the context manager exits. Consumers must treat the
  summary as read-only.
- **`ErrorContext.address` property** added (returns `sbn_address`
  unchanged). The native field stays `sbn_address`; the property is
  the unified cross-lib accessor.

#### Added

- **`SartoriusTransientTransportError`** — new typed exception raised
  on cold-open USB races. Replaces capa's string-matching workaround
  for `"frame too short"` / `"got 0 bytes"`. Raised from two layers:
  `transport/serial.py` (`read_exact` returns 0 bytes inside the
  timeout window) and `protocol/xbpi/framing.py` (frame underrun
  below `MIN_FRAME_SIZE`). `open_device()` swallows up to 3 such
  transients on the first identify with a 50 ms backoff so consumers
  never see cold-open as a failure mode; post-open transients still
  surface to callers (spec §F).
- **`DeviceResult.success(value)` / `.failure(error)`** classmethod
  factories. Keyword construction still works (spec §E.0).
- **`PollSourceAdapter`** — wrap a single `Balance` as a `PollSource`
  for `record()`. Same class name across every sibling library; for
  sartoriuslib the method signature is
  `poll(names) -> Mapping[str, DeviceResult[Reading]]`. Replaces the
  shim capa carried at `capa/src/capa/devices/sartorius.py` (spec §E).
- **`Balance.snapshot()` → `SartoriusDeviceSnapshot`** — no-I/O
  identity + health snapshot. Base `DeviceSnapshot` carries `name`,
  `model`, `firmware`, `serial`, `connected`, `last_error`,
  `recoverable_error_count`, `captured_at`; the subclass adds
  `family`, `capabilities`, `protocol`, `mode` (spec §H).
- **`Session.recoverable_error_count`** public counter that the open-
  time retry loop and future inline retries bump on every transparently
  retried error (spec §J).
- **`sartoriuslib.units.to_pint(unit) -> str | None`** — free function
  mapping every `Unit` enum value to a pint-compatible string, or
  `None` for units pint can't model (Hong Kong tael, Austrian carat,
  ...). `pint` is **not** a runtime dependency (spec §K).
- **Top-level re-exports**: `sample_to_row`, `PollSourceAdapter`,
  `Recording`, `DiscoverySummary`, `SartoriusDiscoveryResult`,
  `SartoriusDeviceSnapshot`, `DeviceSnapshot`, `to_pint`,
  `summarize_discovery`, `SartoriusTransientTransportError`.

#### Tooling

- Cross-lib import-symmetry smoke test
  (`tests/unit/test_unified_api.py::TestCrossLibImportSymmetry`)
  verifies every sibling library can advertise the same top-level
  exports.

## [0.3.1] - 2026-05-14

### Added

- `find_devices()` — multi-port baudrate-sweep discovery helper plus
  `DEFAULT_DISCOVERY_BAUDRATES` and `FindResult`. Mirrors
  `alicatlib.find_devices` so multi-adapter consumers (capa's Setup-editor
  Discover dialog, `capa hardware discover`) can render every adapter's
  discovery rows uniformly. First hit per port wins; port-open failures
  short-circuit remaining bauds; ports default to
  `anyserial.list_serial_ports()`.

## [0.2.0] - 2026-05-10

### Changed

- **Breaking**: `Balance.aclose()` → `Balance.close()` (and `Session.aclose()` →
  `Session.close()`). Cross-package alignment with `nidaqlib`, `watlowlib`, and
  `alicatlib`. `__aexit__`, sync façade, `SartoriusManager`, CLI, and tests
  updated accordingly.
- **Breaking**: `Sample.elapsed_s` → `Sample.latency_s` (sink row column
  renamed to match). `ErrorContext.elapsed_s` is unchanged — it remains a
  separate concept on `SartoriusError.context`.
- **Breaking**: `DeviceResult.protocol` removed. The protocol is now sourced
  from `result.error.context.protocol` on failure and `Reading.protocol` on
  success; the streaming recorder resolves `Sample.protocol` from those same
  fields.
- Sink scalar type widened to include `bool` for cross-package consistency;
  schema inference now maps `bool` correctly for `SqliteSink`, `ParquetSink`,
  and `PostgresSink` (regression test added).

### Added

- `SartoriusError.with_context()` fluent enrichment, structured `__str__`, and
  `ErrorContext.merged()` (alicatlib pattern). `ErrorContext.extra` is wrapped
  in `MappingProxyType`.

### Documentation

- `open_device` documented as defaulting to `ProtocolKind.XBPI`; `AUTO` is
  opt-in.
- Replaced stale `aclose` / `DeviceResult.protocol` language with `close()`
  and protocol-from-`Reading` / `error.context`.
- Corrected the xBPI checksum example in `docs/protocol.md`.
- Fixed recorder / `pipe()` `AcquisitionSummary` wording.
- Refreshed architecture / testing references from the old RE workspace to
  current `sartoriuslib` paths; removed brittle source-line anchors from
  active docs.

### Tooling

- Dev-dependency bumps via Dependabot: `zensical` 0.0.36 → 0.0.40,
  `hypothesis` 6.152.2 → 6.152.4, `mypy` 1.20.2 → 2.0.0.

## [0.1.0] - 2026-04-25

### Added

Initial release of `sartoriuslib` — an async-first Python driver for
Sartorius lab balances over RS-232/USB. See [docs/design.md](docs/design.md)
for the architectural reference.

#### Public API

- `open_device(port, *, protocol=XBPI, ...)` and `open_balance` — async
  factory returning a `Balance` over a serial port or any duck-typed
  `Transport`. Supports `ProtocolKind.XBPI`, `ProtocolKind.SBI`, and
  opt-in `ProtocolKind.AUTO` (passive autoprint sniff → xBPI `0x02`
  probe → SBI `ESC x1_` probe → fail clearly).
- `Balance` facade — protocol-neutral surface across both wire
  protocols. Weight reads (`poll`, `read_net`, `read_gross`,
  `read_tare_value`), state ops (`tare`, `zero`, `status`,
  `identify`), metrology (`capacity`, `increment`, `temperature`),
  parameter R/W with typed accessors (`get_filter_mode` /
  `set_filter_mode`, `get_display_unit` / `set_display_unit`,
  `get_auto_zero` / `set_auto_zero`, `get_isocal_mode` /
  `set_isocal_mode`, `get_tare_behavior` / `set_tare_behavior`,
  `get_menu_access` / `set_menu_access`), persistence (`save_menu`,
  `reload_menu`), calibration (`internal_adjust`, `last_cal_record`),
  raw escape hatches (`raw_xbpi`, `raw_sbi`), and host-side lifecycle
  ops (`configure_protocol`, `set_baud_rate`, `write_sbn_address`).
- `SartoriusManager` (alias `BalanceManager`) — coordinates many
  balances across one or more serial ports with shared per-port I/O
  locks, `ErrorPolicy.RAISE` / `ErrorPolicy.RETURN`, and
  `DeviceResult[T]` aggregation.
- `record(source, rate_hz=..., duration=..., overflow=...)` —
  absolute-target streaming scheduler producing `Sample` batches with
  send/receive timing provenance.
- Sinks: `InMemorySink`, `CsvSink`, `JsonlSink`, `SqliteSink` in the
  base install; `ParquetSink` (extra `[parquet]`) and `PostgresSink`
  (extra `[postgres]`).
- Sync façade: `Sartorius.open(port, ...)`, `SyncBalance`,
  `SyncSartoriusManager`, sync `record` / `pipe`, sync sink
  variants. CI parity test enforces method/signature alignment with
  the async surface.
- Public types: `Reading`, `BalanceStatus`, `DeviceInfo`, `Quantity`,
  `BalanceState`, `BalanceFamily`, `Capability`, `Availability`,
  `SafetyTier`, `ProtocolKind`, `Sign`, `Unit`, `Sample`,
  `AcquisitionSummary`, `OverflowPolicy`, `DetectionResult`,
  `DiscoveryResult`, `SessionState`, `FirmwareVersion`,
  `CalRecord`, `TemperatureReading`.
- Public error hierarchy: `SartoriusError` and typed subclasses
  covering connection, transport, timeout, frame, parse, protocol,
  capability, configuration, validation, range, command rejection,
  confirmation, autoprint state, sink, and firmware errors. Every
  raise carries an `ErrorContext`.

#### Wire protocols

- **xBPI** — full bidirectional codec. Framing (length-prefix +
  checksum), TLV encode/decode (tags `0x11` / `0x12` / `0x14` /
  `0x21` / `0x22` / `0x24`), opcode/error-code tables, unit
  decoding, and subtype-family parsers (measurement `0x48`, status
  block, typed-float `0x35`, error `0x01`, long string blobs).
- **SBI** — line codec (`\r\n` termination, ESC tokens, autoprint
  recogniser), command-token table (`ESC P/T/V/x1_/x2_/x3_` plus
  filter / ionizer / draft-shield / front-panel-key tokens with a
  read-only safe-list), weight-line parser (sign / stability /
  unit / decimals / overload / underload), refusal markers, and an
  autoprint state machine (passive sniff at open, opportunistic
  surprise-reply detection mid-session, explicit
  `refresh_sbi_autoprint_state` resync).
- **Semantic parity** — keystone test suite proves xBPI and SBI
  fixtures decode to equal `Reading` fields (modulo `protocol`,
  `raw`, `sequence`) for every common balance state.

#### Session and gates

- Per-balance `Session` enforces the gate stack from
  [docs/design.md](docs/design.md) §6.1 in order: **safety →
  protocol → known-denied → priors → execute**, with availability
  cache updates per §6.1.1 (success → `SUPPORTED`, `0x04` →
  `UNSUPPORTED` sticky, `0x06` → `INAPPLICABLE` retryable, timeout
  / parse errors leave state unchanged). `strict=True` promotes
  prior mismatches to pre-I/O refusals; default emits a one-shot
  `SartoriusCapabilityWarning`.
- `0xBA` config-counter-keyed result cache with explicit
  invalidation hooks for the `p13` / `p50` caveat
  (writes that don't tick the counter).
- `SAFE_READ_ONLY_OPCODES` frozenset gates the xBPI raw escape
  hatch; `SBI_READ_ONLY_TOKENS` does the same for SBI.

#### Transport layer

- `Transport` Protocol with `read_exact` (xBPI length-prefix
  framing), `read_until` (SBI lines), and `read_available` (passive
  autoprint sniff); `reopen` accepts `baudrate` / `parity` /
  `stopbits` overrides for the WZA SBI→xBPI flip.
- `SerialSettings` dataclass with 8-O-1 defaults at 9600 baud.
- `SerialTransport` over `anyserial` with normalised error mapping.
- `FakeTransport` (re-exported from `sartoriuslib.testing`) for
  scripted in-process tests with reopen / timeout / write-log
  introspection.

#### CLI

- `sarto-read PORT [--protocol auto|xbpi|sbi]` — open + identify +
  one poll.
- `sarto-discover PORT [--json]` — wraps `discover_port`.
- `sarto-decode --xbpi HEX [HEX ...] | --sbi LINE` — offline
  decoder.
- `sarto-capture PORT --rate HZ --duration S --out FILE` —
  `record(...)` into a sink chosen by file extension.
- `sarto-raw PORT --xbpi 0xNN [HEX ...] | --sbi "ESC P"
  [--confirm]` — bypasses the typed Command layer.
- `sarto-configure {switch-protocol|set-baud-rate|write-sbn-address}
  PORT --confirm` — port-level maintenance helpers.
- `sarto-diag {snapshot|tap|stream|sweep|argfuzz}` — diagnostics
  namespace; destructive operations require
  `--i-understand-this-is-destructive`.
- All CLIs accept `--fixture FILE` to drive a scripted
  `FakeTransport` (xBPI `> hex / < hex` or SBI `> ESC P / < line`,
  parser auto-detected) so end-to-end tests run without hardware.

#### Testing support

- `sartoriuslib.testing` re-exports `FakeTransport`, `ScriptedReply`,
  `canned_frames` (real-balance byte-accurate xBPI TX/RX frames for
  MSE / WZA / BCE), `build_identify_script`,
  `build_metrology_script`, `build_sbi_identify_script`,
  `parse_xbpi_fixture`, and `parse_sbi_fixture`.
- AnyIO test plugin runs every async test against `asyncio`,
  `asyncio+uvloop`, and `trio`.
- Hardware tests gated behind `SARTORIUSLIB_ENABLE_STATEFUL_TESTS`
  / `SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS`.

#### Examples

- `examples/combined_mfc_balance.py` — runs `alicatlib.AlicatManager`
  and `sartoriuslib.SartoriusManager` concurrently in one task
  group, both writing to one shared SQLite database.

#### Tooling

- `pyproject.toml` with hatch-vcs versioning, ruff + mypy + pyright
  (strict), pre-commit, CI / release / docs workflows, issue and PR
  templates.
- Base runtime dependencies: `anyio` and `anyserial`. No other
  required dependencies in the base install.
- Python `>=3.13` (PEP 695 generics are load-bearing).

### Notes

- `docs/protocol.md` §3.3 uses the corrected checksum `0x07` for the
  worked measurement example. The decoder regression suite still covers
  the previous bad-checksum fixture to ensure mismatches are reported
  while the self-consistent body remains inspectable.
