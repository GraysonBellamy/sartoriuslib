# sartoriuslib — Final Architecture

## 0. Scope

`sartoriuslib` is a full public driver for Sartorius balances, parallel in shape and scope to `alicatlib`. It is **not** a scoped-down testing helper — it is meant to be imported, depended on, and used alongside `alicatlib` from quick scripts to production acquisition harnesses. This document is the architecture reference for the current `sartoriuslib` repository; reverse-engineering notes that used to live in the validation workspace have been folded into [Wire protocol](protocol.md), diagnostics CLIs, and fixture-backed tests.

Design goals:

- Feel identical at the public API to `alicatlib` — a user with both libraries imports `open_device` / `Manager` / `Reading` / `sync.*` with the same mental model on each side.
- Handle the protocol duality (xBPI binary, SBI ASCII) transparently — the user gets the same `Reading` dataclass regardless of which wire protocol the balance is currently running.
- Ship full batteries: async core + sync facade, fake transport, CSV/JSONL/SQLite/Parquet/Postgres sinks, streaming acquisition, multi-device manager, typed error hierarchy, first-class testing module.
- Ship RE-friendly escape hatches (raw opcode access, raw SBI strings, offline hex-decode CLI) so forward protocol work keeps living inside the library rather than in ad-hoc scripts.

Async is canonical; sync is a thin portal wrapper. xBPI is the first protocol target because the current captures, notes, and tests already provide a strong foundation; SBI is implemented as a parallel adapter behind the same command/session/facade machinery.

## 1. Public API at a glance

Async (canonical):

```python
from sartoriuslib import open_device, SartoriusManager, Reading
from sartoriuslib import Unit, FilterMode, BalanceFamily, ProtocolKind, Capability

async with await open_device("/dev/ttyUSB0") as bal:
    reading = await bal.poll()            # -> Reading(value=199.995, unit=Unit.G, stable=True, ...)
    await bal.tare()
    async with bal.stream(rate_hz=10) as stream:
        async for r in stream:
            ...
```

Sync facade (same symbols under `sartoriuslib.sync`):

```python
from sartoriuslib.sync import Sartorius, SyncSartoriusManager, SyncCsvSink, record

with Sartorius.open("/dev/ttyUSB0") as bal:
    print(bal.poll())
```

`open_device` is the documented primary entry point for cross-library uniformity with `alicatlib`. `open_balance` exists as a friendly alias.

## 2. Layer map

```
┌───────────────────────────────────────────────────────────────┐
│  Facade     Balance / SartoriusManager / sync.Sartorius       │  devices/, manager.py, sync/
├───────────────────────────────────────────────────────────────┤
│  Session    one balance, one serial port, I/O lock, safety    │  devices/session.py
├───────────────────────────────────────────────────────────────┤
│  Command    Command[Req,Resp] + xbpi / sbi variants           │  commands/*.py
├───────────────────────────────────────────────────────────────┤
│  Protocol   XbpiProtocolClient  |  SbiProtocolClient          │  protocol/xbpi/, protocol/sbi/
├───────────────────────────────────────────────────────────────┤
│  Frame      xBPI frame codec    |  SBI line codec             │  protocol/xbpi/framing.py,
│                                                                  protocol/sbi/framing.py
├───────────────────────────────────────────────────────────────┤
│  Transport  SerialTransport / FakeTransport                   │  transport/
└───────────────────────────────────────────────────────────────┘
   ↑ streaming/, sinks/, registry/, testing.py, cli/, firmware.py, errors.py, config.py, maintenance.py
```

Each boundary is the same as alicatlib's; the only new seam is **two protocol clients under a shared interface**.

## 3. Package structure

```
src/sartoriuslib/
  __init__.py
  py.typed
  errors.py
  firmware.py
  config.py
  maintenance.py
  version.py
  _logging.py

  transport/
    base.py
    serial.py
    fake.py

  protocol/
    base.py              # ProtocolClient Protocol, shared reply types
    client.py            # factory + kind selection
    detect.py            # conservative auto-detection (§4.3)
    xbpi/
      framing.py         # xBPI frame codec (current frame.py → here)
      parser.py          # subtype decoders
      tlv.py
      tables.py          # opcode / subtype / error-code tables
      units.py
      types.py           # XbpiFrame, XbpiReply
    sbi/
      framing.py         # line codec, ESC token handling
      parser.py
      tables.py
      types.py           # SbiReply

  registry/
    units.py
    parameters.py        # parameter-table index → typed enum map
    modes.py             # FilterMode, AutoZeroMode, ...
    aliases.py

  commands/
    base.py              # Command[Req, Resp]
    common.py
    weight.py
    tare.py
    status.py
    identity.py
    metrology.py
    parameters.py
    calibration.py
    system.py
    raw.py

  devices/
    kind.py              # BalanceFamily
    capability.py        # Capability, SafetyTier
    models.py            # Reading, BalanceStatus, DeviceInfo, ...
    balance.py           # the single Balance facade
    session.py           # I/O lock, safety gates, cache invalidation
    factory.py           # open_device / open_balance
    discovery.py         # DiscoveryResult, sweep helpers

  streaming/
    sample.py            # Sample, AcquisitionSummary
    recorder.py          # record(...) absolute-cadence scheduler

  sinks/
    base.py              # Sink Protocol + common schema
    _schema.py
    memory.py
    csv.py
    jsonl.py
    sqlite.py
    parquet.py           # extras
    postgres.py          # extras

  manager.py             # SartoriusManager

  sync/
    portal.py            # anyio.BlockingPortal wrapper
    balance.py
    manager.py
    recording.py
    sinks.py

  testing.py             # public: FakeTransport, fixture parsers, canned frames

  cli/
    read.py              # sarto-read
    capture.py           # sarto-capture
    raw.py               # sarto-raw
    decode.py            # sarto-decode (offline, no hardware)
    discover.py          # sarto-discover
    configure.py         # sarto-configure (confirmed operations)
    diagnostics/
      snapshot.py        # sarto-diag snapshot
      sweep.py           # sarto-diag sweep
      argfuzz.py         # sarto-diag argfuzz
      tap.py             # sarto-diag tap
      stream.py          # sarto-diag stream
```

The stable library API lives above `protocol/`. Reverse-engineering tools are kept but clearly separated under `cli/diagnostics/` and never used during normal `open_device(...)` or discovery.

## 4. The protocol-duality seam

### 4.1 Design

