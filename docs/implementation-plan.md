---
description: Historical phased implementation plan for sartoriuslib, retained for reference. Current API and behavior are documented in the guides and API reference.
---

# sartoriuslib — Implementation Plan

!!! note "Historical planning document"
    This file is retained for implementation history. It was written when the
    repository was still mostly scaffolded; the current API and behavior are
    documented in the guide pages and the API reference.

Phased roadmap for constructing `sartoriuslib` per [design.md](design.md) and
[protocol.md](protocol.md), with alicatlib (`~/Documents/git/alicatlib/`) as
the structural reference. Current state: ~95 stub files (~1,089 LOC), with the
exception hierarchy, enums, config, firmware, and logging already implemented;
everything below `protocol/`, inside `commands/`, `devices/`, `transport/`,
`sinks/`, `streaming/`, `sync/`, and `cli/` is empty.

## Sizing and approach

- **Target size.** Parity with alicatlib (~21k LOC) suggests ~15–20k LOC: less
  because Sartorius has fewer command variants, more because of dual-protocol
  — they roughly cancel.
- **Strategy.** Vertical slices, bottom-up through the layer map
  ([design.md §2](design.md)). xBPI first; SBI architected from day one,
  filled in at Phase 7. Async canonical; sync wraps async.
- **Commit rhythm.** Each phase is merge-ready. Every phase lands with tests
  and no regressions in earlier phases.
