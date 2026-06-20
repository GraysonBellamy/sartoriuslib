#!/usr/bin/env python3
"""Combined alicatlib + sartoriuslib acquisition into a single SQLite DB.

Runs an :class:`alicatlib.AlicatManager` and a
:class:`sartoriuslib.SartoriusManager` concurrently inside one
:func:`anyio.create_task_group`. Each manager streams through its own
:func:`record` call into a separate sink, and both sinks share one
SQLite database file (each writes to its own table) so downstream
analysis sees one canonical run.

This example is the headline multi-source workflow that motivated
cross-library schema parity. Swap ``SqliteSink`` for ``ParquetSink``
or ``PostgresSink`` (with the matching extra installed under
``sartoriuslib[parquet|postgres]``) to land into the durable target
of your choice.

    # Requires both libraries installed in the same venv.
    PORT_MFC=/dev/ttyUSB0 PORT_BAL=/dev/ttyUSB1 \\
        uv run python examples/combined_mfc_balance.py

The script is configured for a 30-second run at 5 Hz on the MFC and
2 Hz on the balance, then closes both managers, prints a summary,
and reads the row counts back from the DB to prove persistence.
"""

from __future__ import annotations

import os
import sqlite3

import anyio
from alicatlib import AlicatManager
from alicatlib.sinks import SqliteSink as AlicatSqliteSink
from alicatlib.sinks import pipe as alicat_pipe
from alicatlib.streaming import record as alicat_record

from sartoriuslib import SartoriusManager
from sartoriuslib.sinks import SqliteSink, pipe
from sartoriuslib.streaming import record


async def main() -> None:
    port_mfc = os.environ.get("PORT_MFC", "/dev/ttyUSB0")
    port_bal = os.environ.get("PORT_BAL", "/dev/ttyUSB1")
    duration_s = float(os.environ.get("DURATION", "30"))
    db_path = os.environ.get("OUTPUT", "combined_run.db")

    async with (
        AlicatManager() as mfcs,
        SartoriusManager() as bals,
    ):
        await mfcs.add("fuel", port_mfc)
        await bals.add("scale", port_bal)

        async with (
            alicat_record(mfcs, rate_hz=5.0, duration=duration_s) as mfc_stream,
            record(bals, rate_hz=2.0, duration=duration_s) as bal_stream,
            AlicatSqliteSink(db_path, table="mfc_samples") as mfc_sink,
            SqliteSink(db_path, table="balance_samples") as bal_sink,
            anyio.create_task_group() as tg,
        ):

            async def _drain_mfc() -> None:
                summary = await alicat_pipe(mfc_stream, mfc_sink)
                print(f"mfc samples_emitted: {summary.samples_emitted}")

            async def _drain_bal() -> None:
                summary = await pipe(bal_stream, bal_sink)
                print(f"balance samples_emitted: {summary.samples_emitted}")

            _ = tg.start_soon(_drain_mfc)
            _ = tg.start_soon(_drain_bal)

    print(f"\nwrote {db_path}")
    with sqlite3.connect(db_path) as conn:
        (mfc_count,) = conn.execute("SELECT COUNT(*) FROM mfc_samples").fetchone()
        (bal_count,) = conn.execute("SELECT COUNT(*) FROM balance_samples").fetchone()
    print(f"  mfc_samples       {mfc_count} rows")
    print(f"  balance_samples   {bal_count} rows")


if __name__ == "__main__":
    anyio.run(main)