**One `Balance` facade + pluggable `ProtocolClient` + per-protocol command variants.** The facade is protocol-agnostic; each command declares one or both variants, and the session picks based on the active protocol. Commands that exist on only one side declare only that variant; sending through the wrong protocol raises `SartoriusProtocolUnsupportedError` pre-I/O. Protocol mismatch is the **only** hard pre-I/O gate — family and capability gates are advisory (§6.1).

The **decoded `Reading` shape is identical across both protocols** — that is what makes a single facade viable. SBI emits `+     0.00 g  \r\n`; xBPI emits subtype-0x48 with an 8-byte body. Both decode to the same frozen dataclass. Protocol-specific detail (TLV tags, subtype families, SBI framing chars) never leaks past `Variant.decode`.

### 4.2 Command shape

Per-protocol work lives on **variant objects**, not as encode/decode methods bolted onto `Command`. `None` for an absent variant is self-documenting, keeps the opcode/token co-located with the logic that uses it, and makes missing protocol support trivial to test and reject pre-I/O.

```python
@dataclass(frozen=True, slots=True)
class Command[Req, Resp]:
    name: str
    xbpi: XbpiVariant[Req, Resp] | None            # None → command not defined for xBPI
    sbi:  SbiVariant[Req, Resp]  | None            # None → command not defined for SBI
    family_hints: frozenset[BalanceFamily]         # advisory prior from captures; empty = no prior
    capability_hints: Capability                   # advisory prior; 0 = no prior
    safety: SafetyTier                             # READ_ONLY / STATEFUL / PERSISTENT / DANGEROUS
    min_firmware: FirmwareVersion | None = None
    max_firmware: FirmwareVersion | None = None

@dataclass(frozen=True, slots=True)
class XbpiVariant[Req, Resp]:
    opcode: int
    def encode(self, ctx: CommandContext, request: Req) -> bytes: ...
    def decode(self, reply: XbpiReply, ctx: CommandContext) -> Resp: ...

@dataclass(frozen=True, slots=True)
class SbiVariant[Req, Resp]:
    token: bytes                                   # ASCII command token (e.g. b"\x1bP")
    def encode(self, ctx: CommandContext, request: Req) -> bytes: ...
    def decode(self, reply: SbiReply, ctx: CommandContext) -> Resp: ...
```

The session selects `cmd.xbpi` or `cmd.sbi` based on the active protocol; if the selected variant is `None`, it raises `SartoriusProtocolUnsupportedError` pre-I/O. That is the only hard pre-I/O gate that comes from the command definition itself — `family_hints` and `capability_hints` are advisory (§6.1).

### 4.3 Protocol selection on `open_device`

- `protocol=ProtocolKind.XBPI` (default): force xBPI at the caller's serial settings. This matches the original xBPI-first driver surface and avoids surprising probe traffic for users who already configured the balance.
- `protocol=ProtocolKind.SBI`: force SBI at the caller's serial settings. Forced SBI still performs a short passive autoprint sniff because already-enabled autoprint is a different operating mode on the same line framing; the sniff never writes to the balance and queues any observed line for later consumption.
- `protocol=ProtocolKind.AUTO`: opt in to conservative detection at the caller's serial settings → drain input → passively sniff a short window for SBI autoprint lines → probe xBPI with safe identity reads (opcode `0x02`) → probe SBI with an identify line → fail clearly if neither answers.
- `open_device` **never** changes the balance's protocol mode and **never** runs opcode sweeps, fuzzing, broad parameter probes, or wide baud/parity sweeps. Narrow-band serial auto-probing is an explicit discovery feature under `sarto-discover`, not part of `open_device`.
- Protocol-mode switching (the WZA SBI→xBPI flip) lives behind `Balance.configure_protocol(...)` and `sarto-configure` as an explicit, `confirm=True`-gated operation, never as a side effect of open.

### 4.4 SBI autoprint mode

SBI command/reply and SBI autoprint share ASCII line framing but behave
differently enough to model separately. In command/reply mode the host owns one
transaction at a time (`ESC P`, identity tokens, etc.). In autoprint mode the
balance owns the wire and continuously emits data or status records; observed
read-only query tokens do not produce distinguishable replies while that stream
is active.

The package therefore treats detected autoprint as a consume-only mode:

- `open_device(..., protocol=SBI, identify=True)` fails with
  `SartoriusAutoprintActiveError` if autoprint is already active.
- `open_device(..., protocol=SBI, identify=False)` succeeds and records
  `Session.sbi_autoprint_active`.
- `Balance.poll()` reads the next valid autoprint weight line without writing.
- `stream(mode="autoprint")` is the streaming API for already-enabled
  autoprint; `stream(mode="poll")` refuses while autoprint is active so the
  continuous output cannot build stale backlog.
- SBI command/reply APIs that expect replies fail with
  `SartoriusAutoprintActiveError` while autoprint is active. No-reply control
  tokens remain possible under their normal safety gates because they do not
  rely on parsing a response.
- If the user enables autoprint from the balance front panel mid-session, the
  next SBI command/reply call that sees unsolicited autoprint output marks the
  session active, preserves the observed line for later consumption, and raises
  `SartoriusAutoprintActiveError`.
- If the user disables autoprint mid-session, `poll()` /
  `stream(mode="autoprint")` may time out until the caller explicitly runs
  `refresh_sbi_autoprint_state()`. A quiet refresh clears autoprint mode and
  allows SBI command/reply APIs again; observed output keeps consume-only mode
  active.

## 5. Balance family + capability taxonomy

**One `Balance` class, no family subclasses.** Alicatlib has subclasses because `FlowMeter` and `PressureController` are fundamentally different devices — one accepts setpoints, the other does not. All Sartorius balances do the same thing: weigh, tare, zero. They differ in *how much extra* they can do, not *what they are*. Capability flags are the right dispatch mechanism; a subclass hierarchy would be ceremony. Family is a discriminator string on `DeviceInfo`, not a class identity. Family-specific convenience — if needed — ships as free functions in `sartoriuslib.families` rather than as subclasses.

