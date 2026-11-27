# MSE1203S-100-DR — internal adjustment (`0x28 21 78`) capture set

Bench captures behind `docs/protocol.md` §7.7.1 and §7.12, and the
`Balance.wait_for_adjustment()` design (issue #55).

| Field | Value |
|---|---|
| Model | `MSE1203S-100-DR`, software blob `00 39 21 00 39 01 39 01 00 01` |
| Wire framing | xBPI, 57600-8-O-1, host SBN 1 → balance SBN 9 |
| Captured | 2026-11-27 |
| Conditions | ~344 g preload on the pan (tared out), noisy rig (state flips `0x80`/`0x88` at rest), isoCAL on (`p15=3`) and due at the start |

## Files

All files are gzipped JSONL, one exchange per line.

- **`run_a_adjust.jsonl.gz`**: plain internal adjust, polled at ~4 Hz
  (`0x30`, `0x1E`, `0xB9`, `0x62`, plus `0x32`/`0xBA`/`0x20`/`0x76`/`0x35` every
  fifth cycle). Fields: `t` (s since start, monotonic), `wall` (UTC), `tag`,
  `op`, `args`, `kind` (`ok`/`err`/`timeout`), `lat_ms`, `raw`, `subtype`,
  `body`, `dec` (decoded view). `0x28` at t=5.10 s, idle again at t=29.59 s.
  The first line is a `meta` record and the last is a `summary` record.
- **`run_b_abort.jsonl.gz`**: same poll set; `0x29` abort sent 8.07 s after
  `0x28`. The adjust bit clears ~2.5 s later and `0xB9` is unchanged.
- **`run_c_reenter.jsonl.gz`**: same poll set plus `0x77`/`0x78`/`0x7A`.
  A second `0x28` is sent 3 s in and refused with err `0x06`. The running
  adjust completes normally.
- **`run_g_fast.jsonl.gz`**: ~200 Hz trace, repeating `0x32 ×4, 0x1E, 0xB9`.
  Fields: `t` (s since start), `op`, `body` (hex, `null` on a failed
  exchange). This is the source of the state timeline in `docs/protocol.md`
  §7.7.1 and of `sartoriuslib.testing.MSE_INTERNAL_ADJUST_TIMELINE`.

Zero timeouts, framing errors, or device errors (other than the deliberate
`0x06`s) occurred across all four runs.

## Re-capturing

`sarto-diag adjust-trace PORT --i-understand-this-is-destructive` produces
the same JSONL shape (`--fast` for the run-G style trace). It runs a real
internal adjustment.
