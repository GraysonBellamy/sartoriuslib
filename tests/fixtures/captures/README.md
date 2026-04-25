# Real-balance capture fixtures

Wire-trace captures collected on real Sartorius hardware. Each
sub-directory groups captures by balance + protocol; files within are
referenced by name from the regression tests under
`tests/unit/fixtures_regression/`.

The point: the unit suite proves the codecs work on synthetic bytes.
These fixtures pin them to **bytes that came off a real device**, so a
decoder change that breaks real-world replies fails CI even if every
synthetic test still passes.

## Layout

```
tests/fixtures/captures/
├── mse_xbpi/         # MSE1203S-100-DR speaking xBPI at 19200-8-O-1
│   ├── README.md
│   ├── idle_tap.txt        # 0 bytes — proof the line is silent (autoprint off)
│   ├── snapshot.json       # `sarto-diag snapshot` — every safe-list opcode + reply
│   ├── snapshot_auto.json  # same, but resolved via `--protocol auto`
│   └── run_60s.{csv,jsonl,sqlite}  # 60s @ 10Hz with a mid-run perturbation
└── mse_sbi/          # MSE1203S-100-DR speaking SBI at 19200-8-O-1
    └── idle_tap.txt        # 0 bytes — autoprint off + line silent
```

## How fixtures are produced

Every file here was produced by running the runbook in
[`docs/hardware-day.md`](../../../docs/hardware-day.md) — the commands
that write to a path under `tests/fixtures/captures/...` are the
canonical recipe. Re-running the runbook against a different MSE / WZA
/ BCE adds new sub-directories without touching existing ones.

## How fixtures are consumed

- `snapshot*.json` rows feed the per-opcode decoder regression tests.
- `run_60s.*` rows feed the streaming-codec regression test (parser
  must decode every `raw` field).
- `idle_tap.txt` proves the passive sniff promise (zero bytes returned
  on a quiet line); also functions as a non-empty smoke test for the
  capture path itself.
