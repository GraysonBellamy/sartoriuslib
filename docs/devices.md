# Balances

Sartorius balances all do the same thing — weigh, tare, zero — and differ
in *how much extra* they can do, not *what they are*. `sartoriuslib` exposes
a single [`Balance`](api/devices.md) class for every model. Family is a
discriminator on `DeviceInfo`, capabilities are a flag bitmap, and
family-specific behaviour is dispatched on capabilities, not by class
hierarchy. See [Design](design.md) §5 for the full rationale.

## One class, many families

`open_device(...)` always returns a [`Balance`](api/devices.md). The
balance's [`DeviceInfo`](api/devices.md) carries the protocol that opened
it, the model string returned by xBPI `0x02` (or SBI `x1_`), the family
classification, and the seeded + live-probed capability bitmap.

```python
async with await open_device("/dev/ttyUSB0") as bal:
    info = bal.info  # populated when open_device(..., identify=True)
    print(info.model, info.family, info.protocol)
    print(info.capabilities)
```

## Family classification

Family is decided by the model-string prefix:

| Prefix | Family | Notes |
|---|---|---|
| `MSE*` | `BalanceFamily.CUBIS` | Cubis line. Full xBPI plus extended opcodes (`0xBC` module list, app modes, level sensor). |
| `WZ*` / `WZA*` | `BalanceFamily.OEM_WEIGH_CELL` | OEM weigh cells. Subset of the MSE opcode table. |
| `BCE*` | `BalanceFamily.BASIC_LAB` | Basic lab balances. MSE opcode subset, no Cubis extensions. |
| anything else | `BalanceFamily.UNKNOWN` | First-class case — no priors, every call becomes a live probe. |

[`classify_family(model)`](../src/sartoriuslib/devices/kind.py#L26) is the
helper. Classification is case-insensitive and whitespace-tolerant.

!!! note "All families ship in SBI from the factory"
    Every Sartorius balance — MSE, WZA, BCE — ships from the factory in
    **SBI**, not xBPI. Switching to xBPI is a front-panel menu change on
    every family. WZA additionally defaults to **autoprint at 1200-7-O-1**;
    MSE and BCE default to SBI command/reply at the same baud as their
    xBPI side. See [Troubleshooting](troubleshooting.md) for first-contact
    settings.

## Capability flags

[`Capability`](api/devices.md) is a `Flag` enum. Bits are seeded from a
family-defaults table when `identify()` succeeds and then confirmed (or
contradicted) by lightweight targeted probes and by actual command
attempts over the life of the session.

| Capability | Source | Meaning |
|---|---|---|
| `XBPI_SUPPORT` | identify | Balance itself supports xBPI (whether or not the active protocol is xBPI). |
| `SBI_SUPPORT` | identify | Balance itself supports SBI. |
| `PROTOCOL_SWITCHING` | family table + probe | Confirmed protocol switch available (e.g. WZA SBI→xBPI). |
| `HIRES_WEIGHT` | xBPI `0x1F` | Sub-mg weight read. |
| `PARAMETER_TABLE` | xBPI `0x55` | Indexed parameter table. Size varies (70 vs 8). |
| `CONFIG_COUNTER` | xBPI `0xBA` | Runtime-config invalidation counter (cache hint). |
| `TEMPERATURE_SENSORS` | xBPI `0x76` | One or more temperature sensors. Count varies 1/2/3. |
| `CAL_RECORD` | xBPI `0xB9` | Last calibration metadata. |
| `INTERNAL_CAL` | xBPI `0x28` | Internal calibration adjust. |
| `EXTERNAL_CAL` | family + probe | External calibration available. |
| `ISOCAL` | parameter `p15`, status bit `0x10` | isoCAL automatic adjustment. |
| `EXTENDED_OPCODES` | xBPI `0xBC` etc. | Cubis-only module list and extended opcodes. |
| `APP_MODES` | family table | Count / density / percent application modes (Cubis). |
| `LEVEL_SENSOR` | parameters `p59`/`p60` | Cubis level sensor. |
| `BARGRAPH` | xBPI `0x2F` | Bargraph display. |
| `AUTO_OUTPUT` | parameter `p36` | SBI autoprint (`auto_wo` / `auto_w`). |
| `RAW_ADC` | xBPI `0x75` | Raw ADC counts (BCE). |

## Capabilities are priors, not contracts

The reverse-engineering sample behind these tables is small — a handful
of models, mostly in one firmware revision each. The library treats
family tables and capability defaults as **priors from observation**,
not protocol guarantees.

[`DeviceInfo.probe_report`](api/devices.md) carries the full
[`ProbeOutcome`](api/devices.md) per capability:

- `Availability.UNKNOWN` — never exercised; priors may exist.
- `Availability.SUPPORTED` — directly confirmed.
- `Availability.UNSUPPORTED` — device returned xBPI `0x04` / equivalent SBI refusal. Sticky per session.
- `Availability.INAPPLICABLE` — device returned xBPI `0x06`. State-dependent; retryable.

Plus a [`ProbeSource`](api/devices.md): `FAMILY_TABLE` (seeded prior),
`TARGETED_PROBE` (explicit probe during `identify()`), `LIVE_CALL`
(updated by a normal command's response), or `USER_OVERRIDE`.

By default, the session **attempts** commands whose priors don't match
and updates the probe report on the response. Pre-I/O refusal happens
only under `strict=True` or after an observed denial on the current
session. See [Safety](safety.md) and [Design](design.md) §6.1 for the
gate-order rationale.

## Identifying a balance

```python
async with await open_device("/dev/ttyUSB0") as bal:
    info = await bal.identify()
    print(f"{info.manufacturer} {info.model}")
    print(f"  serial:    {info.serial}")
    print(f"  software:  {info.software}")
    print(f"  capacity:  {info.capacity}")
    print(f"  increment: {info.increment}")
    print(f"  family:    {info.family}")
    print(f"  caps:      {info.capabilities}")
    for cap, outcome in info.probe_report.items():
        print(f"  {cap.name:>20s}  {outcome.availability}  ({outcome.source})")
```

`identify()` runs on `open_device(..., identify=True)` (the default) and
populates `Balance.info`. Re-running it forces a refresh — useful after a
`configure_protocol(...)` switch or a parameter write that may flip a
capability.

## Discovery

[`discover_port(port)`](../src/sartoriuslib/devices/discovery.py) probes
a single port and returns a [`DiscoveryResult`](api/devices.md) regardless
of outcome — `protocol` and `model` are populated only on success. The
[`sarto-discover`](troubleshooting.md#sarto-discover) CLI sweeps multiple
ports and serial settings.

## See also

- [Commands](commands.md) — the command surface and gate order.
- [Readings](data-frames.md) — `Reading`, `BalanceStatus`, `DeviceInfo` shapes.
- [Streaming](streaming.md) — xBPI cadenced poll and SBI autoprint modes.
- [Wire protocol](protocol.md) — xBPI/SBI framing and opcode tables.
- [Design](design.md) §5 — full taxonomy and runtime-verification model.
