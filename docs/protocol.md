# Sartorius xBPI Protocol — Reverse-Engineered Reference

A reference to the Sartorius xBPI binary protocol, reverse engineered
across three balances spanning Sartorius's product line:

| Model | Class | Capacity / resolution | xBPI default |
|---|---|---|---|
| **MSE1203S-100-DR** | Cubis analytical | 1200 g / 1 mg | 19200-8-O-1 |
| **WZA8202-N** | OEM weigh cell | 8200 g / 10 mg | 1200-8-O-1 |
| **BCE3202-1S** | Basic laboratory | 3200 g / 10 mg | 9600-8-O-1 |

All three families ship from the factory in **SBI mode**; switching to
xBPI requires a front-panel menu change on each unit. The "xBPI default"
column above is the framing the unit uses *once* it has been switched to
xBPI — not the protocol it runs out of the box.

Cross-referenced with the publicly available WZG OEM weigh-cell manual,
which documents a partial command list for a related OEM cell family.
Many commands overlap across all three balances, but the Cubis has
expanded the protocol — most notably by requiring **TLV-wrapped
arguments** where WZG shows plain byte arguments.

Where protocol features are uniform across all three balances, they
are presented as the xBPI baseline. Where they differ, the text calls
out which balance family behaves how. §14 consolidates the cross-family
differences in one place.

To the best of our knowledge, this is the **first open-source reverse
engineering** of xBPI. Every "Sartorius driver" on GitHub/PyPI implements
the ASCII **SBI** protocol, not the binary xBPI documented here.

---

## Confidence legend

- **[SURE]** — verified by live captures AND/OR matching the WZG manual.
- **[LIKELY]** — strongly supported by evidence but not bulletproof.
- **[GUESS]** — plausible interpretation, not yet validated.
- **[UNKNOWN]** — observed but meaning not understood.

---

## 1. What is xBPI?

Sartorius balances speak three documented protocols:

| Protocol | Encoding | Public drivers? |
|---|---|---|
| **SBI** (Sartorius Balance Interface) | ASCII (`ESC P`, `ESC T`…) | Many |
| **BPI** (Binary Processor Interface) | Binary, legacy | None |
| **xBPI** (eXtended BPI) | Binary, with SBN addressing | **None** |

xBPI is the modern binary protocol used by Cubis MSE, Secura, Quintix (when
set to binary mode), and OEM weigh cells. It is **not publicly documented**
by Sartorius. Sartorius sells a paid OPC server product whose entire
purpose is to bridge xBPI; that product exists because the protocol is
closed.

## 2. Physical and link layer

### 2.1 Serial parameters

| Parameter | Value |
|---|---|
| Default baud | **family-specific**: MSE = 19200, BCE = 9600, WZA = 1200 |
| Supported baud | 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 (via p31) |
| Data bits | 8 |
| Parity | Odd |
| Stop bits | 1 |
| Flow control | None (hardware CTS documented but not required) |

8-O-1 framing is universal across all three balances. The WZG manual
claims "7-bit ASCII" for xBPI, which is **wrong** — captured frames
contain bytes like 0x88, 0xBB that cannot fit in 7 bits. Use 8-O-1.

The PC-USB port is **parity-forgiving on receive** — the balance accepts
requests at any parity regardless of its menu-configured parity, so the
"PC-USB parity" menu setting only affects TX from the balance.

All three families ship from the factory in SBI mode and require a
front-panel menu change to switch to xBPI. WZA's SBI default is
autoprint at 1200-7-O-1 (`+     0.00 g  \r\n`); MSE and BCE default to
SBI command/reply at their respective baud rates with no autoprint
enabled.

**MSE has two physical ports with separate protocol configuration:**
the 9-pin SUB-D peripheral port and the USB-B "PC-USB" port. The
peripheral port's protocol is settable via xBPI parameter `p35`
(see §10) — write + `0x47` SAVE_MENU + cold boot to apply. The
**PC-USB port's protocol is NOT exposed via the xBPI parameter table**
(indices 0-79 swept empirically 2026-04-25; only the front-panel
`Device → PC-USB → Dat.Rec.` menu can flip it). Consequence: the
package's `Balance.configure_protocol(...)` is host-side only on
PC-USB — it cannot rewrite the wire's protocol; the user must
flip the front-panel menu first. On the peripheral port, by
contrast, the package can both read and re-program p35.

### 2.2 SBN addressing

xBPI supports RS-485 multidrop with a **Sartorius Bus Node (SBN)** address.
On a point-to-point RS-232 link, both host and balance still include their
SBN in every host-to-balance frame:

- Host SBN = `0x01` (by convention)
- Balance SBN = set via opcode `0x72` (write) / read via `0x71`
  - Our MSE1203S reports `0x00` [SURE]
- The balance accepts `dst_sbn = 0x09` (its factory default) and responds
  regardless of its own SBN when directly connected. [LIKELY]

## 3. Frame format

### 3.1 Host → balance (TX)

```
  [len] [src_sbn=01] [dst_sbn=09] [opcode] [args...] [chk]
  \____/                                              \___/
  = bytes-after-len-byte (incl. chk)            = sum(all-prev-bytes) & 0xFF
```

- `len` = count of bytes that follow the length byte (i.e. `len + 1` = total
  frame size). [SURE]
- `chk` = `sum(bytes_0..N-1) & 0xFF` where N is the last index. [SURE]
- Default SBNs: `src=0x01`, `dst=0x09`. [SURE]

### 3.2 Balance → host (RX)

```
  [len] [marker=0x41] [subtype] [body...] [chk]
```

- `marker` is always `0x41`. [SURE]
- `subtype` classifies the payload shape (see §4). [SURE]
- `chk` = same formula as TX. [SURE]

### 3.3 Worked examples

```
Read SBN:              04 01 09 71 7f          len=4, op=0x71, chk=0x7F
Reply (subtype 0x21):  04 41 21 00 66          len=4, subtype=0x21, body=00

Read net weight:       04 01 09 1e 2c
Reply (measurement):   0b 41 48 bb a3 d7 0a 3d 30 82 45 07
                        len=11, subtype=0x48, 8-byte measurement body, chk=0x07
```

## 4. Response subtype encoding

The `subtype` byte (byte[2] of a device reply) follows a **type-length
convention**:

```
  high nibble = type class
  low nibble  = body length (for types 0..4); for type 5, length = 16 + low
```

| Subtype | Family | Body length | Meaning |
|---|---|---|---|
| `0x00` | `ack` | 0 (usually) | Generic ACK; success with no data. Exception: `0xBC` returns subtype `0x00` with a variable-length body. |
| `0x01` | `error` | 1 | Error code byte follows (see §6) |
| `0x12` | `bargraph`/nested | 2 (or variable) | u16 BE bar-graph value; sometimes TLV-nested data |
| `0x14` | `structured_u32` | 4 | u32 BE; seen on BCE `0x75` (raw load ADC) |
| `0x21` | `short_data` | 1 | u8 |
| `0x22` | `short_data` | 2 | Typically `[state][status]` |
| `0x24` | `short_data` | 4 | u32 BE or 2×u16 BE |
| `0x34` | `typed_float_alt` | 4 | float32 BE (no aux byte) |
| `0x35` | `typed_float` | 5 | `[float32 BE][aux byte]` — used by max/increment/temperature |
| `0x41` | `short_blob` | 1 | 1-byte blob; seen on BCE `0x7C` |
| `0x43` | `long_data` | 3 | Raw blob |
| `0x45` | `long_data` | 5 | Raw blob (e.g. factory number) |
| `0x48` | `measurement` | 8 or 17 | Measurement frame (short OR streaming-long) |
| `0x4A` | `long_data` | 10 | Raw blob (e.g. software version) |
| `0x50` | `long_data` | 16 | ASCII string, null-padded (e.g. "Sartorius") |
| `0x51` | `long_data` | 17 | e.g. cal record at 0xB9 |
| `0x54` | `long_data` | 20 | ASCII string (e.g. model number) |

## 5. TLV argument/value encoding

Where the WZG manual documents an opcode taking a plain single-byte arg,
the Cubis MSE **requires the arg wrapped as a Type-Length-Value (TLV)
record**:

```
  <opcode>  [tag]  [value bytes...]
```

The TLV **tag** encodes the value's size in its low nibble:

| Tag | Value size | Meaning |
|---|---|---|
| `0x11` | 1 byte | u8 (rarely used as request arg) |
| `0x12` | 2 bytes | u16 BE |
| `0x14` | 4 bytes | u32 BE |
| `0x21` | 1 byte | **u8 — most common request-arg wrapper** |
| `0x22` | 2 bytes | u16 BE (seen in response bodies) |
| `0x24` | 4 bytes | u32 BE (seen in response bodies) |

### 5.1 Worked examples

```
WZG form:         0x0C 00        "Read max, area 0"  — ERRORS on Cubis
Cubis form:       0x0C 21 00     "Read max, area 0"  — returns 1200.0 g ✓

WZG form:         0x1E 01        "Read net weight, 10× resolution"  — ERRORS
Cubis form:       0x1E 21 01     "Read net weight, 10× resolution"  — works
```

### 5.2 Multi-TLV responses

Response bodies may contain multiple TLVs concatenated. Example — `0x55`
(read parameter table) at index 0:

```
TX:  06 01 09 55 21 00 86
RX:  06 41 21 02 21 04 8f
     └─┘ ├──┘ ├──────┐├──┐└─┘
     len mkr  (byte [2] is
              subtype 0x21, also
              the first TLV's tag)
              └─ TLV1: 0x21 0x02 ─┘└─ TLV2: 0x21 0x04 ─┘
```

So the parameter-table entry at idx 0 has `(current=0x02, max=0x04)`.

### 5.3 `parse_tlv_sequence` helper

The Python helper `sartoriuslib.protocol.xbpi.parse_tlv_sequence(body)` walks
a bytes object and yields `(tag, value_bytes)` tuples. To parse a multi-TLV
response body like above, prepend the subtype byte:

```python
f = parse_frame(raw)
tlvs = parse_tlv_sequence(bytes([f.subtype]) + f.body)
```

## 6. Error codes

When the balance returns subtype `0x01`, the single body byte is an error
code:

