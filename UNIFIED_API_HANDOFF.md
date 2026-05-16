# sartoriuslib — Unified Device-Library API Handoff

**Status:** prerelease, no backward compatibility required. Old names are removed outright; no deprecation shims, no re-exports.

**Sibling libraries adopting the same spec:** `alicatlib`, `watlowlib`, `nidaqlib`.
You're the agent working on **sartoriuslib**; you don't need to coordinate with the others, but the spec below is being applied to all four libraries in parallel so they look and behave like siblings. Diverging from the spec means consumers (capa, future SDKs) have to maintain four translation tables.

---

## 1. Background

The four libraries listed above are owned by the same author and consumed by `capa` (a cone-calorimeter-class lab instrument app) and other downstream tools. They already share a remarkable amount of structural symmetry — every lib has `manager.py`, `devices/` (or equivalent), `transport/`, `protocol/`, `streaming/`, `sinks/`, `sync/`, `errors.py`, `config.py`, `record()`, `sample_to_row()`. We're closing the remaining inconsistencies so a consumer that knows one library knows them with minimal friction.

**Honest scope of unification.** Three things genuinely differ across the four libs and we will not paper over them:

1. **The streaming record's shape.** alicat and sartorius emit one `Sample` per device per tick, wrapping a wide `Reading`. watlow emits N flat `Sample`s per tick (one per `(device, parameter, instance)`). nidaq emits `DaqReading` or `DaqBlock` directly with no `Sample` wrapper. The §C timestamp contract is a floor that applies to *whatever the emitted record is*, regardless of its outer shape.
2. **The recorder's poll protocol.** alicat/sartorius/nidaq recorders consume `poll(names) -> Mapping[str, DeviceResult[<reading>]]`. watlow's recorder consumes `poll_many(parameters, *, names, instances) -> Sequence[Sample]` — parameter-fanout is fundamental to watlow's design. Same `PollSourceAdapter` class name across libs, different method signatures.
3. **Per-record-type row conversion.** alicat/watlow/sartorius export `sample_to_row`. nidaq exports `reading_to_row` and `block_to_rows`. Consumers dispatch on type, not on a single uniform function name.

What *is* unified: the timestamp contract on every emitted record, the discovery-result base shape, the error-context base fields and the `address` accessor, the `Recording(stream, summary)` context-manager wrapper, the `open_device(...)` semantics, the top-level export discipline, and the `to_pint` helper.

**Sartorius is the lib with the most-load-bearing transient-error work.** Capa is the primary consumer; its adapter layer at `capa/src/capa/devices/sartorius.py` papers over several gaps that should live in this library instead. **Most notably:** capa string-matches `"frame too short"` and `"got 0 bytes"` on exception messages to detect USB cold-open races and retry — that pattern needs to be replaced with a typed `SartoriusTransientTransportError` raised by the transport layer. This is the only lib of the four where the transient-error work is justified by current evidence (alicat and watlow defer this work; nidaq follows separately from documented NI codes).

---

## 2. Unified API Spec (v1)

This section is identical across all four library handoffs. **Apply every item below unless explicitly marked "N/A for sartoriuslib".**

### A. Canonical names

| Concern | Canonical name | Old aliases to **delete / rename** |
| --- | --- | --- |
| Factory | `open_device(...)` | `open_balance` — delete |
| Top-level manager | `SartoriusManager` | `BalanceManager` — delete |
| Discovery | `find_devices(...)` | — |
| Discovery result | `DiscoveryResult` | keep/normalize the current per-probe `DiscoveryResult`; rename the per-port summary `FindResult` to `DiscoverySummary` if it stays public |
| Per-tick read result | `Reading` | (already canonical) |
| Streaming record | `Sample` (frozen dataclass; wraps `Reading`) | — |
| Recording wrapper | `Recording[T]` (see §M) | — |
| Result wrapper | `DeviceResult[T]` with `success()`/`failure()` factories (see §E.0) | — |
| Error context | `ErrorContext` (unprefixed; namespace disambiguates) | — |
| PollSource wrapper | `PollSourceAdapter` (signature is per-lib; see §E) | — |

No deprecation aliases. If a name changes, the old name is gone.