- **Test strategy.** AnyIO plugin (already in
  [tests/conftest.py](../tests/conftest.py)); fixture-driven golden tests for
  wire decoding; `FakeTransport` for session tests; real-hardware tests gated
  by env vars
  ([pyproject.toml:277-289](../pyproject.toml#L277-L289)).

## Phase boundaries (milestones visible to users)

```
Phase 0 ─── skeleton scrubbed; pyproject drift fixed
Phase 1 ─── Transport Protocol + SerialTransport + FakeTransport
Phase 2 ─── xBPI wire codec (framing + TLV + parser + tables)
Phase 3 ─── Command[Req,Resp] + XbpiVariant + Session + ProtocolClient
Phase 4 ─── open_device + Balance facade + core reads + α   ← usable driver
Phase 5 ─── Parameter table + typed accessors + metrology + cache
Phase 6 ─── Manager + streaming + sinks + sync facade + β   ← full acquisition
Phase 7 ─── SBI framing/parser/variants + semantic parity + 1.0   ← stable
Phase 8 ─── Discovery + configure_protocol + maintenance + diagnostics CLI
Phase 9 ─── Extras (Parquet/Postgres), combined-harness examples, 1.1
```

---

## Phase 0 — Hygiene and package glue

**Size.** ~200 LOC touched, mostly metadata.

1. Confirm [pyproject.toml](../pyproject.toml) metadata matches
   `sartoriuslib` (already done — verify: no leftover `sartoriustesting`
   refs anywhere).
2. Verify `import sartoriuslib` works end-to-end;
   [tests/unit/test_package_imports.py](../tests/unit/test_package_imports.py)
   already does this.
3. Add `testing.py` re-exports to `sartoriuslib/__init__.py` so
   `from sartoriuslib.testing import ...` works once populated.
4. Convert existing `captures/` (if any exist in the RE workspace) to
   [tests/fixtures/captures/](../tests/fixtures/captures/) in the §8.2
   format.

**Acceptance.** `uv run pytest` passes on the existing smoke tests; no stale
names in metadata.

---

## Phase 1 — Transport layer

**Size.** ~600 LOC + ~400 LOC tests. Models alicatlib's `transport/` closely.

**Files.**

- [transport/base.py](../src/sartoriuslib/transport/base.py) — `Transport`
  Protocol, `SerialSettings` dataclass. Must cover xBPI's binary
  length-prefix reads (`read_exact`) AND SBI's line reads (`read_until`).
  Passive sniff needs `read_available(idle_timeout, max_bytes)`.
- [transport/serial.py](../src/sartoriuslib/transport/serial.py) —
  `SerialTransport` over `anyserial`, with
  `reopen(baudrate, parity, stopbits)` for the WZA SBI→xBPI flip.
- [transport/fake.py](../src/sartoriuslib/transport/fake.py) — scripted
  `FakeTransport` with xBPI frame helpers and SBI line helpers; write-log
  introspection for assertions.

**Tests.**

- `test_serial_transport_roundtrip.py` (integration, hardware-gated).
- `test_fake_transport.py` — scripted sequences, timeout behavior,
  write-log assertions.
- Property test: `read_exact(n)` returns exactly `n` bytes or raises
  `SartoriusTimeoutError`.

**Acceptance.** `FakeTransport` can deliver both binary xBPI frames and SBI
ASCII lines; `SerialTransport` matches alicatlib's contract.

---

## Phase 2 — xBPI wire codec

**Size.** ~800 LOC + ~1200 LOC tests (golden-heavy). Pure byte-level code,
fully testable without hardware.

**Files (all under
[protocol/xbpi/](../src/sartoriuslib/protocol/xbpi/)).**

- `framing.py` — `build_command(opcode, args, src_sbn, dst_sbn) → bytes`,
  `parse_frame(bytes) → XbpiFrame`, `checksum(bytes)`. Port from the
  existing RE workspace `frame.py`.
- `tlv.py` — TLV tag table from [protocol.md §5](protocol.md), encode/decode
  helpers, multi-TLV response splitter.
- `tables.py` — opcode → name map, subtype → family map
  ([protocol.md §4](protocol.md)), error-code map (`0x03`, `0x04`, `0x06`,
  `0x07`, `0x10`, `0x11`).
- `units.py` — xBPI unit byte → `registry.Unit` mapping.
- `parser.py` — decoders per subtype family: `measurement` (`0x48`, short +
  long), `short_data` (`0x21`/`0x22`/`0x24`), `typed_float` (`0x35`),
  `long_data` (`0x50`/`0x54`), `error` (`0x01`).
- `types.py` — `XbpiFrame`, `XbpiReply`, `MeasurementBody` frozen
  dataclasses.

**Tests.** In `tests/unit/protocol/xbpi/`:

- `test_framing.py` — checksum round-trip; worked examples from
  [protocol.md §3.3](protocol.md).
- `test_tlv.py` — every tag in [protocol.md §5](protocol.md); multi-TLV
  response from `0x55` index 0.
- `test_parser_measurement.py` — short measurement `0b 41 48 ...` decodes
  to the exact Reading from the protocol doc.
- `test_parser_errors.py` — every `0x01 XX` code maps to the right typed
  reason.
- `test_parser_stringblobs.py` — `0x50`/`0x54` ASCII with null-padding from
  identify captures.
- `test_parser_typed_float.py` — `0x35` temperature/capacity/increment
  decoding.
- Property tests: malformed length, bad checksum, truncated body →
  `SartoriusFrameError`.

**Acceptance.** Every capture in
[tests/fixtures/captures/](../tests/fixtures/captures/) round-trips through
the codec. No parser unit understands anything about protocol selection,
sessions, or I/O.

---

## Phase 3 — Command layer + Session + ProtocolClient

**Size.** ~1400 LOC + ~800 LOC tests. The architectural core; the seam that
makes dual-protocol tractable.

**Files.**

- [commands/base.py](../src/sartoriuslib/commands/base.py) —
  `Command[Req, Resp]`, `XbpiVariant[Req, Resp]`,
  `SbiVariant[Req, Resp]`, `CommandContext`, `SafetyTier` (already in
  [devices/capability.py](../src/sartoriuslib/devices/capability.py)).
- [protocol/base.py](../src/sartoriuslib/protocol/base.py) —
  `ProtocolClient` Protocol with
  `execute(variant, request_bytes) → XbpiReply | SbiReply`, plus I/O-lock
  contract.
- `protocol/xbpi/client.py` (new file) — `XbpiProtocolClient` —
  single-in-flight, per-call timeout, drains on error, maps `0x01 XX` to
  typed exceptions.
- [protocol/client.py](../src/sartoriuslib/protocol/client.py) — factory
  that picks xBPI or SBI based on `ProtocolKind`.
- [devices/session.py](../src/sartoriuslib/devices/session.py) — `Session`
  owns I/O lock; applies gates in the [§6.1](design.md#L340) order (safety
  → protocol → known-denied → priors → execute → availability update per
  [§6.1.1](design.md#L362)); maintains per-command availability cache;
  holds the `0xBA` config-counter snapshot.

**Tests.**

- `test_command_dispatch.py` — command with only xBPI variant on SBI
  session raises `SartoriusProtocolUnsupportedError` pre-I/O, never writes
  bytes.
- `test_safety_gates.py` — PERSISTENT/DANGEROUS without `confirm=True`
  raises `SartoriusConfirmationRequiredError`, no bytes written to
  FakeTransport.
- `test_availability_cache.py` — first `0x04` flips `UNSUPPORTED` and
  sticky; second call refuses pre-I/O; `0x06` flips `INAPPLICABLE` but
  next call tries again; timeout leaves availability unchanged.
- `test_session_lock.py` — two concurrent `execute()` calls on the same
  session serialize; different sessions on different ports run
  concurrently.
- `test_strict_mode.py` — `strict=True` refuses on family prior mismatch;
  `strict=False` emits `SartoriusCapabilityWarning` and attempts.

**Acceptance.** Session is fully testable with `FakeTransport`; all §6.1
gates are proven; no command in the system knows about serial ports or
transport details.

---

## Phase 4 — open_device + Balance facade + core reads — **α milestone**

**Size.** ~1000 LOC + ~600 LOC tests. First externally useful milestone.

**Files.**

- [commands/weight.py](../src/sartoriuslib/commands/weight.py) — `READ_NET`
  (xBPI `0x1E`), `READ_NET_HIRES` (`0x1F`), `READ_GROSS` (`0x20`),
  `READ_TARE` (`0x22`). Each ships with an xBPI variant only for now.
- [commands/tare.py](../src/sartoriuslib/commands/tare.py) — `TARE`
  (`0x14`), `ZERO` (`0x18`) — STATEFUL, run freely.
- [commands/status.py](../src/sartoriuslib/commands/status.py) — `STATUS`
  (`0x32`/`0x30`).
- [commands/identity.py](../src/sartoriuslib/commands/identity.py) —
  `IDENTIFY` — composite that merges `0x02` + `0x07` + `0x00` into
  `DeviceInfo`.
- [commands/raw.py](../src/sartoriuslib/commands/raw.py) — `raw_xbpi` with
  the READ_ONLY safe-list ([design.md §6.1](design.md#L346)).
- [devices/models.py](../src/sartoriuslib/devices/models.py) — `Reading`,
  `BalanceStatus`, `DeviceInfo`, `Quantity`, `ProbeOutcome`
  ([design.md §7](design.md#L417)).
- [devices/balance.py](../src/sartoriuslib/devices/balance.py) — `Balance`
  facade with `poll/tare/zero/identify/status/raw_xbpi`.
- [devices/factory.py](../src/sartoriuslib/devices/factory.py) —
  `open_device(port, *, protocol=XBPI, ...)` — only forced-xBPI this
  phase; AUTO lands in Phase 8.
- Discovery/AUTO remains stubbed.
- [testing.py](../src/sartoriuslib/testing.py) — export `FakeTransport`,
  `canned_frames.identify_mse`, `canned_frames.net_weight`,
  `parse_xbpi_fixture`.

**Tests.**

- `test_open_device_forced_xbpi.py` end-to-end: FakeTransport scripted from
  real capture → `open_device(..., protocol=ProtocolKind.XBPI)` →
  `balance.poll()` returns the expected `Reading`.
- `test_identify_merged.py` — DeviceInfo composite correctness across MSE
  / WZA / BCE canned frames.
- Stability-bit preference: measurement-frame flag `0x40` is read, not a
  separate `0x32` call ([design.md §7 note](design.md#L435)).
- `test_error_hierarchy.py` — ErrorContext present on every typed error;
  opcode/command/port/model all captured.

**Acceptance.**

```python
async with await open_device("/dev/ttyUSB0", protocol=ProtocolKind.XBPI) as bal:
    r = await bal.poll()          # Reading
    await bal.tare()
    info = await bal.identify()   # DeviceInfo
```

works against FakeTransport and real MSE hardware. CHANGELOG gets an α entry.

---

## Phase 5 — Parameter table, typed accessors, metrology, cache

**Size.** ~1200 LOC + ~700 LOC tests.

**Files.**

- [commands/metrology.py](../src/sartoriuslib/commands/metrology.py) —
  `READ_CAPACITY` (`0x0C`), `READ_INCREMENT` (`0x0D`), `READ_TEMPERATURE`
  (`0x76`, TLV-21 sensor index).
- [commands/parameters.py](../src/sartoriuslib/commands/parameters.py) —
  `READ_PARAMETER` (`0x55`), `WRITE_PARAMETER` (`0x56`).
- [commands/calibration.py](../src/sartoriuslib/commands/calibration.py) —
  `LAST_CAL_RECORD` (`0xB9`), `INTERNAL_ADJUST` (`0x28`, DANGEROUS).
- [commands/system.py](../src/sartoriuslib/commands/system.py) —
  `CONFIG_COUNTER` (`0xBA`), `SAVE_MENU` (`0x47`), `RELOAD_MENU` (`0x46`).
- [registry/modes.py](../src/sartoriuslib/registry/modes.py) —
  `FilterMode`, `AutoZeroMode`, `DisplayAccuracyMode`, `TareMode`,
  `OutputMode`.
- [registry/units.py](../src/sartoriuslib/registry/units.py) — `Unit` enum
  (g/kg/mg/N/…/UNKNOWN).
- [registry/parameters.py](../src/sartoriuslib/registry/parameters.py) —
  typed-accessor table: index → enum + setter validator.
- [registry/aliases.py](../src/sartoriuslib/registry/aliases.py) — fuzzy
  string → enum lookup (mirrors alicatlib's pattern).
- Extend [devices/balance.py](../src/sartoriuslib/devices/balance.py):
  `capacity/increment/temperature`, typed `get_X/set_X`, raw
  `read_parameter/write_parameter`, `save_menu/reload_menu`,
  `internal_adjust/last_cal_record`.
- Extend [devices/session.py](../src/sartoriuslib/devices/session.py):
  implement the `0xBA`-keyed cache from
  [§6.3](design.md#L385), with the caveat test (items not proven tied to
  `0xBA` stay uncached or invalidate on any explicit write).

**Tests.**

- `test_parameter_table.py` — read parameter 1 decodes to `FilterMode`;
  setter validates enum, sends TLV correctly.
- `test_cache_invalidation.py` — `capacity()` cached; `0xBA` bump flushes;
  unrelated fetch doesn't.
- `test_cache_caveat.py` — persistent-pref write that doesn't tick `0xBA`
  still invalidates the cache for that index.
- Safety proof: `write_parameter` without `confirm=True` → no bytes ever
  written.

**Acceptance.** The α surface plus typed parameter accessors is stable.
Every typed getter/setter has a golden-fixture test per family we have
captures for.

---

## Phase 6 — Manager, streaming, sinks, sync — **β milestone**

**Size.** ~3500 LOC + ~1800 LOC tests. Big phase; split across multiple
commits.

**Files.**

- [streaming/sample.py](../src/sartoriuslib/streaming/sample.py) — `Sample`
  frozen dataclass with
  device/reading/requested_at/received_at/midpoint_at/monotonic_ns/latency_s/protocol/metadata/error.
- [streaming/recorder.py](../src/sartoriuslib/streaming/recorder.py) —
  `record(...)` absolute-target scheduler ported from alicatlib's pattern;
  `AcquisitionSummary`, `OverflowPolicy`, `PollSource` Protocol.
- `streaming/stream_session.py` (new) — `StreamingSession` used by
  `Balance.stream(mode=…)`.
- [manager.py](../src/sartoriuslib/manager.py) — `SartoriusManager` with
  ref-counted per-port client pool, port canonicalization (realpath on
  POSIX, COM normalization on Windows), `ErrorPolicy.RAISE` /
  `ErrorPolicy.RETURN`, `DeviceResult[T]`,
  `execute(command, requests_by_name)`.
- [sinks/base.py](../src/sartoriuslib/sinks/base.py) — `SampleSink`
  Protocol, `sample_to_row()` with the
  [design.md §10](design.md#L564) schema.
- [sinks/_schema.py](../src/sartoriuslib/sinks/_schema.py) — `SchemaLock`.
- [sinks/memory.py](../src/sartoriuslib/sinks/memory.py),
  [csv.py](../src/sartoriuslib/sinks/csv.py),
  [jsonl.py](../src/sartoriuslib/sinks/jsonl.py),
  [sqlite.py](../src/sartoriuslib/sinks/sqlite.py).
- [sync/portal.py](../src/sartoriuslib/sync/portal.py) — `SyncPortal`
  anyio wrapper; single-member ExceptionGroup unwrap.
- [sync/balance.py](../src/sartoriuslib/sync/balance.py) — `Sartorius`
  entry + `SyncBalance`.
- [sync/manager.py](../src/sartoriuslib/sync/manager.py) —
  `SyncSartoriusManager`.
- [sync/recording.py](../src/sartoriuslib/sync/recording.py),
  [sync/sinks.py](../src/sartoriuslib/sync/sinks.py).

**Tests.**

- `test_manager_concurrency.py` — devices on distinct ports run in
  parallel; same-port serializes through one lock.
- `test_manager_port_canonicalization.py` — symlinks collapse; Windows COM
  variants match.
- `test_record_cadence.py` — absolute-target:
  `target[n] = start + n/rate_hz`; drift bounded to one tick under slow
  polls.
- `test_record_overflow.py` — BLOCK / DROP_NEWEST / DROP_OLDEST semantics;
  summary counts match.
- `test_sinks_csv.py`, `test_sinks_jsonl.py`, `test_sinks_sqlite.py` —
  round-trip schema; null on overload/underload;
  `error_type`/`error_message` on failed samples.
- `test_sync_parity.py` (CI-enforced) — mechanically compares async
  Balance methods to `SyncBalance` methods for signature parity (modulo
  `await`).
- `test_sync_portal_shutdown.py` — portal closes cleanly even with
  in-flight `record(...)`.

**Acceptance.**

```python
async with SartoriusManager() as mgr:
    await mgr.add("bal1", "/dev/ttyUSB0", protocol="xbpi")
    async with record(mgr, rate_hz=10, duration=60) as stream:
        async for batch in stream:
            ...
```

works; sync equivalent works; CSV/JSONL/SQLite sinks accept 10 Hz for 60 s
without drop. CHANGELOG gets a β entry.

---

## Phase 7 — SBI support — **1.0 milestone**

**Size.** ~1500 LOC + ~900 LOC tests. Fill in what Phase 3 left as a seam.

**Files.**

- [protocol/sbi/framing.py](../src/sartoriuslib/protocol/sbi/framing.py) —
  line codec (`\r\n` termination), ESC-token handling (`ESC P`, `ESC T`),
  autoprint-line recognizer.
- [protocol/sbi/tables.py](../src/sartoriuslib/protocol/sbi/tables.py) —
  command-token table, unit-string map, sign/overload/underload parsers.
- [protocol/sbi/parser.py](../src/sartoriuslib/protocol/sbi/parser.py) —
  line → `SbiReply`; weight-line parser produces the same `Reading` shape
  as xBPI.
- [protocol/sbi/types.py](../src/sartoriuslib/protocol/sbi/types.py) —
  `SbiLine`, `SbiReply`.
- `protocol/sbi/client.py` (new) — `SbiProtocolClient`.
- Add SBI variants to existing commands: `READ_NET` (`ESC P`), `TARE`
  (`ESC T`), `ZERO` (`ESC V`), `IDENTIFY` (`ESC x1_` / `ESC x2_` /
  `ESC x3_`), and `STATUS` as a best-effort `ESC P`-derived status where
  applicable. Commands with no verified SBI variant (`READ_GROSS`,
  `READ_TARE_VALUE`, metrology, parameter table, cal record) keep
  `sbi=None` — Session refuses them pre-I/O on SBI sessions. Note:
  `ESC O` is **block keys**, not gross weight, per `docs/sbi_commands.md`.
- Streaming adds `Balance.stream(mode="poll")` default,
  `mode="autoprint"` consume-only. Temporary autoprint configuration
  remains blocked until a verified SBI parameter-write/read path for
  `p36` exists; do not silently switch via xBPI from an SBI session.
- Live autoprint handling must tolerate attaching mid-line, blank unit fields,
  and status records. Observed MSE SBI output leaves the unit field blank
  while unstable and emits `Stat     Cal.Int.` during internal calibration;
  those lines are non-weight status, not identity replies. While autoprint is
  active, read-only identity/status commands did not produce distinguishable
  replies on the stream, so `mode="autoprint"` remains the correct API for
  consuming already-enabled output. Forced SBI open passively detects this
  mode; `identify=True`, raw SBI calls that expect replies, and
  `stream(mode="poll")` fail clearly with `SartoriusAutoprintActiveError`,
  while `poll()` reads the next valid autoprint weight without writing. If the
  user toggles autoprint from the balance during an open session, surprise
  autoprint replies flip the session into consume-only mode; an explicit
  `refresh_sbi_autoprint_state()` re-sniffs the line and clears the flag when
  the port is quiet.

**Tests.**

- `test_sbi_framing.py` — every line format from captures.
- `test_sbi_weight_parser.py` — sign, stability, overload, underload,
  unit, decimal places.
- `test_semantic_parity.py` — **the keystone test:** xBPI and SBI fixtures
  for equivalent balance states produce `Reading` objects that compare
  equal modulo `protocol`, `raw`, and `sequence`.
- `test_sbi_autoprint_consume.py` — `stream(mode="autoprint")` without
  enabling it first fails loudly.
- `test_sbi_autoprint_detect.py` — forced SBI open detects already-enabled
  autoprint, preserves the sniffed line for the first read, and blocks
  command/reply APIs while allowing no-reply control tokens through normal
  safety gates.
- `test_sbi_autoprint_transition.py` — enabling autoprint mid-session is
  detected opportunistically from unsolicited output; disabling it is handled
  by explicit refresh before command/reply APIs resume.
- `test_sbi_autoprint_temporary.py` — initially proves
  `temporary_autoprint=True` is rejected without `confirm=True` and remains
  explicitly unimplemented with `confirm=True` until SBI `p36` writes are
  verified; promote to enable/restore tests once the command is known.
- Error fuzz: malformed SBI lines → `SartoriusParseError`, never a crash.

**Acceptance.** A WZA device in SBI mode and an MSE device in xBPI mode
both produce semantically identical `Reading` objects through the same
`Balance.poll()` call. CHANGELOG 1.0.

---

## Phase 8 — AUTO detect, configure_protocol, maintenance, diagnostics CLI

**Size.** ~1500 LOC + ~600 LOC tests.

**Files.**

- [protocol/detect.py](../src/sartoriuslib/protocol/detect.py) —
  conservative detect ([design.md §4.3](design.md#L211)): drain input →
  short passive sniff for SBI autoprint → xBPI `0x02` probe → SBI
  identify probe → clear failure. No opcode sweeps, no fuzzing, no baud
  sweeps.
- [devices/discovery.py](../src/sartoriuslib/devices/discovery.py) —
  `DiscoveryResult`, `sarto-discover` helpers. Narrow serial-settings
  probing lives here, not in `open_device`.
- [maintenance.py](../src/sartoriuslib/maintenance.py) —
  `switch_protocol(port, target, *, confirm=True)`,
  `set_baud_rate(...)`, `write_sbn_address(...)` — the one-shot
  port-level forms promised by [design.md §16.6](design.md#L760).
- Extend [devices/balance.py](../src/sartoriuslib/devices/balance.py):
  `configure_protocol(protocol, *, confirm=True)` with the WZA→xBPI flip
  sequence and post-switch reopen at new serial settings.
- [cli/read.py](../src/sartoriuslib/cli/read.py),
  [cli/discover.py](../src/sartoriuslib/cli/discover.py),
  [cli/capture.py](../src/sartoriuslib/cli/capture.py),
  [cli/raw.py](../src/sartoriuslib/cli/raw.py),
  [cli/decode.py](../src/sartoriuslib/cli/decode.py),
  [cli/configure.py](../src/sartoriuslib/cli/configure.py).
  `sarto-decode` works offline from hex.
- `cli/diagnostics/*` — port the existing RE tools from the workspace
  (snapshot/sweep/argfuzz/tap/stream) under the `sarto-diag` namespace
  with `--i-understand-this-is-destructive` gates.

**Tests.**

- `test_detect_xbpi.py`, `test_detect_sbi_autoprint.py`,
  `test_detect_sbi_probe.py`, `test_detect_fails_cleanly.py`.
- `test_configure_protocol_wza_flip.py` — confirmed operation only;
  reopens at new baud/parity; rolls back on failure.
- CLI integration via `CliRunner` or subprocess with FakeTransport
  injected via `--fixture` flag.

**Acceptance.** `sarto-read /dev/ttyUSB0` identifies an unknown balance,
chooses xBPI or SBI correctly, prints one reading.
`sarto-decode --xbpi 0b4148bba3d70a3d3082 45 55` decodes offline.

---

## Phase 9 — Extras + examples + 1.1

**Size.** ~600 LOC + docs.

- [sinks/parquet.py](../src/sartoriuslib/sinks/parquet.py) behind
  `[parquet]` extra.
- [sinks/postgres.py](../src/sartoriuslib/sinks/postgres.py) behind
  `[postgres]` extra.
- [sync/sinks.py](../src/sartoriuslib/sync/sinks.py) adds sync variants
  of both.
- `examples/combined_mfc_balance.py` — running `AlicatManager` +
  `SartoriusManager` in one task group with one `record(...)` per
  manager, both writing to one SQLite DB.
- Migration notes (if anything changed in public API) in CHANGELOG.

---

## Cross-cutting concerns (owned continuously, not a phase)

| Concern | Lives where | Decision |
|---|---|---|
| Python version | `>=3.13` already set | Stay there; PEP 695 generics are load-bearing |
| Async backend | AnyIO | Test against `asyncio`, `asyncio+uvloop`, `trio` on every PR ([tests/conftest.py](../tests/conftest.py)) |
| Lint/type | Ruff + mypy + pyright | Already configured strict; do not relax |
| Hardware tests | `-m hardware` opt-in via env | Keep `SARTORIUSLIB_ENABLE_STATEFUL_TESTS`, `SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS` gates |
| Docs site | zensical already in deps | Start with API docs via mkdocstrings at β; user-facing narrative after 1.0 |
| CHANGELOG | One entry per phase | Human-written, not auto-gen |

## Open decisions to revisit

Per [design.md §16](design.md#L753), these stay open until field data arrives:

1. **`strict=False` as default** — commit to it for α; revisit after two
   external users file capability-related issues.
2. **Shared labsink** — duplicate on day one
   ([design.md §16.2](design.md#L757)); revisit only when a third consumer
   appears.
3. **Persistent `probe_report`** — defer.
4. **Firmware gates** — treat firmware as another soft prior; revisit when
   we have captures from ≥2 firmware revisions of one family.
5. **`SartoriusManager` vs `BalanceManager`** — ship `SartoriusManager`
   canonically with `BalanceManager = SartoriusManager` alias from day one.

## Risk register

| Risk | Phase it bites | Mitigation |
|---|---|---|
| SBI command table thinner than expected; semantic-parity test hard to satisfy | 7 | Allow `Reading.status_flags` to be a superset on xBPI; parity tests check only *common* fields |
| Autoprint `p36` restoration fails mid-stream (network drop) | 7 | Raise on context exit; log the failure; document in-docstring that p36 may remain modified |
| WZA protocol flip leaves port in unknown state | 8 | `configure_protocol` holds a bounded shield with clear BROKEN transition, matching alicatlib's `change_baud_rate` pattern |
| Cache invalidation via `0xBA` under-fires for some prefs | 5 | §6.3 caveat is tested; conservative "uncached or invalidate on explicit write" default |
| Multi-family capture coverage still thin | Every phase | Every command has at least one golden fixture per family we have captures for; gaps are explicit in `probe_report` |

## Immediate next step

Phase 0 is small. Recommended path: verify metadata hygiene, convert any
existing captures into the
[tests/fixtures/captures/](../tests/fixtures/captures/) format, then start
Phase 1 (Transport) — it's the shortest phase that unblocks the rest and is
fully testable without hardware.