| Code | Meaning |
|---|---|
| `0x03` | Value out of range (TLV value unacceptable) |
| `0x04` | Unknown opcode (not implemented by this balance) |
| `0x06` | Operation not applicable (e.g., abort with nothing to abort) |
| `0x07` | Invalid or missing args (wrong form, or missing required TLV) |
| `0x10` | Index out of range (arg well-formed but index exceeds slot count) |
| `0x11` | Unknown meaning; returned by `0x67` on BCE for a no-args call. Adjacent to `0x10` so possibly a second index-range error variant. |

The distinction between `0x04` (no such command) and `0x07` (bad args) is
useful for discovery — it identifies opcodes that EXIST but need the right
arg form. All codes above are reported identically across MSE, WZA, and
BCE (except `0x11`, observed only on BCE).

## 7. Opcode reference

Opcode inventory for the Cubis MSE1203S. Annotations:

- `[WZG]` — documented in the WZG OEM manual.
- `[OBS]` — observed on our unit.
- `[SURE]` / `[LIKELY]` / `[GUESS]` / `[UNKNOWN]` — semantic confidence.

### 7.1 Device information

| Op | Name | Arg | Confidence | Response | Notes |
|---|---|---|---|---|---|
| `0x00` | read_software_version | — | [WZG+OBS][SURE] | subtype 0x4A, 10b packed | Our unit: `00 39 21 00 39 01 39 01 00 01` |
| `0x01` | read_factory_number | — | [WZG+OBS][SURE] | subtype 0x45, 5b | Our unit: `00 31 80 11 65` |
| `0x02` | read_weigh_cell_model | — | [WZG+OBS][SURE] | subtype 0x54, 20b ASCII | Our unit: `"MSE1203S-100-DR"` |
| `0x03` | read_user_id | — | [WZG+OBS][UNKNOWN] | subtype 0x48, zeroed 8b body | Returns a zeroed measurement-format frame regardless of args — the Cubis user-ID storage has moved elsewhere and is not exposed by this opcode |
| `0x04` | write_user_id | ? | [WZG] | — | SIDE-EFFECTING, skip |
| `0x05` | read_oem_text | — | [WZG+OBS][SURE] | subtype 0x50, 16b ASCII | Our unit: `"Sartorius"` |
| `0x07` | read_manufacturer | — | [WZG+OBS][SURE] | subtype 0x50, 16b ASCII | Our unit: `"Sartorius"` |
| `0x08` | undoc_08 | — | [OBS][LIKELY] | subtype 0x43, body `5b 67 df` (= 5,990,367) | Factory-burned constant (firmware-build ID, ROM checksum, or hardware-variant token). Stable across cold boots, warm resets, and every tested state. Accepts any arg form; always returns the same 3 bytes. |
| `0x09` | undoc_09 | ? | [OBS][UNKNOWN] | Varies; arg `0x43` → ACK | Only arg 0x43 returns anything; benign (no observed side effect) |
| `0x0A` | read_configuration_data | — | [WZG+OBS][SURE] | subtype 0x48, 8b | Our unit: `01 01 00 00 00 03 02 01` |
| `0x0F` | read_balance_info | — | [WZG+OBS][SURE] | subtype 0x24, 4b = 2×u16 | Our unit: `02d8 01a4` (728, 420). Bit 6 of the low byte flips between `0x01E4` (xBPI sees weighing) and `0x01A4` (xBPI sees app mode) — a weighing/app flag mirrored by the xBPI-visible 0x62 register. |

### 7.2 Metrological constants

All require TLV-21 args on Cubis. Arg = "area index" 0..3; our unit reports
same value for all areas (single weighing range).

| Op | Name | Confidence | Our unit's value |
|---|---|---|---|
| `0x0B` | read_threshold_0b | [OBS][LIKELY] | float32 = 0.1 — "coarse" stability/zero-tracking threshold (10× increment) |
| `0x0C` | read_max | [WZG+OBS][SURE] | **1200.0 g** ✓ matches spec |
| `0x0D` | read_increment_d | [WZG+OBS][SURE] | **0.001 g = 1 mg** ✓ matches spec; tracks front-panel display-accuracy setting (see p08) |
| `0x0E` | read_threshold_0e | [OBS][LIKELY] | float32 = 0.01 — "fine" threshold (10× increment) |

All return subtype 0x35 (typed_float, 5-byte body `[float32][aux]`).

**Coupled triple:** `0x0B`, `0x0D`, and `0x0E` scale together. Changing the
display-accuracy menu option to "-1 digit" moved all three by exactly 10×
in one step (0x0D: 0.001→0.01, 0x0B: 0.1→1.0, 0x0E: 0.01→0.1). `0x0D` is
the canonical **live increment**; `0x0B` and `0x0E` are fixed ratios above
it — likely "stability threshold in display units" and "minimum zero-tracking
step", pegged to 10× and 100× the increment. [LIKELY]

### 7.3 Tare and zeroing

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x13` | delete_tare | [WZG] | Sub-args: 00=main, 01=appl 1, 02=appl 2. Side-effecting. |
| `0x14` | initiate_combined_tare | [WZG+OBS][SURE] | **Canonical TARE command.** No args. Reply: `03 41 00 44` ACK, synchronous. |
| `0x15` | abort_combined_tare | [WZG+OBS][SURE] | Returns err 0x06 if no tare active. |
| `0x16` | initiate_tare | [WZG] | Alternate tare variant. Side-effecting. |
| `0x17` | abort_tare | [WZG+OBS][SURE] | Returns err 0x06 if no tare active, ACK if one is. Incidentally reveals which app modes auto-tare on entry: count, net_totl, calc, density do; weighing, unit, percent, total, animal_w don't. |
| `0x18` | initiate_zeroing | [WZG+OBS][SURE] | Starts a zeroing operation. ACK reply. |
| `0x19` | abort_zeroing | [WZG+OBS][SURE] | Test vehicle for error 0x06. |
| `0x1A` | initiate_appl_tare | [WZG] | Sub-arg: 01=appl 1, 02=appl 2. |
| `0x1B` | abort_appl_tare | [WZG] | — |
| `0x1D` | write_appl_tare | [WZG] | Preset tare values. Side-effecting. |

### 7.4 Weight and gross-weight reads

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x1E` | read_net_weight_std | [WZG+OBS][SURE] | No-arg returns short measurement. TLV `21 00` = std, `21 01` = 10×, `21 02` = 100× resolution per WZG. |
| `0x1F` | read_net_weight_hires | [WZG+OBS][SURE] | Requires TLV arg. Res 01 = 0.1 mg, res 02 = 0.01 mg (sub-mg readings exposed). `unit_raw` byte[5] changes (0x30/0x40/0x50) to signal precision. |
| `0x1C` | read_appl_tare | [WZG+OBS][SURE] | Read stored application tare. TLV args: `21 01` / `21 02`. On our unit returns same as 0x22 (no appl tare set). |
| `0x20` | read_gross_weight_std | [WZG+OBS][SURE] | **Live** gross weight. Verified: net + tare = gross (199.99 + 330.53 = 530.52 ✓) with 200 g test. |
| `0x21` | read_gross_weight_hires | [WZG+OBS][SURE] | Same as 0x20 but requires TLV arg. |
| `0x22` | read_tare | [WZG+OBS][SURE] | Stored main tare reference. NOT live. |
| `0x23` | read_tare_alias | [OBS][LIKELY] | Returns same as 0x22 — probably an alias. |

### 7.5 Filter / weighing-mode configuration

Filter and sampling-rate are **independent axes**:

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x26` | read_weighing_mode | [WZG+OBS][SURE] | Returns 1/2/3/4 = very stable / stable / unstable / very unstable. Equivalent to param-table p01 (filter/ambient mode). |
| `0x2C` | write_weighing_mode | [WZG+OBS][SURE] | Sets filter preset. ACK reply. Side-effecting. |
| `0x57` | read_cycle_time | [WZG+OBS][SURE] | subtype 0x24 = u32 BE milliseconds. Tracks p01 (filter mode) with a 50/100/200/400 ms doubling curve across p01 values 1-4. See §14.4. |

### 7.6 Status queries

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x30` | read_balance_status_block | [WZG+OBS][SURE] | Returns subtype 0x48 with 8-byte status block (format in §8.2). |
| `0x32` | read_balance_status | [WZG+OBS][SURE] | Returns subtype 0x22: `[state][status]` 2 bytes (see §8.2 for bit meanings). |
| `0x35` | read_time_stamp | [WZG+OBS][SURE] | 1-byte tick counter. On MSE, increments at the measurement rate. On WZA ticks at ~50 Hz independently of cycle_time. |
| `0x36` | read_on_off_status | [WZG+OBS][SURE] | 1 byte: `01` = on. |
| `0x2E` | read_slot_by_index | [OBS][LIKELY] | Takes TLV-21 index. Valid range 0–2; index 3+ → error 0x10. Returns multi-TLV body `[21 <status-byte>][48 <8-byte measurement>]`. On our unit all slots return value 0.0; slots 0, 1 have status=0xff (uninitialized), slot 2 has status=0x02 (configured?). Likely three check-weighing limits (upper/target/lower) or three stored reference masses. |
| `0x2F` | read_gross_bargraph | [WZG+OBS][SURE] | subtype 0x12 u16 BE. Scale 0..1000, 1000 = max capacity. Verified: 442 = 44.2% of 1200 g @ 530 g load. |

### 7.7 Calibration / adjustment

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x28` | start_adjustment | [WZG] | TLV arg selects type (112..123 — internal cal, external cal, linearization, etc.). SIDE-EFFECTING. |
| `0x29` | abort_adjustment | [WZG+OBS][SURE] | Returns err 0x06 when no adjustment is running. |
| `0x76` | read_temperature_sensors | [WZG+OBS][SURE] | See §9 — 4-sensor array. |
| `0x78` | read_adjustment_unit | [WZG] | Returns current cal unit. |
| `0x79` | write_adjustment_unit | [WZG] | 02=g, 03=kg, 04=ct. Side-effecting. |

### 7.8 Presettings and menus

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x46` | read_menu_from_eeprom | [WZG+OBS][SURE] | Reload saved menu settings from EEPROM. ACK. |
| `0x47` | save_menu_to_eeprom | [WZG+OBS][SURE] | Persist current menu to EEPROM. ACK. |
| `0x4A` | read_user_memory_isi | [WZG] | ISI scratch space. Not probed. |
| `0x4B` | write_user_memory_isi | [WZG] | Not probed. |
| `0x54` | read_stop_flags | [WZG+OBS][SURE] | subtype 0x24 u32. Our unit: `0f 00 00 00`. |
| `0x55` | read_parameter_table | [WZG+OBS][SURE] | TLV `21 <idx>` → returns 2 u8 TLVs `(current, max)`. See §10. |
| `0x56` | write_parameter_table | [WZG] | TLV `21 <idx> 21 <val>`. SIDE-EFFECTING. |