**Note on cross-lib divergence (mirrors §1):** sartorius `Sample` wraps a `Reading` like alicat. watlow's `Sample` is flat per `(device, parameter, instance)`. nidaq emits `DaqReading`/`DaqBlock` directly. Same names where the concepts match; explicit divergence where they don't.

### B. `DiscoveryResult` shape

```python
@dataclass(frozen=True)
class DiscoveryResult:
    ok: bool
    port: str
    address: str | int | None       # SBN address for xBPI, None for SBI
    baudrate: int | None
    protocol: ProtocolKind | None   # SBI, XBPI, or None when ok=False before detection
    device_info: DeviceInfo | None  # populated only when ok=True
    error: SartoriusError | None    # populated only when ok=False
    elapsed_s: float
```

Subclass for per-library extras if needed, but never rename a base field. `find_devices()` returns `list[DiscoveryResult]` and **never raises**. `DiscoveryResult` means a concrete candidate/probe row, not an aggregate sweep summary; if a grouped per-port answer is useful, expose it as `DiscoverySummary`.

**Sartorius today has both `DiscoveryResult` and `FindResult` — these serve different layers and should not be collapsed blindly.** The current `DiscoveryResult` is a single per-port-per-baud probe attempt and is closest to the unified shape. The current `FindResult` is a per-port summary across baud rates. Keep the canonical `DiscoveryResult` at probe-row granularity; rename the summary type to `DiscoverySummary` if it remains public.

### C. `Sample` timestamp contract

Every `Sample` carries **at least** these three timestamp fields:

- `t_mono_ns: int` — canonical join key (monotonic nanoseconds since OS boot)
- `t_utc: datetime` — wall-clock acquisition instant (UTC, tz-aware)
- `t_midpoint_mono_ns: int | None` — optional integration-window midpoint; for single polled/autoprint samples this is usually `None`

Rename legacy `monotonic_ns` to `t_mono_ns`, but do **not** blindly rename `received_at` to `t_utc`. For request/response polling, `t_mono_ns` and `t_utc` should represent the best acquisition estimate: the midpoint between request dispatch and response receipt. **Keep** `requested_at`, `received_at`, and `latency_s` as I/O provenance. **Keep** `Sample.metadata` — autoprint vs poll mode annotation lives there and is real data. The three-field contract is a floor, not a ceiling.

### D. Top-level conversion helpers

- `sartoriuslib.sample_to_row(sample) -> dict[str, ScalarValue]`

`sartoriuslib.sinks.sample_to_row` may remain as an internal alias, but the **documented path** is the top-level export.

### E. `PollSourceAdapter`

```python
class PollSourceAdapter:
    """Wrap one Balance as sartoriuslib.record()'s PollSource.

    Same class name across all four libs; the method signature follows
    each library's recorder Protocol — explicit, documented divergence
    (see §1). For sartorius, the recorder consumes
    Mapping[str, DeviceResult[Reading]]."""
    def __init__(self, name: str, device: Balance) -> None: ...
    async def poll(
        self, names: Iterable[str] | None = None
    ) -> Mapping[str, DeviceResult[Reading]]: ...
```

Exported from `sartoriuslib.streaming.PollSourceAdapter` AND `sartoriuslib` top level. Capa's adapter at `capa/src/capa/devices/sartorius.py:186-209` rebuilds this shim — delete it once this lands.

**Autoprint transparency confirmed:** `Balance.poll()` already branches on `session.sbi_autoprint_active` internally (balance.py:276-277) and reads from the unsolicited stream when appropriate. The adapter doesn't need to know — callers see uniform behavior.

#### E.0 `DeviceResult` factory prerequisite

Every PollSourceAdapter code path below assumes `DeviceResult.success(value)` and `DeviceResult.failure(error)` exist. They don't today — `DeviceResult.ok` is a boolean property and construction goes through `DeviceResult(value=v, error=None)`. Land the factories **before** the adapter:

```python
@dataclass(frozen=True, slots=True)
class DeviceResult[T]:
    value: T | None
    error: SartoriusError | None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def success(cls, value: T) -> Self:
        return cls(value=value, error=None)

    @classmethod
    def failure(cls, error: SartoriusError) -> Self:
        return cls(value=None, error=error)
```

