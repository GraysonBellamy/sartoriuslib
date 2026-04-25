# MSE1203S-100-DR — xBPI capture set

| Field | Value |
|---|---|
| Model | `MSE1203S-100-DR` |
| Family | `cubis` |
| Capacity | 1200 g |
| Increment | 1 mg |
| Software-version blob | `00 39 21 00 39 01 39 01 00 01` (10b, packed) |
| Manufacturer | `Sartorius` |
| Factory number | `00 31 80 11 65` (5b) |
| Wire framing | 19200-8-O-1, xBPI binary |
| Captured | 2026-04-25 (hardware day) |
| Conditions | 200 g calibration mass, level pan, ambient ~25.6 °C |

## Files

- **`idle_tap.txt`** — 0 bytes. Proof the line was silent; xBPI does
  not autoprint, so a quiet `sarto-diag tap` over 5 s is the expected
  shape.
- **`snapshot.json`** — `sarto-diag snapshot` walk over every safe-list
  opcode. 34 entries; each carries `opcode`, `name`, `status`, plus the
  raw reply or typed error. The shape is consumed by
  `tests/unit/fixtures_regression/test_mse_xbpi_snapshot.py`.
- **`snapshot_auto.json`** — same set, but produced via
  `--protocol auto`. Validates that auto-detect resolves to xBPI
  cleanly without changing the snapshot content.
- **`run_60s.{csv,jsonl,sqlite}`** — 60-second 10 Hz capture, 600
  samples each, identical content in three sink shapes. The sample
  block contains a mid-run mass lift+replace producing a clear
  perturbation (`value` swings from ~200 g to near 0 g and back).

## Notes from hardware day

Several decoder behaviours were validated for the first time against a
real device on this capture set:

- **Sparse temperature sensor indices.** Sensors at `0`, `1`, `3` —
  index `2` is a deliberate reserved slot returning the
  `7f ff ff ff` sentinel (`celsius=None`). Indices `>=4` raise
  xBPI `0x04`. Drove the parameterized-command flag in
  `Command` (`docs/design.md` §6.1.1 follow-up) so a probe at index
  4 doesn't poison the cache for indices 0/1/3.
- **Cell-busy mantissa.** After `zero()` the unit emits ~6 frames
  with mantissa `0x7fffffff` (the off-scale sentinel) for ~2 s while
  the cell recomputes its zero point. Off-scale flag is set; overload
  / underload stay False (those need `status()` to disambiguate).
- **Capacity / increment have no on-wire unit byte.** The
  typed-float reply is value-only; `Balance.capacity()` /
  `Balance.increment()` fold the current display unit (`p07`) in to
  produce a complete `Quantity`.
