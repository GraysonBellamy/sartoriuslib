---
description: Sartorius SBI control commands — data input syntax, command formats, parameter writes, and the function-key remote commands accepted over the data port.
---

# Data Interfaces

## Data Input

### SBI Commands (Data Input Format)

The computer connected via the data port can send control commands to the balance to control balance and application program functions.  
These control commands may have different formats and contain up to 20 characters. Each of these characters must be sent based on the setup configuration for data transmission.

---

## Formats for Control Commands (Syntax)

**Format 1:**
```
Esc ! CR LF
```

**Format 2:**
```
Esc ! # _ CR LF
```

### Definitions

- **Esc**: Escape  
- **!**: Command character  
- **#**: Digit  
- **<parameter>**: Parameter (number or letter)  
- **_**: Underline (ASCII: 95)  
- **CR**: Carriage return (optional)  
- **LF**: Line feed (optional)

### Examples

- Format 1: `Esc P`  
- Format 2: `Esc x1_`

---

# Overview of SBI Commands

| Format | Command | Action / Function | Comments |
|--------|--------|------------------|----------|
| 1 | ESC P | Print to the interface where the prompt originated | Corresponds to menu, with/without stability |
| 1 | ESC T | “TARE” key taring and zeroing | |
| 1 | ESC K | Filter “Very stable conditions” | |
| 1 | ESC L | Filter “Stable conditions” | |
| 1 | ESC M | Filter “Unstable conditions” | |
| 1 | ESC N | Filter “Very unstable conditions” | |
| 1 | ESC O | Block keys | |
| 1 | ESC Q | Acoustic signal | |
| 1 | ESC R | Unblock keys | |
| 1 | ESC S | Restart | |
| 1 | ESC Z | Internal calibration/adjustment | Depending on menu, 1/2 step increments |
| 1 | ESC U | Tare | |
| 1 | ESC V | Zero | |
| 1 | ESC W | External adjustment with default weight | Depending on menu, 1/2 step increments |

---

## Extended Commands (Format 2)

| Format | Command | Action / Function | Comments |
|--------|--------|------------------|----------|
| 2 | ESC f0_ | (Menu key) | |
| 2 | ESC f1_ | Start calibration | |
| 2 | ESC f2_ | (Enter key) | |
| 2 | ESC f5_ | Left draft shield key (closing/opening as learned or default) | Only if available |
| 2 | ESC f6_ | Right draft shield key (closing/opening as learned or default) | Only if available |
| 2 | ESC p_ | Print as with “PRINT” key (e.g., to several interfaces) | |
| 2 | ESC m0_ | Ionizer status | Only if available |
| 2 | ESC m1_ | Ionizer on, with preset time | Only if available |
| 2 | ESC m2_ | Ionizer off | Only if available |
| 2 | ESC s3_ | (CF key): Back, exit, cancel | |

---

## Draft Shield Commands

### Models with Analytical Draft Shield vs Rotational Draft Shield

| Format | Command | Analytical Draft Shield | Rotational Draft Shield | Comments |
|--------|--------|--------------------------|--------------------------|----------|
| 2 | ESC w0_ | Draft shield status | Draft shield status | Only if available |
| 2 | ESC w1_ | Open left door | Open draft shield 100% to the left | Only if available |
| 2 | ESC w2_ | Close all doors | Close draft shield | Only if available |
| 2 | ESC w3_ | Open upper door | Open draft shield up to position saved | Only if available |
| 2 | ESC w4_ | Open right door | Open draft shield door 100% to the right | Only if available |
| 2 | ESC w5_ | Open left and upper doors | — | Only if available |
| 2 | ESC w6_ | Open left and right doors | — | Only if available |
| 2 | ESC w7_ | Open right and upper door | — | Only if available |
| 2 | ESC w8_ | Open all doors | — | Only if available |

---

## Information / System Commands

| Format | Command | Action / Function |
|--------|--------|------------------|
| 2 | ESC x1_ | Print weigher type |
| 2 | ESC x2_ | Output serial number |
| 2 | ESC x3_ | Print balance software version |
| 2 | ESC s0_ | Press and hold the (Menu) key |

---

## Behavioral notes (verified empirically on MSE1203S BAC 00-39-21)

These notes are documented Sartorius-published syntax above; the points
below are observable side-effects and per-firmware deviations measured
on the bench rather than printed in the manual.

### `ESC P` pauses autoprint

Sending `ESC P` while autoprint (`p36=4` auto_wo / `p36=5` auto_w) is
streaming returns one weight line **and stops the autoprint stream
indefinitely**. The stream stays paused until one of: `ESC V` (zero),
`ESC T` (tare), front-panel zero/tare, or front-panel autoprint
re-enable. Treat `ESC P` as "stop autoprint and read once" rather than
just "read once."

This has implications for the package: if `Balance.poll()` issues
`ESC P` while in autoprint mode (because `sbi_autoprint_active` was
`False` at the time, e.g. due to false-negative open-time detection),
subsequent `stream(mode="autoprint")` reads will time out until
autoprint is resumed. Use `Balance.refresh_sbi_autoprint_state()` to
re-sniff and recover, or send `ESC V`/`ESC T` to wake the stream.

### `ESC V` / `ESC T` resume autoprint

Both execute their command (zero / tare) AND restart any paused
autoprint stream. After `ESC V` you may see a `Stat` status line then
the post-zero reading (`N           0.000 g  \r\n`); after `ESC T` the
post-tare reading appears in the resumed stream.

### Identity tokens (`ESC x1_/x2_/x3_`) work cleanly on MSE1203S BAC 00-39-21

In both autoprint-active and autoprint-paused states. Replies appear
**interleaved** between autoprint weight lines:

```
N     +    0.026    \r\n
MSE1203S-100-DR     \r\n        ← ESC x1_ reply, mid-stream
N     -    0.013    \r\n
```

The earlier hardware-day claim that this firmware silently ignored
identity tokens was wrong; the package's `_probe_sbi` `ESC P`
fallback is kept as defense-in-depth for unknown firmware revisions
but is not exercised on this MSE.

### Unsupported tokens on this firmware

Tokens that produce **zero reply** on MSE1203S BAC 00-39-21 (verified
2026-04-25 via raw-wire probes):

- `ESC ;`  `ESC O`  `ESC K`  `ESC S`  `ESC Z`

These appear in older Sartorius command tables (`ESC O` = block keys,
`ESC S` = restart, `ESC Z` = internal calibration) but the unit does
not respond on the wire. Use the documented Format-1/Format-2 table
above and the firmware-specific Format-2 set as the authoritative
list. Some of these may work on other Sartorius models or firmware
revisions; do not assume cross-firmware portability.

### Cross-protocol byte handling

When the port is in SBI mode, **xBPI bytes are silently ignored** —
the SBI parser does not co-exist with the xBPI parser. Sending xBPI
frames produces no reply and does not disturb the autoprint stream.
The reverse holds: in xBPI mode, all SBI tokens are silently dropped.
Each protocol owns the wire exclusively.