```python
class ProtocolKind(StrEnum):
    XBPI = "xbpi"
    SBI  = "sbi"
    AUTO = "auto"

class BalanceFamily(StrEnum):
    CUBIS          = "cubis"            # MSE, full xBPI + extended opcodes
    OEM_WEIGH_CELL = "oem_weigh_cell"   # WZA — subset; SBI factory default is autoprint (all families ship in SBI)
    BASIC_LAB      = "basic_lab"        # BCE — MSE opcode subset, no Cubis extensions
    UNKNOWN        = "unknown"

class Capability(Flag):
    # Protocols the balance itself supports (may be more than the one currently active)
    XBPI_SUPPORT        = auto()
    SBI_SUPPORT         = auto()
    PROTOCOL_SWITCHING  = auto()   # confirmed mode switch available

    # Feature capabilities
    HIRES_WEIGHT        = auto()   # xBPI 0x1F — sub-mg
    PARAMETER_TABLE     = auto()   # xBPI 0x55 — size varies 70 vs 8
    CONFIG_COUNTER      = auto()   # xBPI 0xBA — cache-invalidation signal (§7)
    TEMPERATURE_SENSORS = auto()   # xBPI 0x76; count varies 1/2/3 across families
    CAL_RECORD          = auto()   # xBPI 0xB9 — last cal metadata
    INTERNAL_CAL        = auto()   # 0x28 internal adjust (MSE)
    EXTERNAL_CAL        = auto()
    ISOCAL              = auto()   # p15, status bit 0x10
    EXTENDED_OPCODES    = auto()   # 0xBC module list etc. — Cubis
    APP_MODES           = auto()   # count / density / percent — Cubis
    LEVEL_SENSOR        = auto()   # p59/p60 — Cubis
    BARGRAPH            = auto()   # xBPI 0x2F
    AUTO_OUTPUT         = auto()   # SBI autoprint (p36 auto_wo / auto_w)
    RAW_ADC             = auto()   # xBPI 0x75 (BCE)

class SafetyTier(IntEnum):
    READ_ONLY   = 0   # weight/status/identity/temperature — no state change
    STATEFUL    = 1   # tare, zero — transient state change, no EEPROM write
    PERSISTENT  = 2   # parameter write, EEPROM save — requires confirm=True
    DANGEROUS   = 3   # baud/SBN change, reset, cal init, protocol switch — requires confirm=True
```

Family is decided by the model string from opcode `0x02` (or SBI identify); capabilities are *seeded* from a family-defaults table and then confirmed or contradicted by lightweight **targeted** probes (never opcode sweeps) and by actual command attempts over the life of the session. Probe outcomes are recorded on `DeviceInfo.probe_report` so the user can see why a capability bit is or is not set.

Classification rules:

- `MSE*` and related Cubis strings → `CUBIS`
- `WZ*` / `WZA*` → `OEM_WEIGH_CELL`
- `BCE*` → `BASIC_LAB`
- unknown strings → `UNKNOWN`

### 5.1 Capability bits and command availability are priors, not contracts

The reverse-engineering sample behind this design is small — a handful of models, mostly in one firmware revision each. Family tables and capability defaults encode **what we have observed**, not **what the protocol guarantees**. The library treats them accordingly:

- `DeviceInfo.capabilities` is a bitmap of capabilities currently believed `SUPPORTED`. `DeviceInfo.probe_report` holds the full `ProbeOutcome` per capability: its `Availability` value (`UNKNOWN` / `SUPPORTED` / `UNSUPPORTED` / `INAPPLICABLE`), the source (`FAMILY_TABLE`, `TARGETED_PROBE`, `LIVE_CALL`, `USER_OVERRIDE`), when the observation happened, and a human-readable note.
- Family tables seed **priors**: "we expect CUBIS to support `EXTENDED_OPCODES`." They do not forbid attempts on other families, and they do not assert a capability before a probe or a live call confirms it.
- An `UNKNOWN` model (opcode `0x02` returns a string we have not classified) is a first-class case, not an error. It gets `family = UNKNOWN` and no seeded capabilities; every call becomes a live probe.

### 5.2 Runtime verification is the authoritative signal

The device is the source of truth, not our tables. Device responses drive the `Availability` transitions catalogued in §6.1.1 — success → `SUPPORTED`, xBPI `0x04` → `UNSUPPORTED` (sticky per session), xBPI `0x06` → `INAPPLICABLE` (retryable), and timeouts/malformed responses leave the prior state alone. Prior-based pre-I/O refusal happens **only** when the session is in strict mode (§6.1). The default is to try.

This keeps the library honest about how little we actually know, and it means adding a newly-RE'd model does not require a library release — identify it, run its commands, and the probe report accumulates truth.

## 6. Command surface — what the Balance facade offers

Grouped the way [PROTOCOL.md §7](PROTOCOL.md) already groups opcodes. Every method is a thin facade over `session.execute(COMMAND, request)`; safety tiers are enforced inside `Session.execute()` before any bytes go out.

```python
class Balance:
    # Weight reads — READ_ONLY, both protocols
    async def poll(self) -> Reading                                  # xBPI 0x1E / SBI ESC P
    async def read_net(self, *, hires: int = 0) -> Reading           # 0x1E / 0x1F
    async def read_gross(self, *, hires: int = 0) -> Reading         # 0x20
    async def read_tare_value(self) -> Reading                       # 0x22
    async def stream(self, *, rate_hz: float | None = None) -> StreamingSession

    # Tare / zero — STATEFUL, both protocols
    async def tare(self) -> None                                     # xBPI 0x14 / SBI ESC T
    async def zero(self) -> None                                     # xBPI 0x18

    # Status / identity — READ_ONLY
    async def status(self) -> BalanceStatus                          # 0x32 and/or 0x30
    async def identify(self) -> DeviceInfo                           # one-shot: model+sw+serial+capacity+increment+sbn+capabilities

    # Metrology (xBPI-only family)
    async def capacity(self, area: int = 0) -> Quantity              # 0x0C
    async def increment(self, area: int = 0) -> Quantity             # 0x0D
    async def temperature(self, sensor: int = 0) -> TemperatureReading  # 0x76 [TEMPERATURE_SENSORS]

    # Typed parameter-table accessors
    async def get_filter_mode(self) -> FilterMode
    async def set_filter_mode(self, mode: FilterMode, *, confirm: bool = False) -> None   # PERSISTENT
    async def get_display_unit(self) -> Unit
    async def set_display_unit(self, unit: Unit, *, confirm: bool = False) -> None        # PERSISTENT
    async def get_auto_zero(self) -> AutoZeroMode
    async def set_auto_zero(self, mode: AutoZeroMode, *, confirm: bool = False) -> None   # PERSISTENT
    # ... one pair per well-understood index (isoCAL, baud, parity, output mode, ...)

    # Parameter table (raw) — [PARAMETER_TABLE]
    async def read_parameter(self, index: int) -> ParameterEntry                             # READ_ONLY, 0x55
    async def write_parameter(self, index: int, value: int, *, confirm: bool = False) -> None  # PERSISTENT, 0x56

    # Calibration
    async def internal_adjust(self, *, confirm: bool = False) -> CalResult                # DANGEROUS [INTERNAL_CAL]
    async def last_cal_record(self) -> CalRecord                                          # READ_ONLY 0xB9 [CAL_RECORD]

    # EEPROM persistence
    async def save_menu(self, *, confirm: bool = False) -> None                           # PERSISTENT 0x47
    async def reload_menu(self, *, confirm: bool = False) -> None                         # PERSISTENT 0x46

    # Protocol-mode switching — DANGEROUS, gated on PROTOCOL_SWITCHING
    async def configure_protocol(self, protocol: ProtocolKind, *, confirm: bool = False) -> None

    # Raw escape hatches — bypass the Command layer; RE + advanced users
    async def raw_xbpi(self, opcode: int, args: bytes = b"", *, confirm: bool = False) -> XbpiReply
    async def raw_sbi(self, command: bytes | str, *, confirm: bool = False,
                      expect_lines: int = 1) -> list[bytes]
```

