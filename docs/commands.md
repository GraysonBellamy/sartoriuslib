# Commands

Every Sartorius command is one [`Command[Req, Resp]`](../src/sartoriuslib/commands/base.py#L107)
spec — a frozen dataclass carrying metadata (name, family hints, capability
hints, safety tier, optional firmware bounds) plus at most one variant per
protocol. Per-protocol work lives on
[`XbpiVariant`](../src/sartoriuslib/commands/base.py#L67) /
[`SbiVariant`](../src/sartoriuslib/commands/base.py#L87) objects, not as
methods bolted onto `Command`. The session selects the variant matching the
active protocol; if the selected variant is `None`, the call fails pre-I/O
with `SartoriusProtocolUnsupportedError`. See [Design](design.md) §4.2.

This page is a catalogue by module. Full API reference is at
[api/commands.md](api/commands.md); the
[`Balance`](api/devices.md) facade methods are thin wrappers over
`session.execute(COMMAND, request)`.

## Anatomy of a command

```python
from sartoriuslib.commands import Command
from sartoriuslib.commands.base import XbpiVariant, SbiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.kind import BalanceFamily
```

Key [`Command`](../src/sartoriuslib/commands/base.py#L107) fields:

| Field | Purpose |
| --- | --- |
| `name` | Python-friendly identifier; used in error context and probe-report keys. |
| `xbpi` | [`XbpiVariant`](../src/sartoriuslib/commands/base.py#L67) carrying opcode + encode/decode, or `None` if the command is not defined for xBPI. |
| `sbi` | [`SbiVariant`](../src/sartoriuslib/commands/base.py#L87) carrying ASCII token + encode/decode, or `None` if the command is not defined for SBI. |
| `family_hints` | Advisory `frozenset[BalanceFamily]` prior; empty = no prior. |
| `capability_hints` | Advisory `Capability` flag; `Capability(0)` = no prior. |
| `safety` | [`SafetyTier`](api/devices.md): `READ_ONLY`, `STATEFUL`, `PERSISTENT`, `DANGEROUS`. |
| `min_firmware` / `max_firmware` | Optional firmware-version bounds. |
| `parameterized` | Set when the request carries a sub-resource selector (sensor index, parameter index, area). Out-of-range arguments don't poison the per-command availability cache (see [Design](design.md) §6.1.1). |

Every command is dispatched via `session.execute(spec, request)`. The
request is a typed per-command dataclass; the response is the typed result
the variant decodes. The session never juggles raw bytes at the public
surface.

## Gate order

`Session.execute()` applies gates in a specific order before any I/O. See
[Safety](safety.md) for the full table and [Design](design.md) §6.1 for
rationale.

1. **Safety tier** (hard) — `PERSISTENT` / `DANGEROUS` require `confirm=True`.
2. **Protocol** (hard) — active-protocol variant must not be `None`.
3. **Known-denied** (hard once observed) — per-session availability cache short-circuits commands the device has already returned `0x04` for.
4. **Family / capability priors** (soft by default; hard under `strict=True`) — emits `SartoriusCapabilityWarning` and attempts the command anyway.
5. **Execute**, then update the availability cache from the device's response.

## Commands by module

### Weight — [commands/weight.py](../src/sartoriuslib/commands/weight.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `READ_NET` | xBPI `0x1E` / SBI `ESC P` | READ_ONLY | `Balance.poll()`, `Balance.read_net()` |
| `READ_GROSS` | xBPI `0x20` | READ_ONLY | `Balance.read_gross()` |
| `READ_TARE_VALUE` | xBPI `0x22` | READ_ONLY | `Balance.read_tare_value()` |
| `READ_NET_HIRES` | xBPI `0x1F` | READ_ONLY | `Balance.read_net(hires=1)` (capability-gated on `HIRES_WEIGHT`) |
| `READ_GROSS_HIRES` | xBPI `0x21` | READ_ONLY | `Balance.read_gross(hires=1)` |

All weight reads decode into a [`Reading`](data-frames.md) regardless of
protocol.

### Tare / zero — [commands/tare.py](../src/sartoriuslib/commands/tare.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `TARE` | xBPI `0x14` / SBI `ESC T` | STATEFUL | `Balance.tare()` |
| `ZERO` | xBPI `0x18` | STATEFUL | `Balance.zero()` |

`STATEFUL` runs without `confirm=True` — these are normal interactive
operations.

### Status / identity — [commands/status.py](../src/sartoriuslib/commands/status.py), [commands/identity.py](../src/sartoriuslib/commands/identity.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `STATUS_BLOCK` | xBPI `0x30` / `0x32` | READ_ONLY | `Balance.status()` |
| `READ_MODEL` | xBPI `0x02` / SBI `x1_` | READ_ONLY | merged into `Balance.identify()` |
| `READ_MANUFACTURER` | xBPI `0x07` | READ_ONLY | merged into `Balance.identify()` |
| `READ_OEM_TEXT` | xBPI `0x05` | READ_ONLY | merged into `Balance.identify()` |
| `READ_SW_VERSION` | xBPI `0x00` / SBI `x2_` | READ_ONLY | merged into `Balance.identify()` |
| `READ_FACTORY_NUMBER` | xBPI `0x01` / SBI `x3_` | READ_ONLY | merged into `Balance.identify()` |
| `READ_SBN` | xBPI ID frame | READ_ONLY | merged into `Balance.identify()` |

`Balance.identify()` runs the identity reads in one shot and returns a
single [`DeviceInfo`](data-frames.md). Open with `identify=True` (default)
to populate `Balance.info` automatically.

### Metrology — [commands/metrology.py](../src/sartoriuslib/commands/metrology.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `READ_CAPACITY` | xBPI `0x0C` | READ_ONLY | `Balance.capacity(area=0)` |
| `READ_INCREMENT` | xBPI `0x0D` | READ_ONLY | `Balance.increment(area=0)` |
| `READ_TEMPERATURE` | xBPI `0x76` | READ_ONLY | `Balance.temperature(sensor=0)` (capability-gated on `TEMPERATURE_SENSORS`) |

`READ_TEMPERATURE` is `parameterized=True` — an out-of-range sensor index
returns `SartoriusIndexOutOfRangeError` without poisoning `temperature(0)`
for the rest of the session.

### Parameters — [commands/parameters.py](../src/sartoriuslib/commands/parameters.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `READ_PARAMETER` | xBPI `0x55` | READ_ONLY | `Balance.read_parameter(index)` |
| `WRITE_PARAMETER` | xBPI `0x56` | PERSISTENT | `Balance.write_parameter(index, value, confirm=True)` |

Both are `parameterized=True`. Typed accessors (`get_filter_mode`,
`get_display_unit`, `get_auto_zero`, `get_isocal_mode`, `get_tare_behavior`,
`get_menu_access`) wrap `READ_PARAMETER` with the
[registry](api/registry.md) decoders so callers don't have to memorise
parameter indices. Setters validate enum values before sending and require
`confirm=True`.

### Calibration — [commands/calibration.py](../src/sartoriuslib/commands/calibration.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `LAST_CAL_RECORD` | xBPI `0xB9` | READ_ONLY | `Balance.last_cal_record()` (capability-gated on `CAL_RECORD`) |
| `INTERNAL_ADJUST` | xBPI `0x28` | DANGEROUS | `Balance.internal_adjust(confirm=True)` (capability-gated on `INTERNAL_CAL`) |

### System — [commands/system.py](../src/sartoriuslib/commands/system.py)

| Command | Opcode | Safety | Surface |
| --- | --- | --- | --- |
| `CONFIG_COUNTER` | xBPI `0xBA` | READ_ONLY | session-internal cache invalidation (capability-gated on `CONFIG_COUNTER`) |
| `SAVE_MENU` | xBPI `0x47` | PERSISTENT | `Balance.save_menu(confirm=True)` |
| `RELOAD_MENU` | xBPI `0x46` | PERSISTENT | `Balance.reload_menu(confirm=True)` |

`CONFIG_COUNTER` is the runtime-config invalidation signal the session
consults for the read-side cache (`capacity`, `increment`, identify, typed
parameter accessors). See [Design](design.md) §6.3 for the caveat about
which writes the counter actually ticks on.

### Raw — [commands/raw.py](../src/sartoriuslib/commands/raw.py)

`Balance.raw_xbpi(opcode, args=b"", confirm=False)` and
`Balance.raw_sbi(command, confirm=False, expect_lines=1)` bypass the
typed-command layer for reverse-engineering and forward protocol work.
A built-in safelist of READ_ONLY opcodes (`0x00`, `0x01`, `0x02`, `0x05`,
`0x07`, `0x1E`, `0x20`, `0x22`, `0x30`, `0x32`, `0xB9`, `0xBA`) runs
without `confirm=True`; everything else requires the explicit gate.

## Configuration commands (Balance methods)

These are exposed as `Balance` methods rather than top-level command
specs because they cut across multiple opcodes or carry post-I/O state
restoration.

| Method | Tier | Notes |
| --- | --- | --- |
| `Balance.configure_protocol(protocol, confirm=True)` | DANGEROUS | xBPI↔SBI mode switch (capability-gated on `PROTOCOL_SWITCHING`). |
| `Balance.set_baud_rate(baud, confirm=True)` | DANGEROUS | Updates the parameter, saves, re-opens the transport at the new rate. |
| `Balance.write_sbn_address(sbn, confirm=True)` | DANGEROUS | Updates the xBPI bus address and rebinds the session. |

[`sartoriuslib.maintenance`](api/maintenance.md) exposes the same three
operations as one-shot port-level helpers
(`switch_protocol`, `set_baud_rate`, `write_sbn_address`) for callers who
don't want a full session lifecycle.

## See also

- [Balances](devices.md) — `Balance` facade and capability flags.
- [Readings](data-frames.md) — `Reading` / `BalanceStatus` / `DeviceInfo`.
- [Safety](safety.md) — gate order and `confirm=True` rules.
- [Wire protocol](protocol.md) — opcode tables and frame layout.
- [Design](design.md) §6 — full command surface and gate rationale.