### 7.9 Basic / system

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x40` | reconfiguration | [WZG] | SIDE-EFFECTING. |
| `0x41` | reset_temporary_errors | [WZG] | Side-effecting. |
| `0x58` | initiate_reset | [WZG] | Warm/cold reset. DANGEROUS. |

### 7.10 Data interface

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x5C` | set_baud_rate | [WZG] | 00=9600, 01=19200, 02=38400, 03=57600. DANGEROUS (changes serial comms mid-flight). Note: uses different encoding than param-table p31 / p63. |
| `0x71` | read_sbn_address | [WZG+OBS][SURE] | Our unit: 0x00. |
| `0x72` | write_sbn_address | [WZG] | DANGEROUS. |

### 7.11 Extended opcodes (not in WZG)

Opcodes in this section are not in the WZG OEM manual but are
implemented in Sartorius firmware. Availability varies by family:

- Most are present on MSE (Cubis); a subset (`0xBA`, `0xB9`, `0xAA`)
  are also present on BCE3202.
- WZA lacks most of these entirely (`err_04 unknown_opcode`).
- Truly Cubis-exclusive: `0x62`, `0x59`/`0x5A` (their side effects
  on `0x62`), and the `0x80`-set state-byte encoding in status
  blocks (§8.2).

See §14.2 for the cross-family presence matrix.


#### ACK-only opcodes (resolved)

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x59` | clear_xbpi_app_flag | [OBS][SURE] | ACK. When `0x62` was 3, clears it to 0 and bumps `0xBA` by 1. When `0x62` already 0, no-op ACK. Does NOT change the actual UI/functional app mode — the balance display keeps showing its app. Confirms `0x62` is an xBPI-visible flag separate from real app state. |
| `0x5A` | alias_of_0x59 | [OBS][SURE] | Alias of 0x59. Identical behavior verified by isolated probe. |
| `0x67` | nop | [OBS][SURE] | Pure NOP. ACK only, no side effect. |
| `0xFF` | nop | [OBS][SURE] | Pure NOP. ACK only, no side effect. |

#### Data-returning opcodes

| Op | Name | Confidence | Notes |
|---|---|---|---|
| `0x25` | undoc_25_alias_p25 | [OBS][SURE] | u8 register — direct-read alias of parameter-table **p25**. Both always report identical values across all observed state transitions (warm drift, cold boot, direct probes). An alias scan across 23 captures identified this as the only runtime-varying opcode↔param alias on this unit; the known `0x26`↔p01 alias could not be variation-verified because p01 didn't change in captures. Args ignored. |
| `0x31` | undoc_31 | [OBS][UNKNOWN] | short_data 2-byte `0x00 81`. Args ignored. |
| `0x33` | undoc_33 | [OBS][UNKNOWN] | Always returns 2-byte `10 00` — same as the constant in measurement status blocks. Args ignored. |
| `0x48` | read_stored_ref_maybe | [OBS][GUESS] | Returns a measurement-format frame with value 1000.0 g. Likely a stored reference/target mass. |
| `0x50` | undoc_50_gated_by_62 | [OBS][LIKELY] | u8 register, TLV-21 addressed. idx 0 returns `u8=3` only when `0x62` is in certain non-zero states (valid for `0x62 ∈ {3, 8}`, errs for `0x62 ∈ {0, 5}`). idx 1+ always errs on this unit. Behaves as a shadow of `0x62` with its own gating predicate; the value 3 carries no distinct information from `0x62`. |
| `0x51` | undoc_51_slotted_weights | [OBS][LIKELY] | 4-slot measurement-shaped register (TLV-21, idx 0–3; idx 4+ → err 0x03). Cold-boot default holds the arithmetic progression `{0, 1000, 2000, 3000} g` — a firmware-wide default, independent of actual capacity (3000 g exceeds this unit's 1200 g max). Likely a multi-point cal-setpoint or check-weighing-target table. The unit-byte (byte 6) flips between `0x42` (proper `g`) and `0x62` (`base=0x22`) when either (a) a physical load is on the pan, or (b) `0x62` is at value 3. Either condition alone suffices. Float values stay constant across the unit-byte flip. `0xBD` drives the same flip synthetically. |
| `0x62` | xbpi_menu_activity_state | [OBS][SURE] | Multi-valued time-decaying register. Observed values `{0, 2, 3, 5, 8, 16}`. Cold-boot value = 0. Non-zero values are transient — decay to 0 within ~5 s of inactivity. The specific non-zero value appears to encode event type (menu-entry / menu-exit / mode-activation) but the mapping is not yet decoded. Cleared synchronously by `0x59`/`0x5A`. Not a reliable "which app is selected" signal: captures of the same UI state can show different values depending on how long ago the last menu event fired. |
| `0x7E` | undoc_7e | [OBS][UNKNOWN] | Returns subtype 0x12 (bargraph family) with u16 BE body. Reads 0 regardless of pan load — not a net bargraph and not a gross alias. Args ignored. Possibly unused on this balance, or reserved for a mode we have not triggered (check-weighing deviation, percent-mode bargraph, etc.). |
| `0xAA` | undoc_aa | [OBS][UNKNOWN] | Accepts TLV `21 00/01` only. Returns 10-byte body decoding as two `0x14` TLVs: u32 values `0x21120097` and `0x0095b744`. Meaning unknown. |
| `0xB5` | undoc_b5 | [OBS][UNKNOWN] | 2-slot u8 register (TLV-21, idx 2+ → err 0x03). Cold-boot default: both slots read 24. idx 1 drifts at runtime (observed 0 and 2 alongside p25/p56/p58 class movement) and resettles to 24 on its own after some events (e.g., overload/underload recovery) — not strictly monotonic, not persistent. Slot 0 is stable at 24 across all observed states. |
| `0xB7` | undoc_b7 | [OBS][UNKNOWN] | 2-slot register (TLV-21, idx 0/1 both → typed_float_alt 1.5; idx 2+ → err 0x03). Both slots return the same value; could be a setting + mirror pair or a two-axis reading that happens to be equal. Value 1.5 may be a gain or multiplier constant. |
| `0xBD` | undoc_bd_trigger | [OBS][SURE] | **Mode-trigger, not a pure read.** 2-slot u32 register (subtype 0x44, 4-byte body): idx 0 → 180, idx 1 → 0; idx 2 → err 0x03. A call briefly puts the balance into "active measurement" mode, causing the `0x51` slot unit-byte to flip from `0x42` (`g`) to `0x62` (`base=0x22`). Two calls sandwich a single measurement cycle so the unit-byte appears to toggle back. The first call also populates `0xBB` from pristine `ff ff ff` to `00 e8 00` (sticky across subsequent calls). Value 180 is stable across repeated calls. Kept in the wide-sweep SKIP list because of the mode-trigger side effect. |
| `0xBE` | undoc_be_multiblock | [OBS][UNKNOWN] | Returns long_data subtype 0x4A with a body of N repeated `[4a] [10-byte sw-version-like payload]` blocks — same shape as `0xBC`'s module list but a different block count. Args ignored. Likely a separate firmware/module enumeration. |
| `0xB9` | read_last_cal_record | [OBS][SURE] | Returns 17-byte body = cal-temperature + 13-byte cal metadata (subtype 0x51). Temperature matches sensor 0's live reading at the cal moment to LSB precision. Ignores all TLV args. See §7.12 for byte-level layout. |
| `0xBA` | config_generation_counter | [OBS][SURE] | 1-byte counter that increments on most runtime-config changes. Useful for cache invalidation. Not every param write bumps it — boot-time flags like p13 (tare-on-power-on) and p50 (acoustic) change without bumping BA. BA tracks runtime weighing-pipeline config; UI-preference changes live in a separate persistence class. Resets to 1 on a full cold boot. Warm-reset behaviour is less clearly characterised (BA has drifted upward during such events, but the reset itself vs. incidental activity wasn't isolated). |
| `0xBB` | undoc_bb | [OBS][LIKELY] | 3-byte body. Args ignored. Multi-valued register tracking some session/state history. Observed states: `ff ff ff` (pristine post-cold-boot), `00 e8 00` (after first `0xBD` call; sticky), `4f d5 00` (after some menu/overload sequences), `ff 85 01` (separate historical session). At least 4 distinct states — more than a single-bit event flag. |
| `0xBC` | read_module_list_likely | [OBS][LIKELY] | Returns subtype `0x00` with body `22 XX XX [11-byte-block...]×N`. Each 11-byte block matches the `0x00` sw-version response format (`4a 00 39 21 00 39 01 39 01 00 01`). Prefix `0x22` is likely a block-count tag; next 2 bytes are a freshness counter (varies read-to-read); N varies across sessions (observed 2, 3, 4). Likely a firmware module/plugin list — each block = one installed component's version. Rejects TLV args with err 0x07. |

### 7.12 The cal record (0xB9) byte layout

17-byte body, subtype `0x51`:

| Bytes | Field | Meaning | Confidence |
|---|---|---|---|
| `[0..3]` | cal_temperature | float32 BE — ambient temperature at the moment the last cal ran; matches sensor 0's live reading to LSB | [SURE] |
| `[4..12]` | cal_signature | 9 bytes identifying which cal-type was run. Identical across multiple internal cals on our unit: `01 09 00 04 02 06 00 07 01` | [LIKELY] |
| `[13..15]` | cal_counters | Counters that increment per cal event. On our unit [14] and [15] each incremented +1 per cal; [13] incremented +2 per cal (possibly auto-isoCAL piggybacking, or internal adjustment iterations counted separately). | [LIKELY] |
| `[16]` | padding | `0x00` terminator | [LIKELY] |

If the balance has never had a cal recorded in the current RAM buffer, the
metadata bytes `[4..16]` are all zero. The temperature prefix `[0..3]` may
still hold the last-cal reading (kept in a separable field — see below).

**Storage architecture (three tiers, all with empirical support):**

1. **Cal coefficients** live in EEPROM-backed RAM loaded by the boot path
   into the measurement pipeline. Not exposed via `0xB9`. Weighing reads
   remain accurate even when `0xB9` is empty, confirming this layer is
   independent of the visible buffer.
2. **The `0xB9` cal-record RAM buffer** (what this opcode reads) is
   populated on cal events and cleared on every cold boot (plug-pull
   verified: the full 17 bytes read as zeros immediately after boot).
3. **Separable fields within the buffer.** Certain undocumented writes
   (`0x2D`, `0x53`, `0x61`, `0x6B`, `0xB6`) can zero `[4..16]` while
   leaving `[0..3]` intact at the last-cal temperature. This proves
   cal-metadata and cal-temperature occupy separate backing locations
   that alias into the same readable record.

## 8. Measurement frame decoding

### 8.1 Short measurement (8-byte body)

Returned by `0x1E`, `0x1F`, `0x20`, `0x21`, `0x1C`, `0x22`, `0x23` —
subtype `0x48` with 8-byte body:

```
byte  field                  type         meaning
[0-3] value                  float32 BE   displayed value in reported units
[4]   aux                    u8           usually last byte of the float
                                          — extra-precision echo [LIKELY]