### 6.1 Gates, in order

`Session.execute()` applies gates in a specific order. **Safety** and **protocol** gates are hard; **family** and **capability** gates are soft by default and upgrade to hard only under strict mode.

1. **Safety tier** (hard):
   - `READ_ONLY` runs freely.
   - `STATEFUL` (tare, zero) runs freely — these are normal interactive operations.
   - `PERSISTENT`, `DANGEROUS` raise `SartoriusConfirmationRequiredError` unless `confirm=True`.
   - `raw_xbpi` / `raw_sbi` require `confirm=True` unless the opcode/command is on a built-in READ_ONLY safe-list (identity, status, weight reads).

2. **Protocol** (hard): if the active `ProtocolKind` has no variant defined for this command, raise `SartoriusProtocolUnsupportedError` pre-I/O. This is the only wire-level gate we can apply with certainty.

3. **Known-denied command** (hard once observed): if the session's per-command availability cache records this command's current `Availability` as `UNSUPPORTED`, raise `SartoriusUnsupportedCommandError` pre-I/O. Do not re-probe a refusal on every call. `INAPPLICABLE` does **not** block — it is retryable.

4. **Family / capability priors** (soft by default):
   - Default (`strict=False`): if the command's `family_hints` or `capability_hints` prior does not match the observed device, emit a single-shot `SartoriusCapabilityWarning` via the standard `warnings` module and **attempt the command anyway**. The device's response updates the per-command availability cache per §6.1.1.
   - `strict=True` (opt-in on `open_device(..., strict=True)` and on `Session`): refuse pre-I/O with `SartoriusCapabilityError`. Right for environments where an unexpected byte on the wire is worse than a blocked call.

5. **Execute.** On the device's response, update availability per §6.1.1 and either return the decoded result or raise the typed error.

Rationale: the current RE sample is too small to refuse commands pre-I/O based on family tables. The cost of a wrong denial (user blocked from a command their balance actually supports) is worse than the cost of a failed attempt (one round-trip, a clean typed error, and a correct capability update). `strict=True` stays available for environments where that trade-off inverts.

#### 6.1.1 Mapping device responses to availability

Not every error means "capability missing." The session maps responses to `Availability` values carefully:

| Response | Availability update | Rationale |
|---|---|---|
| Success | `SUPPORTED` | Direct confirmation. |
| xBPI `0x04` (unsupported/unknown opcode) | `UNSUPPORTED` | Device is telling us the command does not exist. Cache it. |
| xBPI `0x06` (operation not applicable) | `INAPPLICABLE` | Command exists but current state is wrong (e.g. tare during cal). Do **not** mark the capability missing. |
| xBPI `0x03` (value out of range) | unchanged | Bad argument, not a support signal. |
| xBPI `0x07` (invalid/missing args) | unchanged | Bad request, not a support signal. |
| xBPI `0x10` (index out of range) | unchanged for the command; the specific index is simply out of range. |
| xBPI `0x11` and other unknown codes | unchanged | Preserve the raw code in `ErrorContext`; classify conservatively. |
| Timeout | unchanged | Could be cabling, power, framing drift. Do not infer absence from a non-response. |
| Malformed/unparseable response | unchanged | Same reasoning as timeout. Only clear device-side refusals update availability. |

`INAPPLICABLE` is retryable: the next call attempts the command again because the state may have changed. `UNSUPPORTED` is sticky for the session and pre-I/O-refused on subsequent calls.

### 6.2 Typed accessors over the parameter table

`get_filter_mode()` wraps `read_parameter(1)` and decodes through the [PROTOCOL.md §10.1](PROTOCOL.md) mapping — the user does not memorize "filter mode is index 1." Ship one `get_X`/`set_X` pair per well-understood index (filter mode, display unit, auto-zero tracking, isoCAL mode, baud rate, parity, output mode). Typed setters validate enum values before sending and require `confirm=True`. Unknown indices remain reachable via raw `read_parameter`/`write_parameter`.

### 6.3 Config-counter cache invalidation

Session caches `capacity()`, `increment()`, `identify()`, and all typed `get_X()` results keyed on xBPI's `0xBA` counter (a one-byte register that increments on any runtime-config change — see [PROTOCOL.md §7.11](PROTOCOL.md)). On any write, on an explicit `refresh()`, and before returning a cached value when stale, Session re-reads `0xBA`; a mismatch flushes the cache. This is a genuine advantage over `alicatlib` — Sartorius publishes exactly the signal a cache needs. SBI sessions skip caching entirely (no counter available).

**Caveat.** `0xBA` does not appear to tick on every persistent-preference write we have observed. Treat it as a runtime-configuration invalidation signal, not a universal EEPROM revision. The cache stays conservative: items whose connection to `0xBA` is not proven out stay uncached or get invalidated on any explicit write, regardless of the counter.

### 6.4 Initial xBPI opcode mapping

