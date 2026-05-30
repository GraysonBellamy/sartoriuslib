---
description: Troubleshooting Sartorius balance connections — factory SBI defaults, serial mis-config, autoprint surprises, and the typed error hierarchy.
---

# Troubleshooting

This page collects the failure modes that come up first in the field —
serial mis-config, factory protocol defaults, autoprint surprises, and
the typed errors the library raises when something is off. The CLI tools
in [`sartoriuslib.cli`](api/sartoriuslib.md) cover most of the
investigation paths.

## "I just got a balance — what protocol is it speaking?"

**All Sartorius balances ship from the factory in SBI.** Switching to
xBPI is a front-panel menu change on every family. So a brand-new
device will not respond to the default `open_device("/dev/ttyUSB0")`
call (which forces xBPI).

First-contact paths:

```python
from sartoriuslib import open_device, ProtocolKind

# Force SBI explicitly.
async with await open_device("/dev/ttyUSB0", protocol=ProtocolKind.SBI) as bal:
    info = await bal.identify()

# Or auto-detect (passive autoprint sniff → xBPI probe → SBI probe).
async with await open_device("/dev/ttyUSB0", protocol=ProtocolKind.AUTO) as bal:
    print(bal.session.active_protocol, bal.info.model)
```

`AUTO` is conservative: it never writes during the sniff window and
never sweeps baud or parity. Wider port discovery lives in
`sarto-discover`.

## Per-family factory defaults

| Family | Factory protocol | Baud / framing |
| --- | --- | --- |
| MSE (Cubis) | SBI command/reply | 19200-8-O-1 (xBPI side same baud) |
| BCE (basic lab) | SBI command/reply | 9600-8-O-1 |
| WZA (OEM weigh cell) | **SBI autoprint** | 1200-7-O-1 |

WZA's autoprint default is the trap that bites first — a `poll()` will
see continuous output and the open-time identify will trip
`SartoriusAutoprintActiveError`. See [Streaming](streaming.md) for the
consume-only mode.

## `sarto-discover`

```bash
sarto-discover /dev/ttyUSB0 --baud 9600 --parity O --timeout 1.0
```

Probes a single port at the supplied serial settings and prints the
[`DiscoveryResult`](api/devices.md). Like `open_device(protocol=AUTO)`,
discovery is conservative — never sweeps baud or parity, never writes
during the autoprint sniff.

If `sarto-discover` finds nothing:

1. Verify the port path. On Linux, `ls /dev/ttyUSB*` and `lsusb` confirm
   the adapter is attached.
2. Verify permission. The user needs read/write on the device, usually
   via `sudo usermod -aG dialout $USER`.
3. Try the family's documented factory baud (table above).
4. As a last resort, use `sarto-diag tap` to passively watch the line
   while toggling the front-panel protocol switch.

## `sarto-read`

```bash
sarto-read /dev/ttyUSB0 --protocol auto
```

Open, identify, print one reading, exit. The fastest sanity check that
a balance is alive and decoded correctly.

## "Cross-mode bytes are silently ignored"

xBPI mode silently ignores SBI tokens, and SBI mode silently ignores
xBPI bytes — the parsers are mutually exclusive. If a session opens in
the wrong mode, `poll()` will time out rather than error with a useful
message. Confirm the active protocol with `bal.session.active_protocol`
before debugging deeper.

## SBI autoprint surprises

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `SartoriusAutoprintActiveError` on `open_device(..., identify=True)` | Autoprint enabled (factory default for WZA) | Open with `identify=False`, or use `mode="autoprint"` to consume the stream. |
| `SartoriusAutoprintActiveError` on `bal.stream(mode="poll")` | Autoprint flipped on mid-session | Use `mode="autoprint"` instead. |
| `poll()` times out after autoprint was disabled mid-session | Session still in autoprint mode | Call `bal.refresh_sbi_autoprint_state()` to re-detect. |

See [Streaming](streaming.md) for the full state machine.

## Common typed errors