[5-6] unit                   2 bytes      see §8.4
[7]   flags                  u8           bit 0x40 = stable; low nibble varies
                                          by opcode/mode (NOT persistent)
```

**Overload / underload** is signalled simultaneously in three places:

1. The measurement-body sentinel `bytes[0..4] == 7f ff ff ff ff`.
2. The status block's state byte: `0x82` overload, `0x84` underload.
3. Status byte bit `0x08` clears briefly on the *recovery transition* out
   of the off-scale state (sub-second window) but is *unchanged* during
   the off-scale state itself — see §8.2.

[SURE] Detect the state itself by watching the sentinel + state byte; the
bit-0x08 clearing window is a secondary signal useful for knowing when the
ADC has fully re-settled after recovery.

### 8.2 Status block (also 8-byte body, subtype 0x48, returned by 0x30)

```
byte  field                  type         meaning
[0]   aux_flag                u8          Varies by family (see below).
                                          0x00 on WZA/BCE; observed 0x08
                                          on MSE under some states.
[1-2] marker                  2 bytes     always `00 81`
[3]   state                   u8          family-dependent — see below
[4]   status                  u8          family-dependent — see below
[5-6] marker_suffix           2 bytes     always `10 00`
[7]   seq                     u8          monotonic measurement counter.
                                          On MSE, increments at the
                                          measurement rate. On WZA, the
                                          tick counter (0x35) ticks at
                                          ~50 Hz independently of the
                                          measurement rate, so the `seq`
                                          here may track a different
                                          clock on simpler balances.