| Command | Opcode | Notes |
|---|---|---|
| `IDENTIFY` | `0x02`, `0x07`, `0x00` | model / manufacturer / software, merged into one `DeviceInfo` |
| `POLL_NET` / `READ_NET` | `0x1E` | primary weight read |
| `READ_NET_HIRES` | `0x1F` | sub-mg, gated on `HIRES_WEIGHT` |
| `READ_GROSS` | `0x20` | |
| `READ_TARE` | `0x22` | |
| `TARE` | `0x14` | STATEFUL |
| `ZERO` | `0x18` | STATEFUL |
| `STATUS` | `0x32` / `0x30` | |
| `READ_CAPACITY` | `0x0C` | TLV-21 area arg |
| `READ_INCREMENT` | `0x0D` | TLV-21 area arg |
| `READ_TEMPERATURE` | `0x76` | TLV-21 sensor index |
| `READ_PARAMETER` | `0x55` | `PARAMETER_TABLE` |
| `WRITE_PARAMETER` | `0x56` | PERSISTENT |
| `CONFIG_COUNTER` | `0xBA` | cache-invalidation probe |
| `LAST_CAL_RECORD` | `0xB9` | `CAL_RECORD` |
| `INTERNAL_ADJUST` | `0x28` | DANGEROUS, `INTERNAL_CAL` |
| `SAVE_MENU` | `0x47` | PERSISTENT |
| `RELOAD_MENU` | `0x46` | PERSISTENT |

SBI variants bind into the same command names once the SBI command table is finalized. The public API does not change when SBI fills in.

## 7. Public dataclasses

All frozen, `slots=True`. `py.typed` ships.

```python
@dataclass(frozen=True, slots=True)
class Reading:
    value: float | None                    # None on overload/underload sentinel
    unit: Unit                             # G / KG / MG / N / ...
    sign: Sign                             # POSITIVE / NEGATIVE / ZERO / UNKNOWN
    stable: bool
    overload: bool
    underload: bool
    decimals: int | None                   # xBPI: byte[5]>>4; SBI: parsed from formatting
    sequence: int | None                   # xBPI status-block seq; None on SBI
    status_flags: Mapping[str, bool]       # bag of protocol-specific bits (isocal_due, adc_trusted, ...)
    protocol: ProtocolKind                 # which protocol produced this reading
    received_at: datetime                  # wall-clock, for logs
    monotonic_ns: int                      # for jitter analysis
    raw: bytes | str                       # original frame/line for debugging
    # Stability note: xBPI decoders should prefer the measurement-frame flag
    # bit 0x40 over status-block state bytes — it is more portable across
    # families (MSE/WZA/BCE) and does not require a separate 0x32/0x30 round-trip.
    # value is None on overload/underload sentinels, not a numeric floor/ceiling.

@dataclass(frozen=True, slots=True)
class BalanceStatus:
    stable: bool | None
    state: BalanceState                    # STABLE / UNSTABLE / OVERLOAD / UNDERLOAD / OFF / UNKNOWN
    isocal_due: bool | None                # None on SBI (no equivalent signal)
    adc_trusted: bool | None
    sequence: int | None
    raw_state: int | str | None
    raw_status: int | str | None
    raw: bytes | str

@dataclass(frozen=True, slots=True)
class DeviceInfo:
    manufacturer: str | None
    model: str
    serial: str | None
    factory_number: str | bytes | None
    software: str | None
    firmware: FirmwareVersion | None
    family: BalanceFamily
    protocol: ProtocolKind
    capacity: Quantity | None              # from 0x0C on xBPI when available
    increment: Quantity | None             # from 0x0D
    sbn: int | None                        # xBPI bus address
    serial_settings: SerialSettings
    capabilities: Capability
    probe_report: Mapping[Capability, ProbeOutcome]   # observability for absent bits

@dataclass(frozen=True, slots=True)
class Quantity:
    value: float
    unit: Unit

@dataclass(frozen=True, slots=True)
class TemperatureReading:
    sensor: int
    celsius: float
    raw: bytes | None

@dataclass(frozen=True, slots=True)
class ParameterEntry:
    index: int
    current: int
    max: int | None
    raw: bytes

@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    port: str
    serial_settings: SerialSettings
    protocol: ProtocolKind | None
    model: str | None
    evidence: Mapping[str, str]            # what was tried, what answered

@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    availability: Availability             # UNKNOWN / SUPPORTED / UNSUPPORTED / INAPPLICABLE
    source: ProbeSource                    # FAMILY_TABLE / TARGETED_PROBE / LIVE_CALL / USER_OVERRIDE
    at: datetime | None                    # when the observation happened (None for priors)
    detail: str | None                     # human-readable note ("xBPI 0x04 on 0xBC", etc.)
```

`Availability` is the *derived state* the session consults; `ProbeOutcome` is the *observation record* that produced it (what happened, when, from which source). A session's per-command availability cache stores the current `Availability` value keyed on command name; `DeviceInfo.probe_report` surfaces the full tri/quad-state record to callers for debugging.

Enums: `Unit` (all xBPI/SBI units documented or observed, plus `UNKNOWN`), `Sign`, `BalanceState`, `FilterMode`, `AutoZeroMode`, `DisplayAccuracyMode`, `TareMode`, `OutputMode`, `ProtocolKind` (named with the `Kind` suffix to avoid collision with `typing.Protocol`), `BalanceFamily`, `Capability`, `SafetyTier`, `Availability`, `ProbeSource`. Every enum that decodes protocol data includes `UNKNOWN` or otherwise preserves raw values so the library stays forward-compatible.

## 8. Transport + testing

### 8.1 Transport Protocol

Supports both binary (xBPI length-prefixed) and line-oriented (SBI) reads:

```python
class Transport(Protocol):
    label: str
    is_open: bool
    async def open(self) -> None
    async def close(self) -> None
    async def reopen(self, *, baudrate: int | None = None,
                     parity: str | None = None, stopbits: int | None = None) -> None
    async def write(self, data: bytes, *, timeout: float) -> None
    async def read_exact(self, n: int, *, timeout: float) -> bytes          # xBPI length-prefix reads
    async def read_until(self, sep: bytes, *, timeout: float) -> bytes      # SBI line reads
    async def read_available(self, *, idle_timeout: float, max_bytes: int) -> bytes  # passive sniff
    async def drain_input(self) -> None
```

Implementations: `SerialTransport` over `anyserial`, plus `FakeTransport` for tests.

### 8.2 `sartoriuslib.testing` — first-class public module

