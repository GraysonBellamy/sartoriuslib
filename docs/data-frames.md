---
description: Frozen Reading dataclasses returned by the Balance facade — protocol-agnostic models for weight value, unit, stability, and timestamp.
---

# Readings

Both wire protocols decode into the same frozen, `slots=True` dataclasses.
That parity is the whole point of the dual-protocol seam — a caller asks
for `poll()` and gets a `Reading` regardless of whether the active protocol
is xBPI or SBI. See [Design](design.md) §4 and §7.

This page surveys the public types returned by the [`Balance`](api/devices.md)
facade and notes which fields are protocol-specific (i.e. `None` on one side).

## `Reading`

[`Reading`](../src/sartoriuslib/devices/models.py) is the canonical
weight-reading shape. `xBPI 0x1E` decodes a measurement-frame body; SBI's
`+     0.00 g  \r\n` autoprint or `ESC P` reply parses the same fields.

| Field | Type | Notes |
| --- | --- | --- |
| `value` | `float \| None` | `None` on the off-scale sentinel. The measurement body alone cannot disambiguate overload from underload — call `Balance.status()` for that. |
| `unit` | `Unit` | Decoded from the xBPI unit byte or the SBI trailing token (`g`, `kg`, etc.). `Unit.UNKNOWN` preserves unrecognised values. |
| `sign` | `Sign` | `POSITIVE` / `NEGATIVE` / `ZERO` / `UNKNOWN`. |
| `stable` | `bool` | From the universal measurement-frame flag bit `0x40` — portable across MSE/WZA/BCE without a separate status round-trip. |
| `overload` | `bool` | xBPI: from the off-scale sentinel + sign byte. SBI: parsed from `+H` / `-H` notation when present. |
| `underload` | `bool` | Same source as `overload`. |
| `decimals` | `int \| None` | xBPI: byte[5] >> 4. SBI: parsed from the formatting in the line. `None` when not derivable. |
| `sequence` | `int \| None` | xBPI status-block sequence counter; **`None` on SBI** (no equivalent signal). |
| `status_flags` | `Mapping[str, bool]` | Protocol-specific flags: `"stable"`, `"off_scale"`, plus `"isocal_due"` / `"adc_trusted"` on xBPI long frames. |
| `protocol` | `ProtocolKind` | `XBPI` or `SBI`. Lets callers cross-check the source. |
| `received_at` | `datetime` | Wall-clock receive time. For logs. |
| `monotonic_ns` | `int` | Monotonic clock — for jitter analysis. |
| `raw` | `bytes` | Original frame or line. SBI lines are stored as raw bytes (not `str`) for byte-exact replay. |

`Reading` implements `__format__` so `f"{r:.4f}"` works:

```python
reading = await bal.poll()
print(f"{reading:.3f} {reading.unit}")  # "199.995 g"
```

Off-scale readings (`value is None`) format as `"None"` for any non-empty
numeric spec rather than raising — settling windows during a tare or zero
commonly emit `None` values and an f-string crash there is a surprising
failure mode.

`Reading.as_dict()` flattens to a row-shaped dict for tabular sinks. Booleans
render as `0`/`1` so SQLite picks INTEGER affinity and CSV/JSONL round-trip
cleanly.

## `BalanceStatus`

[`BalanceStatus`](../src/sartoriuslib/devices/models.py) is the
status-block snapshot from xBPI `0x30` or the SBI equivalent.

| Field | Type | Notes |
| --- | --- | --- |
| `stable` | `bool \| None` | `None` when the protocol can't surface stability separately from the measurement frame. |
| `state` | `BalanceState` | `STABLE` / `UNSTABLE` / `OVERLOAD` / `UNDERLOAD` / `OFF` / `UNKNOWN`. |
| `isocal_due` | `bool \| None` | **MSE-only signal; `None` on WZA/BCE/SBI.** |
| `adc_trusted` | `bool \| None` | **MSE-only; `None` on other families.** |
| `sequence` | `int \| None` | xBPI sequence counter; `None` on SBI. |
| `raw_state` | `int \| str \| None` | Raw state byte for cross-checking against [protocol.md](protocol.md) §8. |
| `raw_status` | `int \| str \| None` | Raw status byte. |
| `raw` | `bytes \| str` | Whole status block or line, untouched. |

## `DeviceInfo`

[`DeviceInfo`](../src/sartoriuslib/devices/models.py) is the identity
snapshot produced by `Balance.identify()`.