The existing keyword-construction path stays valid; the factories are additive ergonomics that the adapter code below uses.

### F. `SartoriusTransientTransportError`

```python
class SartoriusTransientTransportError(SartoriusTransportError):
    """Transport-layer hiccup safe to retry without reopening the port."""
```

**This is the big win for sartorius.** Today capa detects cold-open USB races by string-matching `"frame too short"` and `"got 0 bytes"` on exception messages (`capa/src/capa/devices/sartorius.py:92-109`). That's fragile and will break the next time an exception message changes.

**Raise sites — the cold-open race spans two layers, so the new type is raised from both:**

1. `transport/serial.py` — when `read_exact` / `read_available` returns 0 bytes while bytes were expected (this is the "got 0 bytes" case; pure transport-layer underrun).
2. `protocol/xbpi/framing.py:110` — where `SartoriusFrameError("frame too short: got N bytes ...")` is raised today. Either replace the `SartoriusFrameError` raise with `SartoriusTransientTransportError` *when this happens inside the cold-open window* (first N frames after open), or have the framing layer always raise the transient on underrun and let the session's recoverable-retry path handle it.

`SartoriusFrameError` for *non-underrun* corruption (e.g., bad CRC on a fully-formed frame) stays as-is — that's not transient. The discriminator is "frame had < MIN_FRAME_SIZE bytes" vs "frame had garbage in the bytes."

Existing fatal errors (`SartoriusTimeoutError`, `SartoriusConnectionError`) stay separate.

### G. `ErrorContext` base fields

Class name is `ErrorContext` (**unprefixed** — the `sartoriuslib.` import path already disambiguates from sibling libs). Must expose at minimum:

```python
port: str | None
address: str | int | None  # SBN address; None for SBI (via @property on sbn_address)
command_name: str | None
protocol: ProtocolKind | None
elapsed_s: float | None
extra: Mapping[str, Any]
```

Plus sartorius extras (`opcode`, `model`, `family`). `with_context()` enriches non-destructively.

**`address` strategy:** Keep the existing `sbn_address` as the native frozen-dataclass field — it carries semantic meaning at the xBPI protocol layer. Expose `address` as a `@property` returning `sbn_address`. Consumers read `ctx.address` uniformly; the sartorius-specific field stays where it is. No dataclass migration.

### H. `Device.snapshot()`

```python
@dataclass(frozen=True)
class DeviceSnapshot:
    name: str
    model: str | None
    firmware: str | None
    serial: str | None
    connected: bool
    last_error: ErrorContext | None
    recoverable_error_count: int
    captured_at: datetime  # UTC, tz-aware

async def snapshot(self) -> DeviceSnapshot: ...
```

**No I/O** — built from cached `DeviceInfo` + session counters. Subclass `SartoriusDeviceSnapshot` with extras: `family: BalanceFamily | None`, `capabilities: frozenset[Capability]`, `protocol: ProtocolKind`, `mode: BalanceMode | None` (current mode if known).

### I. Streaming rate — exposed on `Recording`, not on `Balance`

The original spec proposed a `Balance.expected_rate_hz` / `Session.expected_rate_hz` property set by `record()` / `stream()` and cleared on exit. Reversed: that's reverse-coupling (the recorder mutating the balance just so a third party can read back what the recorder already knows).

The configured rate lives on `Recording` (see §M): `recording.rate_hz` returns the rate the recorder is running at, set at `record()` entry. Queue-sizing consumers get the value from the same context manager that owns the schedule.

**SBI autoprint observed rate.** Autoprint isn't a configured-rate concept — the balance emits at its own cadence. If consumers need to know the observed inter-frame rate, expose it as `Recording.observed_rate_hz: float | None` (rolling mean over the last 10 frames; `None` until the window fills). This lives on `Recording`, not on `Balance`, for the same Demeter reasons.

### J. `Session.recoverable_error_count`

Public `int` incremented every time the session swallows-and-retries an error. Reset on `open()`. Includes:
- `SartoriusTransientTransportError` retries (cold-open races)
- Any retry the result cache / counter-pinning logic performs internally

### K. `sartoriuslib.units.to_pint(unit) -> str | None`