Public API (parallel to alicatlib's `testing.py`):

- `FakeTransport` — scripted request/response for unit tests.
- `parse_xbpi_fixture(text)` / `parse_sbi_fixture(text)` — fixture parsers.
- `canned_frames` — reference xBPI frames from real balances ready to drop into tests.
- `build_identify_script(...)`, `build_metrology_script(...)`,
  `build_temperature_script(...)`, `build_parameter_read_script(...)`,
  `build_parameter_write_script(...)`, `build_sbi_identify_script(...)` —
  ready-to-use scripted mappings for common session flows.

xBPI fixture format:

```
# xBPI fixture: read net weight
> 04 01 09 1e 2c
< 0b 41 48 bb a3 d7 0a 3d 30 82 45 07
```

SBI fixture format:

```
# SBI fixture: print
> ESC P
< +     0.00 g
```

Promoted captures live under [tests/fixtures/captures/](../tests/fixtures/captures/);
[`sarto-decode`](../src/sartoriuslib/cli/decode.py) understands xBPI wire
bytes and SBI lines offline.

## 9. Sync / async

Async is canonical. `sartoriuslib.sync` wraps it via `anyio.BlockingPortal`, exactly like alicatlib. Hand-written parity between the two surfaces, enforced by a CI parity test. Users of both libraries get one mental model: `await dev.poll()` works the same whether `dev` is an Alicat MFC or a Sartorius balance.

Exposed sync symbols: `Sartorius.open(...)`, `SyncBalance`, `SyncSartoriusManager`, sync recording helpers, sync sink wrappers. Sync method signatures mirror async facade signatures except for `await`.

The sync portal unwraps single-member `ExceptionGroup`s so ordinary
single-device failures can be caught as their concrete `SartoriusError`
subclass. Multi-error groups are preserved. Under manager
`ErrorPolicy.RAISE`, that means a sync caller sees one concrete exception
when exactly one balance failed and an `ExceptionGroup` when two or more
balances failed.

## 10. Streaming, acquisition, sinks

Two acquisition modes:

- **Request/response polling**: works for xBPI and SBI.
- **Device-driven output**: SBI autoprint as a streaming session.

xBPI does not continuously stream in the decoded protocol; the xBPI `stream(...)`
convenience polls at an absolute cadence. SBI can also poll in command/reply
mode, but once autoprint is detected the stream must be consumed as
`mode="autoprint"` so stale continuous output does not build up behind a
cadenced poll loop.

```python
async with record(balance_or_manager, rate_hz=10, duration=60) as stream:
    async for sample in stream:
        ...
```

`Sample` fields: device name, `Reading`, requested timestamp, received timestamp, midpoint timestamp, `monotonic_ns`, `elapsed_s`, protocol, metadata, error (if any). The recorder logs an `AcquisitionSummary` on close; `pipe(...)` returns an `AcquisitionSummary` with the number of samples handed to the sink.

**Sinks** (parallel to alicatlib): `InMemorySink`, `CsvSink`, `JsonlSink`, `SqliteSink` in the base install; `ParquetSink`, `PostgresSink` behind extras. Schemas stay compatible with `alicatlib`'s where practical. Common schema fields:

| Field | Notes |
|---|---|
| `device` | manager-assigned name |
| `requested_at` / `received_at` / `midpoint_at` | wall-clock, ISO-8601 |
| `elapsed_s` | round-trip time for this sample |
| `value` | nullable on overload/underload |
| `unit` | from `Unit` enum |
| `sign` | from `Sign` enum |
| `stable` / `overload` / `underload` | from `Reading`; rendered as `0` / `1` in the canonical row helper |
| `decimals` / `sequence` | protocol-derived counters when available |
| `protocol` | `xbpi` / `sbi` |
| `raw` | original frame/line as hex |
| `error_type` | fully qualified exception class on error, else null |
| `error_message` | str of exception, else null |

**Streaming on SBI.** Default `stream(rate_hz=...)` is absolute-cadence polling
for xBPI and for SBI command/reply mode. If SBI autoprint has already been
detected, `stream(mode="poll")` refuses and tells the user to use
`stream(mode="autoprint")`.

Autoprint (parameter `p36` = `auto_wo` / `auto_w`) is a real capability, but silently flipping it inside a plain `stream()` call is the wrong default — the user wouldn't know their `p36` value changed. Three explicit modes instead:

- `stream(mode="poll", rate_hz=...)` — default; no device-side mutation.
- `stream(mode="autoprint")` — consumes the device's *existing* autoprint output; fails if autoprint is not already enabled. No device-side mutation.
- `stream(mode="autoprint", temporary_autoprint=True, confirm=True)` — planned persistent-parameter path that will configure `p36` on entry and restore the prior value on exit. In the current release it raises `NotImplementedError`; callers should enable autoprint on the balance and consume it with `mode="autoprint"`.

Gated on `Capability.AUTO_OUTPUT`. The chosen mode is recorded on each `Sample.metadata`.

## 11. Multi-device manager

`SartoriusManager` clones `AlicatManager`:

```python
async with SartoriusManager() as mgr:
    await mgr.add("bal1", "/dev/ttyUSB0", protocol="auto")
    readings = await mgr.poll()
```

Requirements:

- Devices are keyed by caller-provided names.
- Same-port commands serialize through a shared client lock.
- Different ports run concurrently.
- Port identity is canonicalized.
- Per-port clients are ref-counted.
- Results are returned by device name.
- `ErrorPolicy.RAISE` raises an `ExceptionGroup` after all devices complete.
- `ErrorPolicy.RETURN` returns per-device result objects.
- `execute(command, requests_by_name)` for custom multi-device workflows.

Combined Alicat + Sartorius example:

```python
async with AlicatManager() as mfcs, SartoriusManager() as bals:
    await mfcs.add("mfc1", "/dev/ttyUSB0")
    await bals.add("bal1", "/dev/ttyUSB1")
    async with anyio.create_task_group() as tg:
        tg.start_soon(record, mfcs, 5.0)
        tg.start_soon(record, bals, 10.0)
```

## 12. Error hierarchy

Every library exception inherits from `SartoriusError` and carries a structured `ErrorContext`.

```
SartoriusError
├─ SartoriusConfigurationError
│  ├─ UnknownUnitError, InvalidParameterIndexError, InvalidSbnError
│  ├─ SartoriusConfirmationRequiredError        (PERSISTENT/DANGEROUS without confirm=True)
│  └─ SartoriusValidationError
├─ SartoriusTransportError
│  ├─ SartoriusTimeoutError
│  └─ SartoriusConnectionError
├─ SartoriusProtocolError
│  ├─ SartoriusFrameError                       (bad checksum, bad length, malformed TLV)
│  ├─ SartoriusParseError                       (unknown subtype or unparseable SBI line)
│  ├─ SartoriusCommandRejectedError             (xBPI subtype 0x01 / SBI refusal)
│  │  ├─ SartoriusUnsupportedCommandError       (also CapabilityError; xBPI err 0x04)
│  │  ├─ SartoriusValueOutOfRangeError          (also CapabilityError; xBPI err 0x03)
│  │  ├─ SartoriusOperationNotApplicableError   (also CapabilityError; xBPI err 0x06)
│  │  ├─ SartoriusMissingArgsError              (also CapabilityError; xBPI err 0x07)
│  │  └─ SartoriusIndexOutOfRangeError          (also CapabilityError; xBPI err 0x10)
│  └─ SartoriusProtocolUnsupportedError         (command not available on active protocol)
├─ SartoriusCapabilityError
│  ├─ recognized xBPI refusal classes listed above
│  └─ SartoriusFirmwareError
└─ SartoriusSinkError
   ├─ SartoriusSinkDependencyError
   ├─ SartoriusSinkSchemaError
   └─ SartoriusSinkWriteError
```

`ErrorContext` carries: command name, command bytes, opcode or SBI command token, raw response, protocol, port, model, family, SBN address, elapsed seconds, extra structured fields.

xBPI subtype `0x01` error codes map to typed reasons: `0x03` → value out of range, `0x04` → unsupported/unknown opcode, `0x06` → operation not applicable, `0x07` → invalid or missing args, `0x10` → index out of range.

In addition to exceptions, the library emits `SartoriusCapabilityWarning` (a `UserWarning` subclass) when it attempts a command whose priors do not match the observed device in non-strict mode (§6.1). This is a one-shot-per-command signal routed through the standard `warnings` module so callers can silence or escalate with ordinary filter rules.

## 13. CLI

**Stable CLI** (parallel to alicatlib's CLI surface):

- `sarto-read PORT [--protocol auto|xbpi|sbi]` — open, identify, print one reading.
- `sarto-discover [PORT]` — list candidate ports and probe explicitly requested serial settings; produces `DiscoveryResult` output.
- `sarto-capture PORT --rate 10 --duration 30 --out run.jsonl` — timed acquisition to any sink.
- `sarto-raw PORT --xbpi 0x1E` / `--sbi "ESC P"` — send one explicit command, dump response.
- `sarto-decode --xbpi HEX` / `--sbi LINE` — decode wire bytes offline, no hardware required.
- `sarto-configure PORT --protocol xbpi --confirm` — confirmed configuration operations (protocol switch, SBN change, baud change).

**Diagnostics CLI** (`sarto-diag` namespace) — RE tools behind a risk-visible prefix, opt-in installable:

- `sarto-diag snapshot`, `sarto-diag sweep`, `sarto-diag argfuzz`, `sarto-diag tap`, `sarto-diag stream` — the current [src/sartoriuslib/cli/](src/sartoriuslib/cli/) tools lifted forward. Not on the default install path; destructive operations require an explicit `--i-understand-this-is-destructive` flag. Never invoked from normal discovery or open.

## 14. Current Code Map

The package now lives under `src/sartoriuslib/`. Earlier migration notes from
the reverse-engineering workspace are kept only as historical context; the
current module boundaries are:

| Area | Current modules | Notes |
|---|---|---|
| Wire protocols | `protocol/base.py`, `protocol/client.py`, `protocol/detect.py`, `protocol/xbpi/*`, `protocol/sbi/*` | xBPI/SBI framing, parsers, clients, detection. |
| Semantic API | `commands/*`, `devices/*`, `manager.py`, `maintenance.py` | Command specs, `Balance`, session gates, multi-device orchestration, confirmed maintenance. |
| Acquisition | `streaming/*`, `sinks/*`, `sync/*` | Per-balance streams, recorder, async/sync sinks, sync facade. |
| Support | `registry/*`, `testing.py`, `errors.py`, `firmware.py`, `config.py`, `cli/*` | Units/parameters, fixtures, typed errors, version parsing, config, stable and diagnostic CLIs. |
| Tests and captures | `tests/unit`, `tests/integration`, `tests/fixtures/captures` | Protocol goldens, fixture regressions, fake transport, optional-backend sinks. |

## 15. Implementation slices

Build the public package in vertical slices. Each slice lands with tests; the library is usable at the end of slice 5.

0. **Package skeleton + metadata.** Create `src/sartoriuslib/__init__.py`, `py.typed`, `errors.py` stub, public re-exports, test layout. Fix the [pyproject.toml](pyproject.toml) drift (§14) — project name, package dir, script names agree. Acceptance: `pip install -e .` works; `import sartoriuslib` succeeds; `pytest` runs on an empty suite.
1. `Transport` Protocol + `SerialTransport` + `FakeTransport` + transport tests.
2. xBPI frame/framing/parser + TLV + unit tables + golden tests against current captures.
3. `XbpiProtocolClient` + `Command[Req, Resp]` base + `XbpiVariant` + `Session` with I/O lock, safety gates, and availability cache (§6.1.1).
4. `open_device(...)`, `Balance.poll()` / `Balance.tare()` / `Balance.zero()` / `Balance.raw_xbpi()`, `identify()`, fake-transport end-to-end tests.
5. Public error hierarchy + `ErrorContext` + safety-gate proofs (PERSISTENT/DANGEROUS reject before any bytes are written unless `confirm=True`). **— α milestone: usable xBPI driver.**
6. Core xBPI commands: metrology (`capacity`, `increment`), `status`, `temperature`, parameter read/write (raw), `last_cal_record`, `save_menu`/`reload_menu`, config-counter cache invalidation with the §6.3 caveat tested.
7. Typed parameter accessors (`get_filter_mode`, `get_display_unit`, `get_auto_zero`, …) + typed enums in `registry/`.
8. Sync facade (`sartoriuslib.sync.Sartorius`, `SyncSartoriusManager`) + parity tests.
9. `SartoriusManager` + `record(...)` + `Sample` / `AcquisitionSummary`, absolute-cadence scheduler.
10. `InMemorySink`, `CsvSink`, `JsonlSink`, `SqliteSink` + schema tests. **— β milestone: full async+sync xBPI acquisition.**
11. Conservative `protocol=AUTO` + `sarto-discover` + `DiscoveryResult`.
12. SBI framing + parser + SBI command variants for the common command catalog + semantic-parity tests (xBPI and SBI fixtures produce equivalent `Reading`).
13. SBI polling `stream(mode="poll")` + `stream(mode="autoprint")` (consume-only) + `stream(mode="autoprint", temporary_autoprint=True, confirm=True)` with prior-restore on exit. `Balance.configure_protocol(...)` + `sarto-configure` CLI. **— 1.0 milestone: stable release.**
14. Extras: `ParquetSink`, `PostgresSink`, combined-harness examples with `alicatlib`, migration notes.

Each slice is a merge-ready increment, not a branch.

### 15.1 Required test coverage (per slice)

- xBPI checksum and length validation.
- xBPI command encoding against captured goldens.
- xBPI TLV parsing.
- xBPI error mapping (all subtype `0x01` codes).
- xBPI short and long measurement/status decoding (unit, sign, stability, overload, underload, decimals).
- SBI command/response and autoprint parsing.
- Semantic command parity: xBPI and SBI fixtures produce equivalent `Reading` objects for equivalent states.
- Fake transport session tests.
- Discovery tests for explicit xBPI, explicit SBI, passive SBI autoprint, and failed auto-detection.
- Manager concurrency and same-port serialization.
- Sink schema conformance.
- Sync/async facade parity.
- Safety-gate proofs (no bytes written on `PERSISTENT`/`DANGEROUS` without `confirm=True`).
- Config-counter cache invalidation correctness.
- Runtime capability verification: `UNKNOWN`-family device with no seeded priors runs a command, receives `0x04`, and `probe_report` flips the bit to DENIED without raising at the transport layer.
- Strict mode: `open_device(..., strict=True)` refuses a command with a mismatched capability prior pre-I/O; default mode emits `SartoriusCapabilityWarning` and attempts it.

## 16. Open design questions

1. **Limited RE sample → soft gating is the right default, but how soft?** The family tables and capability defaults in §5 reflect only the handful of models we have captures for (largely one firmware revision each). The design in §5.1–§5.2 and §6.1 leans on *runtime verification* — attempt, observe, update — rather than pre-I/O refusal from tables we cannot yet trust. Three sub-questions sit under this:
   - Should `strict=False` be the public default, or should we ship `strict=True` by default and let advanced users opt down? Lean: `strict=False` default, because a wrong denial costs more than a failed attempt when the table is unreliable.
   - Should capability priors *ever* refuse pre-I/O in non-strict mode? Current answer: no — only already-observed denials on *this session* short-circuit. Revisit after we accumulate enough field observations to trust the tables.
   - Should `probe_report` be persistable across sessions (e.g. cached per model+firmware on disk) so a once-observed denial sticks? Attractive but risks baking in wrong observations from a single quirky unit. Defer until we have multi-unit data.
2. **Shared sinks / sample schema.** Extract `Sample` / `Sink` to a common `labsink` dep that both `sartoriuslib` and `alicatlib` depend on, or duplicate the schema verbatim? Copy-paste ships faster; a shared package delivers on the combined-harness promise. Decide **before first release** — sink API is painful to change after publication. Lean: duplicate on day one, extract when a second consumer (beyond alicat+sartorius) appears.
3. **Auto-probe baud/parity.** All three families ship from the factory in SBI; xBPI is reached by a front-panel menu switch. Per-family default framing once in xBPI is MSE 19200-8-O-1, BCE 9600-8-O-1, WZA 1200-8-O-1; in SBI WZA defaults to autoprint at 1200-7-O-1, MSE/BCE default to command/reply at the same baud as their xBPI side. Should `protocol=AUTO` with unspecified serial settings try a narrow sweep, or require the user to supply serial params? Lean: the user supplies serial params; `AUTO` only decides *which protocol* at those params. Wider sweeps live in `sarto-discover`.
4. **Registry as build-time artifact.** Alicatlib builds `registry/data/codes.json` at package-build time. Earned here for balance units + parameter-enum tables, or inline the tables (< 100 entries total)? Lean: inline; revisit if the tables grow.
5. **Firmware-version gating.** Alicatlib gates on firmware families (V1_V7, V8_V9, V10, GP). Sartorius opcode availability varies by family (MSE/WZA/BCE) more than by firmware revision within a family as far as we know, but that observation sits on top of a thin sample (Q1). Treat firmware as another *prior* feeding the same soft-gate machinery; revisit once we have captures from multiple firmware revisions of the same family.
6. **Maintenance module vs Balance method.** `configure_protocol(...)` is exposed both on the `Balance` facade (for discoverability after open) and via `sartoriuslib.maintenance.switch_protocol(port, target, *, confirm=True)` (for one-shot port-level reconfigure without a full session). Same goes for `set_baud_rate`, `write_sbn_address`. Confirm the dual path stays consistent in the first release and collapse to one surface later if usage converges.
7. **Family convenience helpers.** The single-class decision rules out subclassing. If Cubis-only convenience accrues (app modes, level sensor, extended modules), does it live as free functions in `sartoriuslib.families.cubis`, or as a narrow `CubisExtensions(balance)` wrapper? Defer until real Cubis use cases show up.
8. **`SartoriusManager` vs `BalanceManager`.** Two defensible names. `SartoriusManager` mirrors `AlicatManager` exactly (consistent call-site shape across both libraries). `BalanceManager` reads more naturally because the `sartoriuslib` import already namespaces the vendor. Lean: ship `SartoriusManager` as the primary name for cross-library symmetry and expose `BalanceManager` as an alias. Same question applies to `sync.Sartorius` vs `sync.Balance`.

## 17. Assumptions and defaults

- This is a full public library, not a testing-only helper.
- The current repository is a protocol-validation workspace, not the final product repository.
- `open_device(...)` is the primary async entry point for cross-library uniformity with `alicatlib`.
- `open_balance(...)` is a friendly alias.
- Async is canonical; sync wraps async.
- xBPI is implemented first; SBI architecture is present from day one and filled in when the SBI command table is complete.
- Normal discovery is conservative and never runs opcode sweeps, fuzzing, broad parameter probes, or mode changes.
- Protocol switching is explicit, confirmed, and never automatic during `open_device`.
- Sinks and manager are in public scope because the library mirrors `alicatlib` for real acquisition workflows.
- One `Balance` class. Capability flags dispatch; family is a discriminator, not a class identity.
- Family tables and capability defaults are **priors from a small RE sample**, not a contract. The library verifies at runtime: attempt, observe device response, update `probe_report`. Pre-I/O refusal from priors only happens under `strict=True` or after an observed denial on the current session.

---

**First vertical slice** (slices 1–4 above) delivers: transport + xBPI frame + xBPI ProtocolClient + Session + Command dispatch + `Balance.poll()` / `tare()` / `zero()` / `raw_xbpi()` + `FakeTransport` + fixture-based tests. That exercises every layer end-to-end. SBI, family probing, typed parameter accessors, streaming, sinks, manager, and the sync facade layer on after without breaking the public surface.
