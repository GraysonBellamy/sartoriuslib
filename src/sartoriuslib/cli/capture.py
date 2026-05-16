"""``sarto-capture`` — timed acquisition to any sink.

Per design doc §13:

    sarto-capture PORT --rate 10 --duration 30 --out run.jsonl

Wraps :func:`sartoriuslib.streaming.record` + a sink chosen by the
output path's extension (``.csv``, ``.jsonl``, ``.sqlite``/``.db``).
On completion an :class:`AcquisitionSummary` is printed so users can
see drop counts at a glance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sartoriuslib.cli._common import (
    add_open_args,
    resolve_open_args,
    run_cli,
)
from sartoriuslib.errors import SartoriusValidationError
from sartoriuslib.manager import SartoriusManager
from sartoriuslib.sinks import CsvSink, InMemorySink, JsonlSink, SqliteSink
from sartoriuslib.sinks.base import pipe
from sartoriuslib.streaming import OverflowPolicy, record

if TYPE_CHECKING:
    from sartoriuslib.sinks.base import SampleSink
    from sartoriuslib.streaming.recorder import AcquisitionSummary


__all__ = ["main"]


_SINK_BY_EXT: dict[str, str] = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
}


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-capture",
        description="Record polled readings at a fixed cadence into a sink file.",
    )
    add_open_args(parser)
    parser.add_argument(
        "--rate",
        type=float,
        required=True,
        help="Polling rate in Hz (absolute targets, drift bounded to one tick).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        required=True,
        help="Acquisition duration in seconds.",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output file path. Sink is chosen from the extension "
        "(.csv / .jsonl / .sqlite). Pass '-' to drop samples (test mode).",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="balance",
        help="Manager-level device name attached to every sample (default: balance).",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "sqlite"),
        default=None,
        help="Override the sink format (otherwise inferred from --out extension).",
    )
    parser.add_argument(
        "--table",
        default="samples",
        help="SQLite-only: table name (default: samples).",
    )
    parser.add_argument(
        "--overflow",
        choices=("block", "drop_newest"),
        default="block",
        help="Backpressure policy when the buffer fills (default: block).",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=64,
        help="Receive-stream capacity in batches (default: 64).",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    port, protocol, settings = resolve_open_args(args)
    sink = _sink_from_args(args)
    overflow = OverflowPolicy.BLOCK if args.overflow == "block" else OverflowPolicy.DROP_NEWEST

    async with SartoriusManager() as mgr:
        await mgr.add(
            args.name,
            port,
            protocol=protocol,
            serial_settings=settings,
            timeout=args.timeout,
            identify=False,
        )
        async with (
            sink,
            record(
                mgr,
                rate_hz=args.rate,
                duration=args.duration,
                overflow=overflow,
                buffer_size=args.buffer_size,
            ) as recording,
        ):
            summary = await pipe(recording.stream, sink)
    sys.stdout.write(_format_summary(summary, out_path=args.out))
    return 0


def _sink_from_args(args: argparse.Namespace) -> SampleSink:
    """Resolve the sink kind from ``--format`` or the ``--out`` extension."""
    if args.out == "-":
        return InMemorySink()
    out_path = Path(args.out)
    kind = args.format
    if kind is None:
        ext = out_path.suffix.lower()
        kind = _SINK_BY_EXT.get(ext)
    if kind is None:
        raise SartoriusValidationError(
            f"--out: cannot infer sink format from extension {out_path.suffix!r}; "
            f"pass --format csv|jsonl|sqlite (known extensions: "
            f"{', '.join(sorted(_SINK_BY_EXT))})",
        )
    if kind == "csv":
        return CsvSink(out_path)
    if kind == "jsonl":
        return JsonlSink(out_path)
    if kind == "sqlite":
        return SqliteSink(out_path, table=args.table)
    raise SartoriusValidationError(  # pragma: no cover — argparse choices guard
        f"--format: unknown sink kind {kind!r}",
    )


def _format_summary(summary: AcquisitionSummary, *, out_path: str) -> str:
    lines = [
        "acquisition complete:",
        f"  out:             {out_path}",
        f"  samples_emitted: {summary.samples_emitted}",
        f"  samples_late:    {summary.samples_late}",
        f"  max_drift_ms:    {summary.max_drift_ms:.3f}",
    ]
    return "\n".join(lines) + "\n"