| Error | Meaning | What to do |
| --- | --- | --- |
| `SartoriusConnectionError` | Transport open failed | Check port path, permissions, cable. |
| `SartoriusTimeoutError` | No reply within `timeout` | Wrong protocol? Wrong baud? Cross-mode bytes ignored? |
| `SartoriusFrameError` | xBPI framing or checksum invalid | Line noise, electrical fault, or running xBPI parser against an SBI stream. |
| `SartoriusParseError` | Line/frame parsed but body unrecognised | Capture the raw bytes (`bal.raw_xbpi(...)`, `sarto-diag tap`) and decode offline with `sarto-decode`. |
| `SartoriusProtocolUnsupportedError` | Active protocol has no variant for this command | Switch protocol with `configure_protocol(...)`, or use `raw_xbpi`/`raw_sbi`. |
| `SartoriusUnsupportedCommandError` | Device returned xBPI `0x04` | Likely capability gap; check `info.probe_report`. |
| `SartoriusOperationNotApplicableError` | Device returned xBPI `0x06` | State-dependent — retry after the state changes (e.g. tare during cal). |
| `SartoriusValueOutOfRangeError` | Device returned xBPI `0x03` | Bad argument — clamp to the documented range. |
| `SartoriusIndexOutOfRangeError` | Device returned xBPI `0x10` (or `0x04` on parameterised commands) | Sub-resource doesn't exist; check `discover_temperature_sensors()` etc. |
| `SartoriusConfirmationRequiredError` | `PERSISTENT` / `DANGEROUS` op without `confirm=True` | Add `confirm=True` if the operation is intentional — see [Safety](safety.md). |
| `SartoriusCapabilityError` | `strict=True` and prior mismatch | Drop `strict` or set the prior; the device is the source of truth. |
| `SartoriusAutoprintActiveError` | SBI autoprint blocking command/reply | See above. |

## Offline decode with `sarto-decode`

When a wire-trace dump shows up in a bug report or RE session, decode
it without hardware:

```bash
sarto-decode --xbpi "0b 41 48 bb a3 d7 0a 3d 30 82 45 07"
sarto-decode --sbi "+     0.00 g"
```

`sarto-decode` understands xBPI subtypes, TLV bodies, and SBI weight /
identity lines. Output is JSON-friendly; pipe to `jq` for filtering.

## Diagnostics CLI (`sarto-diag`)

Reverse-engineering tools live under the `sarto-diag` namespace. They
are deliberately separate from the stable CLI because they can write to
the device. Always behind a risk-visible prefix:

| Subcommand | Purpose |
| --- | --- |
| `sarto-diag snapshot` | Dump every read-only opcode the balance answers for a given identify-classified family. |
| `sarto-diag sweep` | Walk an opcode range and record responses. |
| `sarto-diag argfuzz` | Probe an opcode's argument space (TLV / index). Destructive ops gated. |
| `sarto-diag tap` | Passively watch the line and decode in real time. |
| `sarto-diag stream` | Continuous sample stream for jitter / framing analysis. |

Destructive subcommands require the explicit
`--i-understand-this-is-destructive` flag. Never invoked from normal
discovery or `open_device`.

## Forced protocol switch

If the balance is in the wrong mode and you cannot reach the front
panel:

```bash
sarto-configure switch-protocol /dev/ttyUSB0 --to xbpi --confirm
```

The CLI thinly wraps `Balance.configure_protocol(...)`, gated on
`Capability.PROTOCOL_SWITCHING` and `confirm=True`. See
[Safety](safety.md).

## Capturing for a bug report

Useful artefacts:

- The raw command and reply bytes — `Balance.raw_xbpi(...)` returns the
  whole reply frame; `bal.session.last_raw` holds the last write/read.
- `bal.info.probe_report` — full availability state per capability.
- `sarto-diag snapshot` output — read-only opcode survey.
- The CLI version banner: `python -c "import sartoriuslib; print(sartoriuslib.__version__)"`.

## See also

- [Wire protocol](protocol.md) — frame layouts, opcode tables, error codes.
- [SBI commands](sbi_commands.md) — full SBI token reference.
- [SBI data output](sbi_data_output.md) — autoprint format spec.
- [Safety](safety.md) — `confirm=True` and gate order.
- [Testing](testing.md) — `FakeTransport` for hardware-free repros.