| Field | Type | Notes |
| --- | --- | --- |
| `manufacturer` | `str \| None` | xBPI `0x07`. |
| `model` | `str` | xBPI `0x02` or SBI `x1_`. Drives `family` classification. |
| `serial` | `str \| None` | xBPI `0x05` (OEM text) or SBI `x3_`. |
| `factory_number` | `str \| bytes \| None` | xBPI `0x01` raw blob; preserved as bytes when not ASCII. |
| `software` | `str \| None` | xBPI `0x00` / SBI `x2_`. |
| `firmware` | `FirmwareVersion \| None` | Parsed from `software`. |
| `family` | `BalanceFamily` | `CUBIS` / `OEM_WEIGH_CELL` / `BASIC_LAB` / `UNKNOWN`. |
| `protocol` | `ProtocolKind` | The protocol the session opened with. |
| `capacity` | `Quantity \| None` | xBPI `0x0C` when available. |
| `increment` | `Quantity \| None` | xBPI `0x0D`. |
| `sbn` | `int \| None` | xBPI bus address. |
| `serial_settings` | `SerialSettings` | Active serial config. |
| `capabilities` | `Capability` | Bitmap of currently `SUPPORTED` capabilities. |
| `probe_report` | `Mapping[Capability, ProbeOutcome]` | Full availability state per capability — see [Balances](devices.md#capabilities-are-priors-not-contracts). |
| `temperature_sensor_indices` | `tuple[int, ...] \| None` | `None` until `Balance.discover_temperature_sensors()` has run. Some firmwares expose sparse indices (`0, 1, 3` on MSE1203S, with the `7f ff ff ff` sentinel at index 2). |

## Other public types

| Type | Returned by | Notes |
| --- | --- | --- |
| [`Quantity`](../src/sartoriuslib/devices/models.py) | `Balance.capacity()`, `Balance.increment()` | `value: float`, `unit: Unit`. |
| [`TemperatureReading`](../src/sartoriuslib/devices/models.py) | `Balance.temperature(sensor)` | `celsius` is `None` when the sensor index returns the `7f ff ff ff` sentinel. |
| [`ParameterEntry`](../src/sartoriuslib/devices/models.py) | `Balance.read_parameter(index)` | Raw `(current, max, raw)`. Typed accessors decode through the registry. |
| [`CalRecord`](../src/sartoriuslib/devices/models.py) | `Balance.last_cal_record()` | `has_metadata` distinguishes "never recorded" (post cold boot) from "valid record". |
| [`ProbeOutcome`](../src/sartoriuslib/devices/models.py) | per-capability entry on `DeviceInfo.probe_report` | `Availability` + `ProbeSource` + timestamp + human-readable detail. |
| [`Sample`](../src/sartoriuslib/streaming/sample.py) | recorder yield | Wraps a `Reading` with device name, requested timestamp, elapsed time, error. See [Logging](logging.md). |

## Enums

| Enum | Module | Values |
| --- | --- | --- |
| `Unit` | [registry/units.py](../src/sartoriuslib/registry/units.py) | `G`, `KG`, `MG`, `LB`, `OZ`, `CT`, `N`, ... plus `UNKNOWN`. |
| `Sign` | [registry/units.py](../src/sartoriuslib/registry/units.py) | `POSITIVE`, `NEGATIVE`, `ZERO`, `UNKNOWN`. |
| `BalanceState` | [devices/models.py](../src/sartoriuslib/devices/models.py) | `STABLE`, `UNSTABLE`, `OVERLOAD`, `UNDERLOAD`, `OFF`, `UNKNOWN`. |
| `ProtocolKind` | [protocol/base.py](../src/sartoriuslib/protocol/base.py) | `XBPI`, `SBI`, `AUTO`. |
| `BalanceFamily` | [devices/kind.py](../src/sartoriuslib/devices/kind.py) | `CUBIS`, `OEM_WEIGH_CELL`, `BASIC_LAB`, `UNKNOWN`. |
| `Capability` | [devices/capability.py](../src/sartoriuslib/devices/capability.py) | Flag bitmap — see [Balances](devices.md#capability-flags). |
| `Availability` | [devices/capability.py](../src/sartoriuslib/devices/capability.py) | `UNKNOWN`, `SUPPORTED`, `UNSUPPORTED`, `INAPPLICABLE`. |
| `ProbeSource` | [devices/capability.py](../src/sartoriuslib/devices/capability.py) | `FAMILY_TABLE`, `TARGETED_PROBE`, `LIVE_CALL`, `USER_OVERRIDE`. |
| `SafetyTier` | [devices/capability.py](../src/sartoriuslib/devices/capability.py) | `READ_ONLY`, `STATEFUL`, `PERSISTENT`, `DANGEROUS`. |

Every enum that decodes protocol data carries an `UNKNOWN` member or
otherwise preserves raw values so the library stays forward-compatible
with firmware revisions we have not yet captured.

## See also

- [Balances](devices.md) — `Balance` facade.
- [Commands](commands.md) — what produces these readings.
- [Streaming](streaming.md) — `Sample` (wraps `Reading`) and `AcquisitionSummary`.
- [Wire protocol](protocol.md) — frame layouts behind these decoded shapes.
- [Design](design.md) §7 — full type catalogue.