```

**State / status byte encoding is family-dependent.** Two patterns observed:

| Condition | Cubis (MSE) state | Cubis (MSE) status | non-Cubis (WZA, BCE) state | non-Cubis status |
|---|---|---|---|---|
| stable | `0x88` | `0x18` (ADC-trust + isoCAL-due off) | `0x08` | `0x20` |
| unstable | `0x80` | `0x08` | `0x00` | `0x00` |
| overload | `0x82` | — | — (not captured) | — |
| underload | `0x84` | — | — (not captured) | — |

Interpretation:
- On Cubis, bit `0x80` in state is the "measurement valid" base and
  bit `0x08` on top marks stable. Status-bit `0x10` = isoCAL
  attention/due; bit `0x08` = ADC-trust.
- On non-Cubis (WZA, BCE), the state byte never sets `0x80`; bit
  `0x08` alone marks stable. Status bit `0x20` appears to carry a
  "result-ready / settled" signal.
- Bit `0x08` is the stable indicator on both families — just placed
  in different bytes.

**Cross-family-portable stable detection**: read the measurement-frame
`flags` byte's `0x40` bit (§8.1). That bit is universal; the
status-block state byte is not.

Byte [0] behaves as an aux-flag: `0x00` on WZA and BCE across all
observed states, but observed as `0x08` on MSE in one capture session
while state cycled 0x80/0x88 normally. Most parsimonious interpretation
is that byte[0] mirrors or extends the MSE-only ADC-trust bit; needs
more in-state captures to pin down. Parsers should not assume byte[0]
is zero.

Distinguishing short-measurement from status-block (both subtype 0x48,
both 8 bytes): status-block bytes [1..2] are reliably `00 81`, status-block
bytes [5..6] are reliably `10 00`. Short-measurement frames rarely
match those patterns.

### 8.3 Long streaming measurement (17-byte body, subtype 0x48)

Returned by `0x1E 09 30` — a short measurement concatenated with a status
block via a single-byte delimiter:

```
[0..7]  short measurement (8 bytes, as §8.1)
[8]     delimiter (always 0x48 — same as subtype!)
[9..16] status block (8 bytes, as §8.2)
```

The length byte of the overall frame is 0x14 (20), making the total frame
21 bytes. [SURE]

### 8.4 Unit encoding (bytes [5..6] of measurement body)

**Byte [5] high nibble = number of decimal places displayed.**

This is the universal rule, confirmed across MSE, WZA, and BCE for g,
kg, and mg displays at standard, 10×-hires (`0x1F 21 01`), and 100×-hires
(`0x1F 21 02`) resolutions. Observed values `0x00` (0 dec) through
`0x70` (7 dec). The increment opcode `0x0D` reports its value in the
current display unit, so the decimal count corresponds to displayed
resolution, not a fixed unit.

Byte [6] = `sign_bits | base_unit_id`:

| Top 2 bits of byte [6] | Sign meaning |
|---|---|
| `0x00` (00) | exactly zero |
| `0x40` (01) | positive |
| `0x80` (10) | negative |

Base-unit IDs (low 6 bits of byte [6]):

| byte[6] low-6 | Unit |
|---|---|
| `0x02` | g |
| `0x03` | kg |
| `0x0D` | mg |
| `0x17` | N |

So a complete decode is:

```
decimals = byte[5] >> 4
sign     = byte[6] & 0xC0   # 0x00 zero, 0x40 pos, 0x80 neg
unit_id  = byte[6] & 0x3F
```

**mg low-nibble anomaly**: on WZA at display=mg, byte[5] comes back with
low-nibble `3` (values `0x03`, `0x13`, `0x23` at std/10×/100× hires).
On MSE at display=mg (1 mg resolution, 0 decimals displayed) low-nibble
is 0. The anomaly correlates with WZA's 10 mg display resolution — a
balance that can't show 1 mg granularity in mg mode — but the exact
semantic isn't decoded. BCE's mg behaviour hasn't been captured. The
practical decoder rule is: **use `byte[5] >> 4` for decimals; treat the
low nibble as advisory/quirky**.

## 9. Temperature sensors (opcode 0x76)

Live multi-sensor temperature read. TLV arg selects the sensor index.
Installed sensors are family-dependent:

| Arg | Sensor role | MSE1203S | WZA8202-N | BCE3202-1S |
|---|---|---|---|---|
| `21 00` | ambient / weighing zone | ~26–28 °C | 20.85 °C | 20.35 °C |
| `21 01` | second ambient / internal | ~26–28 °C | **sentinel** | **sentinel** |
| `21 02` | (reserved) | sentinel | sentinel | sentinel |
| `21 03` | electronics / motor | ~37 °C | 30.15 °C | **sentinel** |
| `21 04+` | — | err 0x04 | err 0x04 | err 0x04 |

Installed sensors → 3 (MSE) / 2 (WZA) / 1 (BCE); not-installed slots
return the sentinel `7f ff ff ff`. A single-sensor basic balance is
expected for the simpler product classes.

Confirmed live (not stored) by observing LSB jitter across repeated
reads on every balance — sensor-0 readings drift by tens of µ°C per
sample on empty pans.

Return format: subtype 0x35 (typed_float, 5-byte body). Temperature is
float32 °C.

On MSE, sensor 0's live reading is what the cal record (0xB9) snapshots
into its first 4 bytes at the moment of a calibration event.

## 10. Parameter table (opcode 0x55)

Parameter-table size is family-dependent:

| Balance | Valid indices |
|---|---|
| MSE1203S | 70 (ranges 0-18, 24-25, 27-45, 50-79) |
| BCE3202 | 70 (same ranges as MSE) |
| WZA8202-N | 8 (indices 1-7, 9 only) |

MSE and BCE share the full 70-index parameter table; WZA's 8-index
subset is the outlier. The subsections below document MSE+BCE index
semantics; WZA exposes the first 8-9 indices with the same meanings
but narrower `max` values on a few (e.g. `max=2` on p05 vs MSE's 3;
`max=23` on p07 vs MSE's 24; `max=5` on p09 vs MSE's 18) — consistent
with WZA being a simpler balance with fewer available options.

The 70 indices valid on MSE+BCE are organised in contiguous ranges:

```
0..18    (19 params)
24..25   (2 params)
27..45   (19 params)
50..79   (30 params)
```

Each read returns two u8 TLVs: `(current_value, max_value)`. Writing is done
via `0x56` with TLVs `(idx, val)` — the frame shape is observed in legacy
captures but we have not exercised writes.

**Address space is exactly 80 u8 indices, TLV-21 only.** `0x55` rejects
wider-tag reads (`22 XX XX`, `24 XX XX XX XX`, alt tags `11`/`12`/`14`)
with err 0x07, and u8 indices 80..255 return err 0x03. There is no hidden
extended table reachable via wider TLV addressing. The 19 unmapped slots
on this unit are either firmware-reserved-but-unused or live behind
service-menu state that xBPI alone cannot trigger.

The `max` field is **firmware-family-wide**, not product-line-wide. On
MSE+BCE many params show `max=18` or `max=24` even though only a
handful of values are accepted — gaps are reserved for balances within
the family that expose more menu options. WZA has narrower `max`
values on a few indices, reflecting its simpler feature set.

### 10.1 Identified parameter mappings

48 of 70 indices mapped. Sorted by index:

| idx | Meaning | Values seen | max | Confidence |
|---|---|---|---|---|
| 1 | filter/ambient mode | 1=very stable, 2=stable, 3=unstable, 4=very unstable | 4 | [SURE] — equivalent to opcode 0x26 |
| 2 | application filter | 1=final_rd, 2=filling, 3=reduc, 4=off | 4 | [SURE] — fully mapped; orthogonal to p01 and p03 |
| 3 | stability range | 1=max_acc, 2=v_acc, 3=acc, 4=fast, 5=v_fast, 6=max_fast | 6 | [SURE] — fully mapped; orthogonal to p01 |
| 4 | stability delay | 1=no, 2=short, 3=average, 4=long | 4 | [SURE] — fully mapped |
| 5 | tare behavior | 1=wo_stab, 2=w_stab, 3=at_stab | 3 | [SURE] — fully mapped |
| 6 | auto-zero tracking | 1=on, 2=off | 2 | [SURE] — fully mapped |
| 7 | display unit | 1=userdef, 2=g, 3=kg, 4=ct, 5=lb, 6=oz, 7=troy_oz, 8=hktael, 9=sngtael, 10=twntael, 11=grains, 12=pennywt, 13=mg, 14=ptplb, 15=chntael, 16=mommes, 17=austrct, 18=tola, 19=baht, 20=mesgal, 21=tons, 22=lb_oz, 23=newton, 24=µg | 24 | [SURE] — fully mapped. `userdef=1` is a user-defined multiplier (defaults to ×1 ≈ grams); the multiplier + label live in a separate register not yet located |
| 8 | display-accuracy mode | 1=default, 2=lponoff, 6=div1, 7=-1 digit | 18 | [SURE] — fully mapped for MSE1203S (only 4 menu options on this unit; gaps reserved for other balances). Value 7 drops 0x0D increment by 10× |
| 9 | cal button assignment | 1=cal_ext, 3=e_cal_usr, 4=cal_int, 8=set_prel, 9=del_prel, 10=blocked, 12=select, 17=set_ext_w | 18 | [SURE] — 8 live values on MSE1203S; gaps reserved |
| 10 | cal/adjust mode | 1=adj, 2=cal_adj | 4 | [LIKELY] — "adjust only" vs "calibrate+adjust". Values 3/4 unexplored |
| 11 | zero range | 1=1pec (1%), 2=2pec (2%) | 5 | [LIKELY] — runtime auto-zero range. Values 3–5 likely 5%/10%/20% |
| 12 | init-zero range | 1=default, 2=2perc | 7 | [LIKELY] — boot-time auto-zero range, distinct from p11. Values 3–7 unexplored |
| 13 | tare-on-power-on | 1=on, 2=off | 3 | [SURE] — does NOT bump 0xBA (boot-time flag, persisted separately) |
| 14 | cycle rate | 1=normal, 2=highvar, 3=slow, 4=medium, 5=fast, 6=very_fast, 7=max | 7 | [LIKELY] — values fully enumerated. **p14 does NOT drive `0x57` cycle_time** — `0x57` is driven by p01, see §14.4. What p14 actually drives is open. The ms mapping next to each value is a front-panel label; its correspondence to actual sampling/cycle behaviour is unverified. |
| 15 | isoCAL mode | 1=off, 2=note, 3=on | 4 | [SURE] — value 4 reserved. This is the persistent isoCAL mode. Status byte bit 0x10 is **not** the enable flag; after triggering internal adjustment with `0x28 21 78`, p15 still read `3` while status changed `0x18 → 0x08` and the front-panel isoCAL symbol went from flashing to solid. Interpret bit 0x10 as isoCAL attention/due. |
| 16 | external-cal lock | 1=free, 2=locked | 2 | [SURE] — fully mapped |
| 25 | runtime-state register (class "boot-resets-to-1") | resets to `current=1, max=0` on cold boot; drifts upward during runtime (observed 1 → 2 in normal operation). Max oscillates 0 ↔ 5 paired with the reset. | 5 (warm) / 0 (cold) | [LIKELY] — part of a register class with **p56** and **p58** (all three reset to 1 on cold boot together). Opcode `0x25` is a direct-read alias. `0xB5` idx 1 co-moves with the same class. Semantic meaning (what drives the runtime drift) undetermined. |
| 31 | peripheral baud rate | 3=600, 4=1200, 5=2400, 6=4800, 7=9600, 8=19200, 9=38400, 10=57600, 11=115200 | 11 | [SURE] — 9 live values on MSE1203S; gaps 1/2 reserved (150, 300). Uses different encoding than opcode 0x5C |
| 32 | peripheral parity | 3=odd, 4=even, 5=none | 5 | [LIKELY] — gaps 1/2 reserved (mark/space) |
| 33 | peripheral stop bits | 1=1, 2=2 | 2 | [SURE] — fully mapped |
| 34 | peripheral handshake | 1=software, 2=hardware, 3=none | 3 | [SURE] — fully mapped |
| 35 | peripheral data-receiver protocol | 1=sbi, 2=xbpi, 4=rem_disp, 7=uni_print, 8=lab_print, 10=off | 10 | [SURE] — gaps 3/5/6/9 reserved. **Controls the 9-pin peripheral port only**, NOT the PC-USB port. Verified 2026-04-25: writing `p35=1` + `0x47` SAVE_MENU + soft and hard reboot did not flip PC-USB protocol; PC-USB stayed in xBPI throughout. The PC-USB protocol selector is not in the parameter table at all (indices 0-79 swept). Front-panel `Device → PC-USB → Dat.Rec.` is the only path. |
| 36 | SBI output mode | 1=ind_no, 2=ind_after, 3=ind_at, 4=auto_wo, 5=auto_w | 6 | [SURE] — (trigger × stability-filter) matrix. Value 6 unexplored. `auto_wo` = without stability filter (continuous, ~60 Hz on MSE1203S); `auto_w` = with stability filter (only stable values). **Does NOT bump 0xBA on write** (verified 2026-04-25); same persistence class as p13/p50. Setting `p36 ≥ 4` arms autoprint but only takes effect when the port is physically in SBI mode — switching protocol mode is what activates the stream. |
| 37 | auto-output stop | 1=off, 2=on | 2 | [SURE] — fully mapped; paired with p36 auto modes |
| 38 | auto-output cycle/interval | 1=each_val, 2=after_2 | 7 | [LIKELY] — values 3–7 likely after_5/10/20/50/100 cycles |
| 39 | SBI output format | 1=16_char, 2=22_char, 4=extra_line | 4 | [LIKELY] — value 3 unexplored |
| 40 | menu access mode | 1=can_edit, 2=rd_only | 2 | [SURE] — fully mapped (menu-lock flag) |
| 41 | external-key assignment | 1=print, 2=z_tare, 3=cal, 5=cf, 6=enter, 9=dr_shield, 10=ionizer, 11=appl, 12=asterisk | 12 | [SURE] — 9 live values on MSE1203S; gaps 4/7/8 reserved |
| 44 | cal unit | 1=g, 2=kg, 4=user_def | 4 | [SURE] — value 3 reserved (likely ct). Note: encoding ≠ opcode 0x79's args |
| 50 | acoustic signal (beep on stable) | 1=off, 2=on | 2 | [SURE] — note reversed polarity vs p13. Does NOT bump 0xBA |
| 53 | power-on mode | 1=off_on_sb, 2=off_on_ao, 3=on_sb, 4=auto_on | 5 | [SURE] — value 5 reserved |
| 56 | runtime-state (class "boot-resets-to-1") | observed 2 → 1 on cold boot | 5 | [LIKELY] — same register class as p25 and p58; all three reset to 1 on cold boot. Semantic meaning undetermined. |
| 57 | door-open resolution | 1=all, 2=reduc | 2 | [SURE] — display behavior when draft-shield door is open |
| 58 | runtime-state (class "boot-resets-to-1") | observed 3 → 1 on cold boot | 4 | [LIKELY] — same register class as p25 and p56; all three reset to 1 on cold boot. Semantic meaning undetermined. |
| 59 | level-check behavior | 1=off, 2=note_to, 3=err_msg | 3 | [SURE] — fully mapped. Paired with p60 |
| 60 | leveling mode | 1=key (manual), 2=auto | 2 | [SURE] — fully mapped. Paired with p59 |
| 61 | peripheral data bits | 1=7, 2=8 | 2 | [SURE] — fully mapped. Not adjacent to p31–p34 block |
| 63 | PC-USB baud rate | 4=1200, 5=2400, 6=4800, 7=9600, 8=19200, 9=38400, 10=57600 | 11 | [SURE] — same encoding as p31. `3=600` and `11=115200` inferred by pattern (not verified) |
| 64 | PC-USB parity | 3=odd, 4=even, 5=none | 5 | [SURE] — same encoding as p32. Gaps 1/2 reserved (mark/space) |
| 65 | PC-USB stop bits | 1=1, 2=2 | 2 | [SURE] — same encoding as p33 |
| 66 | PC-USB handshake | 1=software, 2=hardware, 3=none | 3 | [SURE] — same encoding as p34 |
| 67 | PC-USB data bits | 1=7, 2=8 | 2 | [SURE] — same encoding as p61 |
| 68 | auto-tare after output | 1=off, 2=on | 2 | [SURE] — fully mapped |
| 69 | print-param result mode | 1=man_no, 2=man_after, 3=man_at, 6=auto_lc | 6 | [LIKELY] — values 4/5 unexplored (likely auto_wo/auto_w per p36 pattern) |
| 70 | print-param output format | 1=16_char, 2=22_char, 4=extra_line | 4 | [LIKELY] — value 3 unexplored. Same encoding as p39 |
| 71 | print init/header | 1=off, 2=all, 3=main_par | 3 | [SURE] — fully mapped |
| 72 | GLP print mode | 1=off, 2=cal_adj, 3=always | 3 | [SURE] — fully mapped |
| 73 | tare-print | 1=off, 2=on | 2 | [SURE] — fully mapped |
| 74 | time format (print) | 1=24_hr, 2=12_hr | 2 | [SURE] — fully mapped |
| 75 | date format (print) | 1=dd_mmm_yy, 2=mmm_dd_yy | 2 | [SURE] — fully mapped |

**Unmapped indices (22):** p00, p17, p18, p24, p25 (unknown), p27–p30, p42, p43, p45, p51, p52, p54–p56, p58, p62, p76–p79.

These did not surface through any front-panel menu toggle we ran. Likely
homes: application-program sub-settings (per-app tolerances, nominal
weights, sample sizes, density reference liquids) or deeper service
menus not accessible without elevated credentials.

**Two distinct persistence classes** exist in the balance, distinguishable
by whether `0xBA` (config counter) bumps on write:

- **Weighing-pipeline config** (bumps 0xBA): filter mode, stability range,
  display accuracy, display unit, cal settings, communication settings —
  most of the table.
- **UI preferences / boot flags** (don't bump 0xBA): `p13` tare-on-power-on,
  `p36` SBI output mode (autoprint switch), `p50` acoustic signal. These
  writes bypass the main config-generation counter, likely because they're
  persisted to a separate EEPROM section that the host doesn't need to
  resynchronize on. (Cache-invalidation logic in `devices/session.py`
  must therefore not rely on `0xBA` to detect changes to these indices —
  invalidate explicitly on the write path instead.)

### 10.2 What is NOT in the parameter table

Several menu-accessible settings are **not reachable through the parameter
table or any other xBPI probe** we have found:

- **Application mode and its sub-settings** (weighing / counting / percent /
  net-total / totalizer / animal-weighing / calculation / density; plus the
  per-app sub-menus and mode-activation workflows like count's
  reference-quantity entry). Toggling through all 8 modes and exercising
  count-mode sub-settings (resolution, ref-upt) against the full 80-index
  param table, every structured read, and every no-args 0x00–0xFF sweep
  produces **zero byte differences between settings**. `0xBA` bumps on
  every commit, confirming the balance persists each change — the setting
  values are simply not exposed via xBPI. The only app-activity signals
  are `0xBA` bumps on commits and transient `0x62` / `0x50` / `0x51`
  movements that carry state-machine information but no setting values.

  Hypothesis: app mode and its sub-settings are front-panel/UI concepts
  that reconfigure display and post-processing but not the measurement
  stream xBPI exposes. [LIKELY] Consequence: the unmapped param-table
  indices (p27–p30, p42–p45, p51/p52/p54/p55, etc.) are NOT in app-menu
  territory — they must live elsewhere, most likely behind service-menu
  access (§13.3 C).

- **User ID** (free-entry 7-char alphanumeric, "Input" menu): setting it
  bumps `0xBA` but the string appears nowhere — not in the param table,
  not in any structured probe, not in the full 256-opcode no-args wide
  sweep. `0x03` (read_user_id per WZG) returns a zeroed measurement frame
  regardless of args on this unit. The ID is stored in a register that
  requires specific TLV-addressed reads not yet discovered. By extension,
  the "date", "time", "password", and "cal weight" free-entry fields in
  the Input menu are likely in the same category.

- **Display language** (English / Deutsch / français / italiano / español / …):
  bumps `0xBA` but produces zero readable diffs across any language pair,
  even with the wide sweep. Language is committed to balance state but
  unreachable via current xBPI probing. Likely stored in a display-controller
  chip on a separate bus from the main xBPI processor.

## 11. Known workflows

### 11.1 Tare

```
→ 04 01 09 14 22                    tare command
← 03 41 00 44                       ACK (synchronous completion)
```

No arguments, no polling. `0x14` is the real tare command.

### 11.2 Read weight

```
→ 04 01 09 1e 2c                    read net weight
← 0b 41 48 <8-byte short measurement> <chk>
```

### 11.3 Stream weight (long format)

```
→ 06 01 09 1e 09 30 67              read net with status block attached
← 14 41 48 <17-byte long measurement> <chk>
```

The balance does **not** stream continuously — each frame is one-shot per
request. To poll, re-send. The internal measurement rate is tied to p14
cycle rate (2.5 Hz at "normal" down to 100 Hz at "max"), so polling faster
than the measurement rate yields duplicate frames until the internal `seq`
counter advances.

### 11.4 Read all 4 temperature sensors

```
for sensor in (0, 1, 3):           # index 2 is "not installed" on MSE1203S
    send( 0x76 + [21, sensor] )
    parse reply as subtype 0x35 typed_float
