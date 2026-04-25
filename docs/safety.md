# Safety

`sartoriuslib` drives physical hardware. Safety rules are binding; see
the [Design doc](design.md) §6.1 for the authoritative list.

## Per-command safety tier

Every [`Command`](api/commands.md) carries a [`SafetyTier`](../src/sartoriuslib/devices/capability.py#L40):

| Tier | Examples | Gate |
| --- | --- | --- |
| `READ_ONLY` | weight, status, identity, capacity, increment, temperature, parameter reads | runs freely |
| `STATEFUL` | tare, zero | runs freely — these are normal interactive operations |
| `PERSISTENT` | parameter writes, save menu, communication settings | requires `confirm=True` |
| `DANGEROUS` | baud / SBN change, calibration init, protocol switch | requires `confirm=True` |

Calling a `PERSISTENT` or `DANGEROUS` operation without `confirm=True`
raises `SartoriusConfirmationRequiredError` *before* any I/O — no bytes
go out.

## Gate order

[`Session.execute()`](../src/sartoriuslib/devices/session.py) applies gates
in this fixed order:

1. **Safety tier** (hard) — `PERSISTENT` / `DANGEROUS` need `confirm=True`.
2. **Protocol** (hard) — the active-protocol variant must not be `None`,
   else `SartoriusProtocolUnsupportedError`.
3. **Known-denied command** (hard once observed) — if the per-session
   availability cache records `Availability.UNSUPPORTED` for this
   command, raise `SartoriusUnsupportedCommandError` pre-I/O without
   re-probing. `INAPPLICABLE` does **not** block.
4. **Family / capability priors** (soft by default) — emit a one-shot
   `SartoriusCapabilityWarning` and attempt the command anyway. Under
   `strict=True`, refuse pre-I/O with `SartoriusCapabilityError` instead.
5. **Execute**, then map the device response into an availability
   transition (see [Design](design.md) §6.1.1).

## Why soft-gate by default?

The reverse-engineering sample behind the family / capability tables is
small. The cost of a wrong denial (user blocked from a command their
balance actually supports) is worse than the cost of a failed attempt
(one round-trip and a clean typed error). `strict=True` is opt-in for
environments where an unexpected byte on the wire is worse than a
blocked call:

```python
async with await open_device("/dev/ttyUSB0", strict=True) as bal: ...
```

## Raw escape hatches

`Balance.raw_xbpi(opcode, args=b"", confirm=False)` and
`Balance.raw_sbi(command, confirm=False, expect_lines=1)` bypass the
typed-command layer for reverse-engineering and forward protocol work.

A built-in safelist of READ_ONLY xBPI opcodes runs without
`confirm=True`: identity reads (`0x00`, `0x01`, `0x02`, `0x05`, `0x07`),
weight reads (`0x1E`, `0x20`, `0x22`), status (`0x30`, `0x32`),
calibration record (`0xB9`), and config counter (`0xBA`). Everything
else — including any opcode whose effect we have not catalogued —
requires `confirm=True`.

The SBI safelist follows the same rule: documented read-only tokens run
freely; everything else requires the gate.

## Protocol mode switching

`Balance.configure_protocol(protocol, confirm=True)` is the only path
that mutates the balance's wire-protocol mode. It is gated on
`Capability.PROTOCOL_SWITCHING`, requires `confirm=True`, and on
success rebinds the session's active protocol and serial settings to
the new mode. `open_device(...)` **never** flips protocol mode as a
side effect.

[`sartoriuslib.maintenance`](api/maintenance.md) exposes the same
operation as a one-shot port-level helper for callers who don't want
to hold a full session.

## Tare / zero preconditions

`tare()` and `zero()` assume the balance is in the correct physical state
(empty pan for zero, target sample on pan for tare). These are user
responsibilities; the library cannot verify them remotely. Calls
return without verifying weight, so a misuse silently produces a
wrong reference value.

## SBI autoprint

If autoprint is active, SBI command/reply APIs that expect a reply
fail with `SartoriusAutoprintActiveError`. No-reply control tokens
remain possible under their normal safety gates. See
[Streaming](streaming.md) for the consume-only mode and the recovery
dance for front-panel toggles mid-session.

## Hardware test tiers

| Marker | What it does | Opt-in env var |
| --- | --- | --- |
| `hardware` | Read-only against a connected balance | `SARTORIUSLIB_TEST_PORT=/dev/ttyUSB0` |
| `hardware_stateful` | Changes device state (tare, zero, parameter writes) | `SARTORIUSLIB_ENABLE_STATEFUL_TESTS=1` |
| `hardware_destructive` | Calibration, baud/SBN change, protocol switch | `SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1` |

Default `pytest` runs exclude all three.

## See also

- [Commands](commands.md) — gate order on every command surface.
- [Balances](devices.md) — capability flags as priors not contracts.
- [Streaming](streaming.md) — `confirm=True` on the temporary-autoprint mode.
- [Testing](testing.md) — hardware tiers and `FakeTransport`.
- [Design](design.md) §6.1 — full gate-order rationale.