```python
def to_pint(unit: Unit | str | None) -> str | None:
    """Return a pint-compatible unit string."""
```

Sartorius has `Quantity` (a simple 2-field frozen dataclass: `value: float`, `unit: Unit`) and a `Unit` enum. Map every `Unit` enum value to a pint string ("g", "kg", "mg", "lb", "oz", "ct", etc.). `pint` is **not** added as a runtime dep — `to_pint` returns plain strings.

**Lossy by design** — same rule as siblings. Units pint doesn't model (e.g., exotic troy/Hong Kong units) return `None`; don't try to encode them out-of-band.

**Don't add `Quantity.to_pint()` as a method.** The other libs don't have a quantity wrapper, and adding a method here breaks the symmetric "free function in `<lib>.units`" surface. Free function only.

### L. `open_device(...)` always returns an opened, ready-to-use device

`open_device()` is async, opens the transport, runs detection (if `protocol=None`), runs `identify()` (unless `identify=False`), returns a `Balance` ready to call `poll()` on.

**Async context-manager convention (uniform across all four libs):**
- `device = await open_device(...)` — transport already open, use directly
- `async with await open_device(...) as device:` — `__aenter__` is a no-op `return self`; `__aexit__` calls `close()`

`__aenter__` is **never** the place transport open happens. `open_device()` is the single open path. Cold-open transients are swallowed inside `open_device()` (see §3.2); post-open occurrences surface to callers as `SartoriusTransientTransportError`.

### M. Recording context contract

Unify the recorder context-manager shape across the four libraries. Prefer a small object over tuple unpacking:

```python
@dataclass
class Recording(Generic[T]):
    stream: AsyncIterator[T]
    summary: AcquisitionSummary  # mutable; see contract below
    rate_hz: float               # configured rate the recorder is running at
    observed_rate_hz: float | None = None  # sartorius-specific: SBI autoprint rolling rate
```

Then `async with record(...) as recording:` works the same everywhere, and consumers use `recording.stream`, `recording.summary`, and `recording.rate_hz`.

**Payload type per lib (the `T` parameter is genuinely lib-specific):**

| Lib | `Recording[T]` |
| --- | --- |
| alicatlib | `Recording[Mapping[str, Sample]]` |
| sartoriuslib | `Recording[Mapping[str, Sample]]` |
| watlowlib | `Recording[Sequence[Sample]]` |
| nidaqlib | `Recording[DaqReading]` (polled), `Recording[DaqBlock]` (block) |

**`AcquisitionSummary` mutability contract.** The summary is a **mutable** dataclass owned by the recorder. The recorder is the *only* writer; consumers treat it as read-only. Counters update in place during the run so progress polling (TUIs, dashboards) works without a separate API. `finished_at` is `None` while running and set on context-manager exit.

Each lib keeps its own `AcquisitionSummary` field set — the wrapper unifies, the contents stay lib-specific. **The three serial libs change `AcquisitionSummary` from frozen to mutable** (nidaqlib already is); see §3.x.

### N. Naming & style

- Public exception classes prefix with `Sartorius`
- Enum members SHOUTING_SNAKE_CASE
- Async public APIs that own a resource implement `__aenter__` / `__aexit__`
- No `_foo` imports across consumers — capa today imports only public names; keep it that way

### O. Test surface

`sartoriuslib.testing` should ship:
- `SartoriusFakeDevice` for downstream test suites
- `SartoriusFakeTransport` (already exists)

(Dropped the `make_sample(...)` builder — `Sample` is already construction-friendly.)

---

## 3. sartoriuslib-specific work plan

Files referenced are relative to `src/sartoriuslib/`. Line numbers are approximate (against current `main`).

### 3.1 Delete `open_balance` and `BalanceManager` aliases

Confirm only `open_device` and `SartoriusManager` remain in `__init__.py`. The `BalanceManager` alias must go.

### 3.2 Replace cold-open string-match retry with `SartoriusTransientTransportError`

**Highest-value item for downstream consumers.**

The cold-open race surfaces in **two places** today, both of which must raise the new transient:

1. `transport/serial.py` — when `read_exact` / `read_available` returns 0 bytes while bytes were expected (currently raises a generic `SartoriusTransportError` or similar). Change the raise site to `SartoriusTransientTransportError`.
2. `protocol/xbpi/framing.py:110` — currently raises `SartoriusFrameError("frame too short: got N bytes (min M)")`. Replace **the underrun case** (raw shorter than `MIN_FRAME_SIZE`) with `SartoriusTransientTransportError`. Keep `SartoriusFrameError` for genuine corruption (e.g., bad framing bytes inside a fully-sized frame) — that's not transient.

(The "got 0 bytes" string in capa's match was always pointing at the framing/transport boundary, not just transport.)

`transport/__init__.py`:

```python
class SartoriusTransientTransportError(SartoriusTransportError):
    """Transient transport-layer hiccup that is safe to retry without reopening."""
```

Document the retry contract: callers receiving this exception may retry the same operation up to N times without reopening. After N consecutive transients, escalate to `SartoriusConnectionError`.

`open_device()` itself swallows up to 3 transients on first read with 50ms backoff — cold-open is an open-time problem and consumers shouldn't have to know. The typed error still surfaces for any *post*-open occurrence so callers retain control there.

### 3.3 Normalize discovery types without losing granularity

The two existing types serve different layers and shouldn't be merged blindly:

- **Current `FindResult`** (line 156) — per-port sweep summary (one final answer per port across baud rates). This is the consumer-facing result of `find_devices()`.
- **Current `DiscoveryResult`** (line 60) — per-port-per-baud probe attempt; carries pending lines and autoprint state from the low-level `discover_port()`. Used internally by the sweep.

**Plan:**

1. Keep the per-probe type as canonical `DiscoveryResult` because it matches the cross-lib "one candidate row per attempt" contract. Normalize the base fields to §B.
2. **Subclass `SartoriusDiscoveryResult(DiscoveryResult)` for sartorius-native fields** that the §B base shape doesn't accommodate: `parity`, `stopbits`, `autoprint_active`, `pending_lines`. The spec §B explicitly permits subclassing for per-library extras — use it here. Do not drop these fields; they carry real per-probe metadata that downstream consumers (and reopen-with-autoprint flows) depend on.
3. Rename the current `FindResult` summary to `DiscoverySummary`.
4. Make `find_devices()` return `list[SartoriusDiscoveryResult]` (which is-a `list[DiscoveryResult]` to satisfy the cross-lib contract); provide `summarize_discovery(results) -> list[DiscoverySummary]` as a convenience.
5. Update all internal call sites in `devices/discovery.py` and `protocol/detect.py`.

Concretely:

```python
@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    ok: bool
    port: str
    address: str | int | None
    baudrate: int | None
    protocol: ProtocolKind | None
    device_info: DeviceInfo | None
    error: SartoriusError | None
    elapsed_s: float

@dataclass(frozen=True, slots=True)
class SartoriusDiscoveryResult(DiscoveryResult):
    parity: str
    stopbits: int
    autoprint_active: bool = False
    pending_lines: tuple[bytes, ...] = ()
```

### 3.4 `Sample` timestamp fields

`streaming/sample.py`: rename per §C. Update `streaming/recorder.py`, `streaming/stream_session.py`, `sinks/_schema.py`, and tests.

### 3.5 Top-level `sample_to_row`

Add to `__init__.py`:

```python
from sartoriuslib.sinks.base import sample_to_row
```

### 3.6 `DeviceResult` factories (precondition for §3.7)

`manager.py` — add `success()`/`failure()` factories per §E.0. The keyword-construction path stays valid; the factories are additive ergonomics.

### 3.7 `PollSourceAdapter`

New helper in `streaming/__init__.py`:

```python
class PollSourceAdapter:
    def __init__(self, name: str, device: Balance) -> None:
        self._name = name
        self._device = device

    async def poll(self, names=None):
        if names is not None and self._name not in set(names):
            return {}
        try:
            reading = await self._device.poll()
            return {self._name: DeviceResult.success(reading)}
        except SartoriusError as exc:
            return {self._name: DeviceResult.failure(exc)}
```

SBI autoprint is handled transparently inside `Balance.poll()` (it branches on `session.sbi_autoprint_active` at balance.py:276-277 and reads from the unsolicited stream when appropriate). The adapter therefore doesn't need to know about autoprint — it just calls `poll()`.

Export from `sartoriuslib.streaming.PollSourceAdapter` and `sartoriuslib` top level.

### 3.8 `Balance.snapshot()`

`devices/balance.py`:

```python
@dataclass(frozen=True)
class SartoriusDeviceSnapshot(DeviceSnapshot):
    family: BalanceFamily | None
    capabilities: frozenset[Capability]
    protocol: ProtocolKind
    mode: BalanceMode | None

class Balance:
    async def snapshot(self) -> SartoriusDeviceSnapshot: ...
```

**No I/O on `snapshot()`** — built from cached `DeviceInfo` + session counters. The family classification at `identify()` time is exposed rather than recomputed.

**The `expected_rate_hz` balance property is dropped** (see §I). The configured recorder rate lives on `Recording.rate_hz`; the SBI-autoprint observed rate lives on `Recording.observed_rate_hz` (rolling mean over the last 10 frames, `None` until the window fills). Consumers query the active recording, not the balance.

### 3.9 `Session.recoverable_error_count`

`devices/session.py`: add `recoverable_error_count: int = 0`, increment wherever `Session.execute()` retries a transient or counter-pinning conflict (result cache flushes that retry transparently).

### 3.10 `AcquisitionSummary` becomes mutable + `Recording` wrapper

`streaming/recorder.py`:

1. Change `@dataclass(frozen=True, slots=True)` on `AcquisitionSummary` → `@dataclass(slots=True)`. Set defaults via `field(default_factory=...)` / `field(default=...)` so the recorder can construct an in-progress instance.
2. The recorder updates counters in place during the run.
3. Set `finished_at` on context-manager exit.
4. Introduce `Recording[T]` (see §M) and change the `record()` yield to `Recording(stream=..., summary=..., rate_hz=rate_hz, observed_rate_hz=...)`.

Document the contract per §M: recorder is sole writer; consumers treat as read-only; final state is the value after `__aexit__`.

For SBI autoprint, the recorder maintains a 10-sample rolling buffer of inter-frame intervals and updates `Recording.observed_rate_hz` in place. Returns `None` until 10 frames have been observed.

### 3.11 `sartoriuslib.units.to_pint`

New file `src/sartoriuslib/units.py`. Map every `Unit` enum value to a pint string. Sartorius units include `g`, `kg`, `mg`, `ct`, `oz`, `lb`, `dwt`, `tlh`, etc. — preserve troy/Hong Kong/etc. exotic units by mapping to pint's canonical names where they exist, or returning `None` for things pint doesn't know.

```python
_SARTORIUS_UNIT_TO_PINT: dict[Unit, str] = {
    Unit.G:  "g",
    Unit.KG: "kg",
    Unit.MG: "mg",
    Unit.CT: "carat",
    Unit.OZ: "oz",
    Unit.LB: "lb",
    # …
}

def to_pint(unit: Unit | str | None) -> str | None:
    if unit is None:
        return None
    if isinstance(unit, str):
        try:
            unit = Unit(unit)
        except ValueError:
            return None
    return _SARTORIUS_UNIT_TO_PINT.get(unit)
```

Export `from sartoriuslib.units import to_pint` at top level.

**Do not add `Quantity.to_pint()` method** — keeps the cross-lib surface uniform (free function only, in `<lib>.units`).

### 3.12 `ErrorContext` base fields

`errors.py` — class name stays **`ErrorContext`** (unprefixed; the `sartoriuslib.` namespace already disambiguates). `command_name`, `opcode`, `port`, `model`, `family`, `protocol`, `sbn_address`, `elapsed_s`, `extra` already exist on the dataclass. **Don't rename `sbn_address`** — it carries semantic meaning at the xBPI protocol layer. Add `address` as a `@property` returning `sbn_address`:

```python
@property
def address(self) -> int | None:
    return self.sbn_address
```

Consumers read `ctx.address` uniformly across libs; sartorius-internal code keeps using `sbn_address`.

### 3.13 Surface family-classification result on snapshot

`SartoriusDeviceSnapshot.family` should carry the family the session classified at `identify()` time. The classification today is brittle (model-prefix lookup) — that's fine, just expose the result rather than recomputing per-call.

### 3.14 Sync facade tracks

`sync/__init__.py` re-exports through `SyncPortal`. Every API addition above needs a sync mirror (`SyncBalance.snapshot()`, etc.). Wrap through the portal — don't duplicate logic.

(`SyncBalance.expected_rate_hz` is **not** added — see §I. If a sync caller needs the recorder rate, it reads `Recording.rate_hz` from the sync record-recording wrapper.)

### 3.15 Logging — public hook (nice to have)

The library currently uses a private `_logging` module. Consumers have no public hook to adjust log levels at runtime. Consider exposing a public `sartoriuslib.logging` module with `get_logger(name)` and a `configure_logging(level)` helper. Low priority but cheap.

---

## 4. Breaking changes (explicit list)

1. `open_balance` removed → use `open_device`
2. `BalanceManager` alias removed → use `SartoriusManager`
3. Discovery types normalized: per-probe `DiscoveryResult` (sartorius-extras live on `SartoriusDiscoveryResult` subclass: `parity`, `stopbits`, `autoprint_active`, `pending_lines`) is the canonical result returned by `find_devices()`; the old per-port `FindResult` summary becomes `DiscoverySummary` if retained.
4. `Sample.monotonic_ns` renamed to `t_mono_ns`; `t_utc` added as acquisition-time wall clock; `received_at` stays as I/O provenance; `metadata` retained
5. `ErrorContext.sbn_address` stays as a native field; `ErrorContext.address` added as `@property` returning `sbn_address`
6. Cold-open retries now surface as `SartoriusTransientTransportError` — callers retrying via string-match get to throw that code away. `SartoriusFrameError` underrun cases (`raw < MIN_FRAME_SIZE`) reclassify to the new transient.
7. `record()` context managers yield `Recording(stream, summary, rate_hz, observed_rate_hz)` instead of a bare stream
8. `AcquisitionSummary` becomes mutable (was frozen); the recorder is the sole writer, consumers treat as read-only
9. `DeviceResult` grows `success()` / `failure()` classmethod factories (additive — keyword construction still works)

Capa is the primary external consumer; it will get a coordinated update.

**Not in this scope** (changes from the original spec proposal):
- `Balance.expected_rate_hz` is **dropped** (§I). The configured rate lives on `Recording.rate_hz`; SBI autoprint observed rate lives on `Recording.observed_rate_hz`.

---

## 5. Out of scope (do **not** do)

- Don't change the SBI vs xBPI detection logic (`protocol/detect.py`) — it's conservative and correct
- Don't change the result-cache / counter-pinning semantics — design §6.3 documents this carefully
- Don't change the autoprint-mode semantics — the special-case where `Balance.poll()` reads from the unsolicited stream rather than commanding is intentional
- Don't change family classification rules — domain truth
- Don't add calibration / authorization / capa-specific record types — consumer concerns
- Don't pull `pint` in as a runtime dep — `to_pint` returns strings
- Don't make availability cache or result cache "less sticky" — they exist for good reasons

---

## 6. Acceptance criteria

- [ ] All §A canonical names in place; `open_balance` and `BalanceManager` deleted
- [ ] `DiscoveryResult` is the per-probe result base type matching §B; `SartoriusDiscoveryResult` subclass adds `parity`, `stopbits`, `autoprint_active`, `pending_lines`; `find_devices()` returns `list[SartoriusDiscoveryResult]`; per-port summaries use `DiscoverySummary` if retained
- [ ] `Sample` exposes the §C timestamp fields (`t_mono_ns`, `t_utc`, optional `t_midpoint_mono_ns`) while retaining `requested_at`/`received_at`/`latency_s`/`metadata`
- [ ] `sartoriuslib.sample_to_row` callable at top level
- [ ] `DeviceResult.success(value)` / `DeviceResult.failure(error)` classmethods exist; keyword construction still works
- [ ] `sartoriuslib.streaming.PollSourceAdapter` callable, fully typed; also exported at top level
- [ ] `SartoriusTransientTransportError` raised on cold-open frame-short — verified with a test that simulates a USB cold-open race; raised from BOTH `transport/serial.py` (0-byte read) and `protocol/xbpi/framing.py` (underrun)
- [ ] `ErrorContext` (unprefixed) has the §G base fields; `ErrorContext.address` `@property` returns `sbn_address`
- [ ] `Balance.snapshot()` returns `SartoriusDeviceSnapshot` with cached identity, no I/O
- [ ] `record()` yields `Recording(stream, summary, rate_hz, observed_rate_hz)`; `AcquisitionSummary` is mutable, updated in place; `finished_at` set on exit; `observed_rate_hz` populated for SBI autoprint after 10-frame window fills
- [ ] `open_device()` always returns an opened balance; `Balance.__aenter__` is a no-op `return self`; `__aexit__` closes
- [ ] `Session.recoverable_error_count` increments on retried transients
- [ ] `sartoriuslib.units.to_pint` accepts `Unit | str | None`; covers every `Unit` enum value; lossy by design per §K
- [ ] Sync facade mirrors every async API addition through `SyncPortal`
- [ ] **Cross-lib import-symmetry test passes** — `from sartoriuslib import open_device, find_devices, sample_to_row, PollSourceAdapter, Recording, DeviceResult; from sartoriuslib.units import to_pint` typechecks; same import shape works for sibling libs. **Note:** the test verifies *export presence* only; PollSourceAdapter method signatures intentionally differ per lib (see §E).
- [ ] All existing tests pass
- [ ] New unit tests for: PollSourceAdapter (including autoprint), snapshot, transient transport (with cold-open simulation covering both raise sites), to_pint coverage with Unit and str inputs, DiscoveryResult/DiscoverySummary normalization, Recording wrapper exposes the live summary
- [ ] CHANGELOG.md entry under `## [Unreleased]` listing breaking changes

**Removed acceptance criteria** (relative to the original spec):
- ~~`Balance.expected_rate_hz` works across stream lifecycle~~ — replaced by `Recording.rate_hz` and `Recording.observed_rate_hz` (§I).

---

## 7. Suggested order of work

1. `SartoriusTransientTransportError` + transport raises (§3.2) — **highest-value, do first**
2. `DeviceResult.success()` / `failure()` factories (§3.6) — small, precondition for §3.7
3. `Sample` field rename (§3.4)
4. `ErrorContext` base fields (§3.12)
5. Discovery normalization with `SartoriusDiscoveryResult` subclass (§3.3)
6. Delete `open_balance` / `BalanceManager` (§3.1)
7. `PollSourceAdapter` (§3.7)
8. `Balance.snapshot()` (§3.8)
9. `AcquisitionSummary` mutable + `Recording` wrapper (§3.10)
10. `Session.recoverable_error_count` (§3.9)
11. `units.to_pint` (§3.11)
12. Top-level `sample_to_row` (§3.5)
13. Sync facade updates (§3.14)
14. Optional: public logging hook (§3.15)
15. Tests + changelog

(No `Balance.expected_rate_hz` — replaced by `Recording.rate_hz` and `Recording.observed_rate_hz` per §I.)

---

## 8. Questions to surface (don't guess — ask the maintainer)

- ~~Should `open_device()` swallow cold-open transients internally?~~ — **resolved (§3.2): yes, swallow up to 3 with 50ms backoff inside `open_device()`. Post-open occurrences still surface as typed errors.**
- `SartoriusDeviceSnapshot.mode` — should this be the last observed mode or a fresh probe? Default: last observed (no I/O on snapshot).
- ~~Autoprint observed-rate semantics~~ — **resolved (§I, §3.10): rolling mean over the last 10 frames lives on `Recording.observed_rate_hz`; `None` until 10 frames observed. Not on `Balance`.**
- ~~`Quantity.to_pint()`~~ — **resolved at spec level (§K): no method, free function only. Keeps the surface uniform across libs.**
- ~~`sbn_address` vs `address`~~ — **resolved (§G, §3.12): native field stays `sbn_address`; `address` is a `@property` returning it. No data migration.**
- ~~`Balance.expected_rate_hz`~~ — **resolved (§I): property dropped; configured rate on `Recording.rate_hz`, observed rate on `Recording.observed_rate_hz`.**
