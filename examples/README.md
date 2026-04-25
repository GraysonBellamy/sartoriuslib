# Examples

Runnable scripts demonstrating the sartoriuslib public surface. Each
file is a standalone `.py` — no package layout, no helper modules. Run
with `uv run python examples/<file>.py` (or plain `python` once the
env is active).

Most examples read the serial port from the `PORT` environment
variable and fall back to `/dev/ttyUSB0`:

```bash
PORT=/dev/ttyUSB1 uv run python examples/combined_mfc_balance.py
```

## Combined acquisition

- `combined_mfc_balance.py` — runs `alicatlib.AlicatManager` +
  `sartoriuslib.SartoriusManager` concurrently in one task group, each
  streaming through `record(...)` into its own table inside a single
  SQLite database. The headline use case for combined laboratory
  acquisition; swap `SqliteSink` for `ParquetSink` / `PostgresSink`
  (with the matching extra installed) for parquet or Postgres targets.

  Requires both `alicatlib` and `sartoriuslib` installed in the same
  venv. Reads ports from `PORT_MFC` / `PORT_BAL` and duration from
  `DURATION` (seconds, default 30); writes to `OUTPUT` (default
  `combined_run.db`).
