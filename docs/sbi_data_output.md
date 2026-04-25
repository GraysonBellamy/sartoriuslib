# Data Interfaces — Data Output

## Data Output

You can define the data output parameter so that output is activated either when a print command is received or automatically synchronized with the display or at defined intervals (e.g., application programs and autoprint settings).

### Data Output by Print Command
The print command can be transmitted by pressing the print key or by a software command (Esc P).

### Automatic Data Output
In autoprint mode, data is output to the data interface port without an extra print command.  
You can have synchronized data output automatically at defined display update intervals, with or without the stability parameter.  
The interval time depends on the balance operating status and balance type.

If automatic data output is activated in the Device Configuration, it starts immediately after the balance is turned on.  
You can also configure whether the automatic data output can be stopped and started by pressing the print key.

---

## Data Output Formats

You can output the values displayed in the line for measured values and weight units with or without an ID code.

**Example (without ID code):**
```
+    253 pcs   (16 characters)
```

**Example (with ID code):**
```
Qnt +    253 pcs   (22 characters)
```

---

## Data Output Format with 16 Characters

- Blank display segments are output as spaces  
- Values without a decimal point are printed without a decimal point  
- Character type depends on position  

### Normal Operation (16-character format)

| Pos | Description |
|-----|------------|
| 1 | + / - / space |
| 2 | Space |
| 3–10 | Weight value (decimal aligned, leading zeros as spaces) |
| 11 | Space |
| 12–14 | Unit characters |
| 15 | CR |
| 16 | LF |

### Legend

- `*` = Space  
- `A` = Displayed characters  
- `S` = Unit characters  
- `CR` = Carriage return  
- `LF` = Line feed  

### Stability in Live Autoprint Captures

On the observed MSE output, the unit field is blank while the displayed value is
not stable and appears once the value stabilizes:

```text
N     +    0.031
N     +    0.006 g
```

The parser treats numeric SBI lines with a blank unit field as valid readings
with `stable=False` and `unit=UNKNOWN`; unit-bearing numeric lines are stable
unless they also carry the explicit unstable marker.

Package behavior: when forced SBI open detects already-enabled autoprint, the
session records autoprint as active. Use `identify=False` and consume with
`poll()` or `stream(mode="autoprint")`; SBI calls that expect command replies
are refused because live captures did not produce distinguishable replies while
autoprint was active. If autoprint is changed from the balance front panel
after opening, surprise output moves the session into autoprint mode, and
`refresh_sbi_autoprint_state()` can be used to re-sniff and clear the mode once
the stream has been disabled.

---

## Special Outputs (16-character format)

Examples:
- `High` → Overload  
- `Low` → Underload  
- `Cal. Ext.` → External calibration  

Additional fields:
- `W` = Draft shield status (optional)  
- `I` = Ionizer (optional)  
- `Y,Y,Y` = Draft shield doors  
- `XXX` = Decimal value from binary control data  

---

## Control Information (Binary)

| Decimal | Bit | Meaning |
|--------|-----|--------|
| 1 | Bit0 | 0 = No error / ionizer off, 1 = Draft shield error / ionizer on |
| 2 | Bit1 | 0 = Motors off, 1 = Draft shield in motion |
| 8 | Bit3 | 0 = Learning off, 1 = Learning on |
| 16 | Bit4 | 0 = At least one door open, 1 = All doors closed |
| 32 | Bit6 | 0 = Motorized operation, 1 = Manual operation |

Door status examples:
- `R,M,L = COO` → Right closed, middle & left open  
- `R,M,L = OCC` → Right open, middle & left closed  

---

## Error Messages (16-character format)

Examples:
```
Err ###
APP. ERR
DIS. ERR
PRT. ERR
```

- `###` = Error code  
- Refer to error message section for details  

---

## Example Output

```
+    123.56 g
```

### Position Breakdown

| Position | Meaning |
|---------|--------|
| 1 | Sign (+/-/space) |
| 2 | Space |
| 3–10 | Weight value |
| 11 | Space |
| 12–14 | Unit |
| 15 | CR |
| 16 | LF |

---

# Data Output Format with 22 Characters

This format adds a 6-character ID code prefix to the 16-character format.

### Structure

| Pos | Description |
|-----|------------|
| 1–6 | ID code |
| 7 | Sign |
| 8–15 | Weight value |
| 16–18 | Unit |
| 19–22 | CR / LF / padding |

### Legend

- `K` = ID code character  
- `A` = Displayed characters  
- `S` = Unit symbol  
- `*` = Space  
- `CR` = Carriage return  
- `LF` = Line feed  

---

## Example (22-character)

```
N     +123.56 g
```

---

## Identification of Non-Verified Digits

Non-verified digits are marked with square brackets in printer mode.

- Setting: Communication mode → PRINTER  
- In SBI mode, unverified values are not automatically marked  

---

## Special Outputs (22-character format)

Examples:
- `Stat High`  
- `Stat Low`  
- `Stat Cal. Ext.`  
- `Stat     Cal.Int.`  

Observed during live autoprint: starting internal calibration with `ESC Z`
does not silence the data port. The stream switches to repeated
`Stat     Cal.Int.` status records during the calibration window, then resumes
weight records. The parser treats those calibration records as non-weight
status lines.

---

## Error Messages (22-character format)

Examples:
```
Stat ERR ###
Stat APP. ERR
Stat DIS. ERR
Stat PRT. ERR
```

- `###` = Error code  
- Refer to error message section for details  