```

### 11.5 Read last calibration record

```
→ 04 01 09 b9 be                    read_last_cal_record
← 14 41 51 <17-byte cal record> <chk>
```

Decode per §7.12. If all metadata bytes are zero, no cal has been recorded
since the last EEPROM clear.

## 12. Implementation notes

- The checksum (sum & 0xFF) is trivial; no CRC involved.
- Frame boundaries are unambiguous: read length byte, then `length` more
  bytes, then validate checksum.
- Expect to handle **unexpected frames** if the balance has continuous
  output enabled from SBI mode (seen once in this project — the balance
  auto-printed SBI-style ASCII lines even when set to xBPI because
  "autoprint" was left on from a prior SBI session). **Before opening
  a new session, clear the input buffer and consider probing `0x32`
  to validate xBPI is live.**
- **Wide-sweep self-poisoning:** when enumerating every opcode 0x00–0xFF
  to discover behavior, exclude at minimum the ACK-only opcodes that have
  side effects — `0x59` and `0x5A` clear the xBPI 0x62 flag and bump
  `0xBA`. Including them in a sweep corrupts subsequent diffs with phantom
  "0x62 changes" caused by the tool itself, not the system under test. The
  `sarto-diag sweep` command excludes them from its wide sweep by default.

- **TLV-args-unlock writes:** many opcodes that err on no-args silently
  ACK (executing a write) when given TLV-21 args. An arg-diverse wide
  sweep that only skips the known-destructive no-args set is unsafe — an
  undocumented write register can push the balance into an overload state.
  `sarto-diag argfuzz` is therefore always behind the explicit destructive
  acknowledgement gate. Always run arg-diverse sweeps behind a recent baseline
  so any residual state drift is visible on recovery.

- **Bootloader-drop risk on wide sweeps — SKIP list may be insufficient
  for unfamiliar balances:** a read-only no-args opcode sweep on a BCE3202
  (with an MSE-learned SKIP list plus the WZA-learned `0x9F` silent-ACK
  opcode) left the balance stuck in firmware bootloader mode ("B.LOADER"
  on front panel). Power-cycling recovered cleanly. The reply log showed
  no opcode that ACK'd unexpectedly, meaning at least one opcode in the
  err-0x07 invalid-args class on BCE is **not a pure read** — its no-args
  call has a delayed or state-sensitive side effect. **Do not run an
  undifferentiated opcode sweep on an unfamiliar balance.** Characterise
  each novel opcode with a single-shot probe bracketed by a baseline-diff
  (reads of known stable registers before and after) and a front-panel
  visual check. The cost of catching a bootloader drop early is low; the
  cost of a stuck unit without recovery is catastrophic.

- **xBPI signals vs real state:** `0x62` (and correlated `0x0F` bit 6)
  are menu-activity signals on the xBPI side only; they do not reliably
  track which app is running on the front panel. Use `0xBA` bumps as the
  canonical "something persisted" signal rather than inferring mode from
  any single register.

## 13. Outstanding items

### 13.1 Relationship to the WZG manual

This document is a **strict superset** of the WZG manual. WZG documents 28
opcodes (`0x00–0x07`, `0x0A–0x0F`, `0x13–0x22`, `0x26`, `0x28–0x29`,
`0x2C–0x2F`, `0x32`, `0x35–0x36`, `0x40–0x41`, `0x46–0x47`, `0x4A–0x4B`,
`0x54–0x58`, `0x5C`, `0x71–0x72`, `0x76`, `0x78–0x79`); all are covered here
plus ~25 Cubis-specific extensions. WZG offers no additional leads on our
outstanding unknowns: the data-returning unknowns (`0x08`, `0x25`, `0x31`,
`0x33`, `0x48`, `0x7E`, `0xAA`, `0xB5`, `0xB7`, `0xBB`, `0xBD`) don't appear
there, the parameter-table section is a bare "Timeless menu index" stub, the
status byte has no bit-level documentation, app mode / language / user-ID
storage are absent, the 0xB9 cal record is not mentioned, and service access
is referenced only as a physical hardware port with no xBPI command.

### 13.2 Data-returning opcodes of unclear semantic meaning

Each returns stable structured data but the meaning isn't decoded. Most
likely service/diagnostic registers (calibration coefficients, factory
trim values, hardware-version identifiers): `0x31`, `0x33`, `0x48`, `0x7E`,
`0xAA`, `0xB5`, `0xB7`, `0xBB`, `0xBD`. Individual characterisations are
in §7.11.

### 13.3 Undocumented write opcodes

Five Cubis-specific writes characterised only by error-code texture under
TLV-21 args `00`/`01`/`02`. One of them (most likely `0xB6`) caused an
overload-state lockup during discovery — they remain in the wide-sweep
SKIP list pending isolated probe-op characterisation against a known
baseline.

| Op | arg 00 | arg 01 | arg 02 | Interpretation |
|---|---|---|---|---|
| `0x2D` | out_of_range | ACK | not_applicable | Single-valid-arg write; arg 1 triggers a one-shot action |
| `0x53` | ACK | out_of_range | ACK | Accepts 0 and 2, rejects 1 |
| `0x61` | ACK | ACK | unknown_opcode | Binary arg {0, 1} |
| `0x6B` | ACK | ACK | out_of_range | Binary arg {0, 1} |
| `0xB6` | ACK | ACK | ACK | Broadest acceptance; prime overload-state suspect |

### 13.4 Parameter-table indices still unmapped

19 of 70 indices remain unmapped after exhausting app-layer and user-menu
toggles: p00, p17, p18, p24, p27–p30, p42, p43, p45, p51, p52, p54, p55,
p62, p76–p79. Three more (p25, p56, p58) are partially characterised as
the "boot-resets-to-1" class but their semantic meaning is undetermined.
These slots are reachable via `0x55` reads but don't respond to any
user-accessible menu — they most likely live behind the service menu
(§13.6).

### 13.5 State-gated opcode cluster

A long-arg opcode sweep found opcodes that exist in firmware but return
err 0x06 (operation_not_applicable) rather than err 0x04 (unknown_opcode)
regardless of arg shape:

- `0x4C`, `0x4D`, `0x4F` — adjacent to the EEPROM/ISI ops (`0x46`/`0x47`/`0x4A`/`0x4B`); probably cal- or EEPROM-workflow sub-ops.
- `0x77`, `0x7A` — adjacent to temperature / adjustment-unit ops (`0x76`/`0x78`/`0x79`); probably cal-family.
- `0xA0`, `0xA2`, `0xA3`, `0xA4`, `0xA5`, `0xA7`, `0xAB`, `0xAE`, `0xB0` — a contiguous cluster with no known siblings. Strongest candidate for a service-menu API surface.

The gating condition is unknown. Most likely requires entering a specific
balance state (active cal, active tare, service-mode-unlocked) before
responding. Worth a future session that deliberately enters cal / tare /
zeroing states and re-sweeps the cluster to see which become valid in each.

### 13.6 Service menu (Cubis challenge-response)

Cubis balances protect service-level settings behind a 6-digit
challenge-response. The balance displays a per-session random 6-digit
nonce and requires a 6-digit response `F(nonce)` that only Sartorius
service tooling knows. Brute force across sessions is infeasible — the
nonce resets each attempt and the search space is 10⁶.

**xBPI has no visibility into this flow.** The authentication runs on the
front-panel / display-controller microcontroller, which shares a bus with
language selection, user-ID, and app-layer state (§10.2). At-the-prompt
and at-entry snapshots contain no copy of the nonce in any encoding (u32/
u24/u16 BE or LE, BCD, ASCII), no param-table change, and no state-gated
opcode activation — the state-gated cluster above does NOT unlock in the
password-entry state.

**Service-access mechanisms across the Sartorius product line differ:**

- **Older / industrial balances** (Combics, Masterpro, Signum, Expert LE)
  combine a hardware "menu access switch" (S1 jumper) on the mainboard
  with static default passwords — `202122` (service) and `40414243`
  (general), following a sequential-integer pattern (documented in the
  Combics CAW1P and Masterpro manuals).
- **Cubis I/II and newer** use the 6-digit challenge-response described
  above. No hardware switch is documented in user materials. Neither
  `202122` nor the identity-key (response == nonce) unlocks the Cubis.

**The service software implementing `F` is SARTOCAS Service Program for
PCs v1.10+** (older equivalent: PSION server CAS v4.5+). Distributed only
to authorised Sartorius service engineers via MySartorius service-tier
accounts; no public leak found. If available, a copy would enable two
attacks: (a) sniff the xBPI traffic between Sartocas and the balance via
the `sarto-tap` CLI to observe the unlock sequence, and (b) static
analysis of the binary to extract `F` directly.

**Nonce-generator characterisation (15 consecutive samples):** no
LCG-compatible pattern across standard moduli (10⁶, 2²⁰, 2²⁴, 2³⁰, 2³¹,
2³²), GCD of consecutive differences = 1, no XOR or multiplicative
structure between pairs. Nonces span most of the 6-digit range (117k–899k)
with roughly uniform overall digit frequencies. Statistically significant
last-digit bias toward 0 (6 of 15 nonces end in 0 vs 10% expected,
p ≈ 0.002) — suggests display-formatting truncation or a timer-derived
source with coarser internal resolution than the 6-digit display. The
bias characterises the generator but does not recover `F`.

**Status:** closed for xBPI-only reachability. Access requires one of
(a) a Sartocas binary for analysis, (b) an undocumented hardware jumper
on the Cubis mainboard, (c) a JTAG/SPI firmware dump of the front-panel
microcontroller, or (d) a legitimate Sartorius service engagement.

### 13.7 Miscellaneous open questions

- **`0xB9` bytes [13..15]** are confirmed cal-event counters but their
  exact per-field semantics — and specifically why byte [13] increments
  by +2 per cal where [14] and [15] increment by +1 — are unresolved.
- **TLV-addressed reads with wider tags.** Fuzz testing of `0x03`, `0x08`,
  `0x25`, `0x31`, `0x33`, `0x48`, `0x50`, `0x51`, `0x7E`, `0xAA`, `0xB5`,
  `0xB7`, `0xBB` under TLV-11/12/14/22/24 args returned no new data —
  the protocol appears to be strictly TLV-21-only for slot addressing,
  with other opcodes either ignoring args or rejecting wider tags.
  `0x03` (WZG-documented read_user_id) continues to return a zeroed
  measurement frame on any arg form; the user-ID storage location
  remains unknown.
- **Per-unit correlation.** If a second Cubis is accessible, comparing
  `0x08`, `0xAA`, `0xB7`, and the other factory-constant-looking opcodes
  would identify which fields are factory-trim / serial-derived (differ
  per-unit) vs. universal product constants (identical).

### 13.8 Destructive opcodes (use caution)

The following must not be exercised without clear intent:

- `0x04` write user ID
- `0x28` start adjustment (wrong args can trigger a real calibration)
- `0x40` reconfiguration
- `0x41` reset temporary errors
- `0x56` write parameter table
- `0x58` initiate reset (warm/cold)
- `0x5C` set baud rate (can silently break comms)
- `0x72` write SBN address (can silently break comms)
- `0x79` write adjustment unit

Plus the five undocumented writes in §13.3 (`0x2D`, `0x53`, `0x61`,
`0x6B`, `0xB6`) — any of these can push the balance into an overload
state that only clears on cold boot.

## 14. Cross-family observations

Everything in §1-13 has been validated across three balances. Protocol
features described there without a family qualifier are universal
unless otherwise noted. This section consolidates the family-specific
behaviours and the opcodes that exist on some balances but not others.

### 14.1 Balance identification

| Attribute | MSE1203S-100-DR | WZA8202-N | BCE3202-1S |
|---|---|---|---|
| Class | Cubis analytical | OEM weigh cell | Basic laboratory |
| Capacity × resolution | 1200 g / 1 mg | 8200 g / 10 mg | 3200 g / 10 mg |
| xBPI default baud | 19200 | 1200 | 9600 |
| Ships-from-factory mode | SBI command/reply | SBI autoprint (1200-7-O-1) | SBI command/reply |
| SBN | 0x00 | 0x00 | 0x00 |
| `0x02` model string | `"MSE1203S-100-DR"` | `"WZA8202-N"` | `"BCE3202-1S"` |
| Parameter table size | 70 indices | 8 indices | 70 indices |
| Temperature sensors installed | 3 (idx 0, 1, 3) | 2 (idx 0, 3) | 1 (idx 0) |

### 14.2 Opcode presence matrix

Key opcodes and their behaviour on each balance. `✓` = works,
`err_04` = unknown_opcode, `err_07` = invalid-args (exists but needs
args not yet characterised):

| Opcode | Purpose | MSE | WZA | BCE |
|---|---|---|---|---|
| `0x00`–`0x07` | identity reads | ✓ | ✓ | ✓ |
| `0x08` | factory constant | `5b 67 df` | `00 00 00` | `00 00 00` |
| `0x0B`–`0x0F` | metrology, balance info | ✓ | ✓ | ✓ |
| `0x1E`/`0x1F`/`0x20`/`0x22` | measurement | ✓ | ✓ | ✓ |
| `0x26` | weighing mode | ✓ | ✓ | ✓ |
| `0x2F` | gross bargraph | ✓ | ✓ | ✓ |
| `0x30`/`0x32` | status block/short | ✓ | ✓ | ✓ |
| `0x35`/`0x36` | timestamp/on-off | ✓ | ✓ | ✓ |
| `0x48` | stored reference | 1000.0 g | zeroed | 5000.0 g |
| `0x55`/`0x57` | param table, cycle time | ✓ | ✓ | ✓ |
| `0x62` | menu-activity register | ✓ (active) | reads 0 (dead) | err_04 |
| `0x71` | SBN read | ✓ | ✓ | ✓ |
| `0x76` | temperature | ✓ | ✓ | ✓ |
| `0x78` | read adjustment unit | — (unprobed) | — | ✓ (= 2, g) |
| `0xAA` | universal constant | ✓ | ✓ | ✓ |
| `0xB9` | last cal record | ✓ | err_04 | ✓ (zeroed — no cal) |
| `0xBA` | config generation counter | ✓ | err_04 | ✓ |
| `0xBC` | module list | ✓ | — (untested) | — |
| `0xFF` | NOP | ACK | ACK | ACK |
| BCE-specific (see §14.6) | various | err_04 | err_04 | various |

### 14.3 Metrological thresholds (0x0B / 0x0E)

`0x0D` is the live increment (display-unit dependent). `0x0B` and
`0x0E` are thresholds that scale with the increment — but the 0x0B
ratio depends on display resolution:

| Balance | Resolution | 0x0D increment | 0x0B | 0x0E |
|---|---|---|---|---|
| MSE1203S | 1 mg | 0.001 g | 0.1 g (= 100× incr) | 0.01 g (= 10× incr) |
| WZA8202-N | 10 mg | 0.01 g | 0.5 g (= 50× incr) | 0.1 g (= 10× incr) |
| BCE3202 | 10 mg | 0.01 g (10⁻⁵ kg at kg display) | 0.5 g (= 50× incr) | 0.1 g (= 10× incr) |

`0x0E` is consistently 10× increment across families. `0x0B` is 100×
on 1-mg balances, 50× on 10-mg balances — a resolution-dependent
ratio, not a family-dependent one.

### 14.4 Cycle-time curve is universal (p01-driven)

`0x57 cycle_time` tracks p01 (filter / ambient mode) identically on
every balance that has both indices:

| p01 | label | `0x57` |
|---|---|---|
| 1 | very stable | 50 ms |
| 2 | stable | 100 ms |
| 3 | unstable | 200 ms |
| 4 | very unstable | 400 ms |

Confirmed on MSE at p01=4 (400 ms), WZA at p01 ∈ {2,3,4} (100/200/400 ms),
and BCE at p01 ∈ {2,3} (100/200 ms). The doubling sequence is universal.
**p14** exists on MSE and BCE as a separate parameter but does NOT drive
`0x57`: isolating on BCE with only p01 changing (and p14 held at 1) moved
`0x57` 200→100 ms as p01 went 3→2. What p14 drives is open.

### 14.5 State / status byte encoding by family

See §8.2 for the layout and family-specific state byte meanings. In
summary:

- Cubis (MSE): state byte has bit `0x80` set (unstable `0x80`, stable
  `0x88`); status byte uses bit `0x08` for ADC-trust and bit `0x10`
  for isoCAL-due.
- Non-Cubis (WZA, BCE): state byte never sets `0x80` — stable is
  `0x08`, unstable is `0x00`; status byte uses bit `0x20` for a
  "measurement ready" signal.

Cross-family stable detection should use the measurement-frame flags
byte's `0x40` bit (§8.1), which is universal.

### 14.6 BCE-specific opcodes

All of the following return `err_04 unknown_opcode` on MSE and WZA.
They are BCE-family reads.

#### 14.6.1 `0x75` raw load ADC

Returns a 4-byte body under novel subtype `0x14`, linearly correlated
with load:

> `0x75 ≈ 310,118,054 + 79,767 × mass_g`  (≈ 12.5 µg per ADC count)

Verified on BCE3202 across three loaded mass points (18.27 g / 199.86 g /
699.54 g) with < 0.01% residuals. The empty-pan reading sits ~785,000
counts above the extrapolated zero-mass intercept (≈ 9.8 g equivalent),
because the balance's displayed "0.00 g" is a software-tared value that
`0x75` doesn't participate in.

**Practical use** — field-calibrate two points and interpolate:

1. Tare or leave pan empty, read `0x75` → baseline `B`.
2. Place known mass `m_ref`, read `0x75` → reference `R`.
3. For any later reading `v`: `mass ≈ (v − B) × m_ref / (R − B)`.

Do NOT anchor on the literal y-intercept constant `310,118,054` — it
isn't an observable state.

`0x75` exposes sub-display-LSB dynamics that the balance's digital
filter hides: ~500-count oscillation (~6 mg) on a stable empty pan;
visible creep/relaxation (hundreds of counts) during mass settling;
tighter absolute noise at larger loads (160 counts at 700 g vs 500 at
18 g). Candidate applications: custom filters (EMA/Kalman), creep
monitoring, sub-LSB averaging for steady-state measurements.

First publicly-documented raw-ADC access on any Sartorius xBPI
balance. No published SBI/xBPI driver surfaces this.

#### 14.6.2 `0x3B` — likely user-unit label storage

Stable 12-byte body (subtype 0x48, novel length):

```
3f 80 00 00   00 00 02 80   43 67 20 20
└─ float 1.0  └─ 4 bytes     └─ "Cg  " ASCII null-padded
```

Decomposes as: `multiplier: float32` (1.0 default), 4 middle bytes
(flags or count), 4 trailing ASCII bytes (`"Cg  "`). Hypothesis:
storage for p07's `userdef=1` user-defined display unit, matching
§10.1's note that the user-defined multiplier and label "live in a
separate register not yet located." Unchanged across 5 s — a static
register, not time-varying. To confirm, program a user-defined unit
via the front panel and watch the label field change.

#### 14.6.3 Short novel registers

All return 1-byte bodies (subtype 0x21 or 0x41), stable across reads:

| Opcode | Subtype | Body | Notes |
|---|---|---|---|
| `0x24` | 0x21 | `01` | Unknown u8 register |
| `0x33` | 0x22 | `10 00` | The "magic 1000" constant now reachable as an opcode response (same value that appears in status-block byte [5..6]) |
| `0x34` | 0x24 | `00 00 00` | 3-byte body on a subtype nominally 4-byte — possibly malformed or different layout |
| `0x3D` | 0x21 | `00` | Unknown u8 |
| `0x5B` | 0x21 | `00` | Unknown u8 |
| `0x6F` | 0x21 | `10` | Unknown u8 |
| `0x7C` | 0x41 | `00` | Novel subtype (`0x41` = long_data family, 1-byte length) |

#### 14.6.4 Args-accepting reachable surface (uncharacterised)

52 opcodes on BCE return `err_07 invalid_or_missing_args` on no-args
and all TLV tag variations tried (11/12/14/21/22/24). They exist but
want argument shapes not yet guessed — most likely multi-TLV
structured args (e.g. `21 <slot> 35 <float32> <aux>` write-with-value
forms). Characterisation is blocked by the bootloader-drop risk:

> `0x06, 0x1B, 0x27, 0x2A, 0x3A, 0x3E, 0x3F, 0x42, 0x43, 0x44, 0x45, 0x49,`
> `0x4B, 0x4C, 0x4D, 0x4E, 0x4F, 0x52, 0x5D, 0x63, 0x65, 0x6C, 0x6E, 0x70,`
> `0x7A, 0x7B, 0x7D, 0x7F, 0x80, 0x82, 0x83, 0x8D, 0xA9, 0xAA, 0xAC, 0xAD,`
> `0xB0, 0xB1, 0xB8, 0xBF, 0xC1, 0xC3, 0xC4, 0xC5, 0xC7, 0xC8, 0xC9, 0xCA,`
> `0xCB, 0xD3, 0xD5, 0xD7, 0xD8`

Requires per-opcode characterisation against a baseline with
cold-boot recovery budget. See §12 for the bootloader-drop incident.

#### 14.6.5 `0x9F` — silent-ACK write

Acks every TLV-21 arg with no visible change in any monitored
register. Almost certainly a write. On the permanent SKIP list
until individually characterised.

### 14.7 MSE (Cubis)-specific features

- **`0x62`** — active menu-activity register on MSE (value varies
  transiently with menu interaction). Reads as constant `0x00` on WZA
  (dead register) and is entirely absent on BCE (err_04). Treat as
  Cubis-exclusive.
- **`0x08`** — returns factory-trim body `5b 67 df` on MSE. Both WZA
  and BCE return `00 00 00`. Appears to be MSE-populated factory data
  (hardware-variant token or build checksum) with the register
  unpopulated elsewhere.
- **State byte with `0x80` set** — Cubis-only encoding. See §8.2.
- **Cycle-time driven by p01 only** — MSE has both p01 and p14 but
  `0x57` tracks p01 just like WZA and BCE; p14's effect remains open.
- **Status-block byte [0] may be non-zero on MSE** — one session
  observed `0x08` consistently. See §8.2 notes.

### 14.8 WZA-specific features

- **Reduced parameter table**: 8 valid indices (p01-p07 plus p09)
  versus MSE+BCE's 70. See §10.
- **mg-display byte[5] low-nibble anomaly**: at display=mg, `byte[5]`
  comes back with low-nibble `3` (values `0x03`/`0x13`/`0x23`) instead
  of `0`. Correlates with WZA's 10 mg resolution (display can't reach
  1 mg granularity in mg mode). See §8.4.
- **Time-stamp tick decoupled from measurement rate**: `0x35` on WZA
  ticks at ~50 Hz independently of cycle_time (which is 100-400 ms).
  On MSE, `0x35` ticks at the measurement rate.
- **`0xBA` and `0xB9` are absent** (`err_04 unknown_opcode`) despite
  working on both MSE and BCE. The `0x0B9` cal-record register and
  `0xBA` config-gen counter are genuinely missing from WZA firmware,
  not Cubis-specific as we originally hypothesised.

### 14.9 Universal firmware constants (`0xAA`)

`0xAA` with TLV-21 arg 00 or 01 returns body
`14 21 12 00 97 14 00 95 b7 44` on all three balances, byte-for-byte
identical. Decodes as two u32 TLVs:

- `0x14 21120097` = 0x21120097 (decimal 555,745,431)
- `0x14 0095b744` = 0x0095b744 (decimal 9,811,268)

TLV-21 args ≥ 02 return err_03. Meaning not decoded; strongest
candidates are firmware-build ID and protocol-version tokens. The
byte-for-byte match across three families makes this the best
candidate for a universal xBPI fingerprint.

### 14.10 Other structural observations

- **`0x0A` configuration_data embeds the current unit descriptor**
  in its body bytes [5..6]. At display=g, bytes [5..6] = `02 02`
  (2 decimals, g); at display=kg on BCE, `05 03` (5 decimals, kg).
  The configuration-data structure reuses the unit-descriptor
  encoding from measurement frames.
- **`0x50 slot availability`** varies: MSE has slots 0 and 1 both
  valid (u8=3); WZA has the same pair; BCE has only slot 0.
- **`0x48` stored-reference differs per balance**: 1000.0 g on MSE,
  5000.0 g on BCE (exceeds BCE's 3200 g max, so not a user-settable
  reference). Appears to be a firmware-wired constant chosen at
  manufacture.

### 14.11 Bootloader-drop incident

A read-only no-args opcode sweep on BCE3202-1S (with an MSE-learned
SKIP list plus the WZA-learned `0x9F` silent-ACK opcode) left the
balance stuck in firmware bootloader mode ("B.LOADER" on the front
panel). Power-cycling recovered cleanly. No reply in the capture log
showed an opcode ACK'ing unexpectedly, so at least one opcode in the
err_07 invalid-args response class on BCE has a delayed or
state-sensitive side effect when called with no args.

This means **the §13.8 SKIP list is not sufficient on unfamiliar
balance families**. The WZA+MSE-derived safe list, which worked on
both those balances without incident, triggered the drop on BCE.

**Safe exploration strategy for an unfamiliar balance**:

1. Characterise each opcode with a **single-shot probe**, not a sweep.
2. Bracket each probe with baseline diffs (reads of `0x32`, `0xBA`,
   a few param-table indices) before and after.
3. Between probes, **visually check the front panel** — a bootloader
   drop is front-panel-visible before any serial symptom.
4. Keep a cold-boot recovery budget — the BCE drop cleared with a
   single power cycle, but don't rely on it.

### 14.12 Open items

- **BCE bootloader-drop culprit**. Needs bisection probing with a
  cold-boot budget per iteration.
- **BCE state-byte encoding under shake**. Only stable captured.
- **BCE args-accepting opcode semantics** (§14.6.4). The richest
  untapped reverse-engineering surface.
- **What p14 actually drives**. Confirmed not `0x57`; still unknown.
- **`0x3B` user-unit label change** via front-panel unit programming,
  to confirm the user-defined-unit storage hypothesis.
- **`0x75` on MSE alternatives**. Confirmed absent on MSE; whether
  MSE has a different raw-ADC opcode is open.
- **Per-unit variation of `0x08` within the MSE family** (we have
  one MSE). A second MSE would tell us whether `5b 67 df` is
  per-unit factory trim or family-wide.
- **Status-block byte [0] semantics on MSE** — observed `0x08` in one
  capture session, `0x00` in earlier ones. Needs state-correlated
  captures.

## 15. References

- **This repository**: everything under `src/sartoriuslib/` — the Python
  implementation of the findings documented here.
- **WZG OEM weigh cell manual**: shipped with Sartorius WZG OEM weigh
  cells. A local copy lives at
  [datalab-output-Bilance_Tecniche_Sartorius_OEM_IP65_DS-WZG-e.md](datalab-output-Bilance_Tecniche_Sartorius_OEM_IP65_DS-WZG-e.md)
  in this project.
- **Captured sessions**: `tests/fixtures/captures/` — promoted JSON, JSONL,
  CSV, SQLite, and text fixtures with raw xBPI/SBI bytes and decoded state
  snapshots that feed regression tests.
