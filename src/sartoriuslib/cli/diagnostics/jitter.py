"""``sarto-diag jitter`` - xBPI acquisition timing probe.

Runs a fixed-cadence, read-only xBPI acquisition and records host-side
timing diagnostics for every poll. The default is a 50 Hz long net-weight
read (``0x1E 09 30``), which returns weight plus status/sequence data in
one frame on balances that support it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

import anyio
from anyio.lowlevel import checkpoint
from anyserial import list_serial_ports

from sartoriuslib.cli._common import add_open_args, resolve_open_args, run_cli
from sartoriuslib.devices.factory import open_device
from sartoriuslib.errors import (
    SartoriusConnectionError,
    SartoriusError,
    SartoriusProtocolUnsupportedError,
    SartoriusValidationError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import (
    decode_long_measurement_body,
    decode_measurement_body,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from anyserial import PortInfo

    from sartoriuslib.protocol.xbpi.types import XbpiFrame
    from sartoriuslib.transport.base import SerialSettings, Transport

__all__ = [
    "JitterDecodedFrame",
    "JitterSample",
    "collect_jitter_samples",
    "main",
    "summarize_samples",
]


type FrameMode = Literal["long", "short"]
type JsonValue = str | int | float | bool | dict[str, JsonValue] | list[JsonValue] | None
type RowValue = str | int | float | bool | None

_DEFAULT_RATE_HZ: float = 50.0
_DEFAULT_DURATION_S: float = 10.0
_DEFAULT_SPIN_MS: float = 0.0
_XBPI_LONG_ARGS: bytes = b"\x09\x30"
_MS_PER_S: float = 1_000.0
_NS_PER_S: int = 1_000_000_000
_CHECKPOINT_REMAINING_NS: int = 500_000


class RawXbpiBalance(Protocol):
    """Minimal balance surface needed by the jitter loop."""

    async def raw_xbpi(
        self,
        opcode: int,
        args: bytes = b"",
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> XbpiFrame:
        """Send a raw xBPI command and return the decoded frame."""
        ...


@dataclass(frozen=True, slots=True)
class JitterDecodedFrame:
    """Decoded xBPI weight frame fields relevant to timing diagnostics."""

    value: float | None
    unit: str
    sign: str
    stable: bool
    off_scale: bool
    overload: bool | None
    underload: bool | None
    decimals: int | None
    sequence: int | None
    adc_trusted: bool | None
    isocal_due: bool | None
    raw: str


@dataclass(frozen=True, slots=True)
class JitterSample:
    """One acquisition attempt with target, wake, poll, and decode timing."""

    index: int
    target_ns: int
    wake_ns: int
    requested_ns: int
    received_ns: int | None
    decoded: JitterDecodedFrame | None
    error: str | None
    error_type: str | None

    @property
    def midpoint_ns(self) -> int | None:
        """Host-side midpoint between request send and reply receive."""
        if self.received_ns is None:
            return None
        return (self.requested_ns + self.received_ns) // 2

    @property
    def latency_ms(self) -> float | None:
        """Round-trip latency in milliseconds."""
        if self.received_ns is None:
            return None
        return _ns_to_ms(self.received_ns - self.requested_ns)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag jitter",
        description=(
            "Read-only xBPI fixed-cadence timing probe. Defaults to 50 Hz "
            "using long net-weight frames with status/sequence data."
        ),
    )
    add_open_args(parser, port_required=False, protocol_default="xbpi")
    parser.add_argument(
        "--rate",
        type=float,
        default=_DEFAULT_RATE_HZ,
        help=f"Target poll rate in Hz (default: {_DEFAULT_RATE_HZ:g}).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=_DEFAULT_DURATION_S,
        help=f"Acquisition duration in seconds (default: {_DEFAULT_DURATION_S:g}).",
    )
    parser.add_argument(
        "--frame",
        choices=("long", "short"),
        default="long",
        help="xBPI weight frame to request: long=0x1E 09 30, short=0x1E (default: long).",
    )
    parser.add_argument(
        "--spin-ms",
        type=float,
        default=_DEFAULT_SPIN_MS,
        help=(
            "High-precision final wait window in milliseconds. "
            "0 disables spin-waiting; try 1-2 ms on Windows (default: 0)."
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Optional CSV path for per-sample timing diagnostics.",
    )
    parser.add_argument(
        "--summary-out",
        type=str,
        default=None,
        help="Optional JSON path for the computed timing summary.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary on stdout.",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    _validate_run_args(args.rate, args.duration, spin_ms=args.spin_ms)
    port, protocol, settings = await _resolve_jitter_open_args(args)
    frame_mode = _frame_mode(args.frame)

    async with await open_device(
        port,
        protocol=protocol,
        serial_settings=settings,
        timeout=args.timeout,
        identify=False,
    ) as balance:
        if balance.session.active_protocol is not ProtocolKind.XBPI:
            raise SartoriusProtocolUnsupportedError(
                "sarto-diag jitter requires an xBPI session; pass --protocol xbpi "
                "or switch the balance to xBPI before running the probe",
            )
        samples = await collect_jitter_samples(
            balance,
            rate_hz=args.rate,
            duration=args.duration,
            frame=frame_mode,
            timeout=args.timeout,
            spin_ms=args.spin_ms,
        )

    summary = summarize_samples(samples, rate_hz=args.rate, duration=args.duration)
    summary["port"] = _port_label(port)
    summary["frame"] = frame_mode
    summary["spin_ms"] = args.spin_ms

    if args.out is not None:
        _write_csv(Path(args.out), samples, rate_hz=args.rate)
        summary["out"] = args.out
    if args.summary_out is not None:
        _write_json(Path(args.summary_out), summary)

    if not args.quiet:
        sys.stdout.write(_format_summary(summary))
    return 0


async def collect_jitter_samples(
    balance: RawXbpiBalance,
    *,
    rate_hz: float = _DEFAULT_RATE_HZ,
    duration: float = _DEFAULT_DURATION_S,
    frame: FrameMode = "long",
    timeout: float | None = None,
    spin_ms: float = _DEFAULT_SPIN_MS,
) -> list[JitterSample]:
    """Collect xBPI timing samples from ``balance`` at an absolute cadence."""
    _validate_run_args(rate_hz, duration, spin_ms=spin_ms)
    period_s = 1.0 / rate_hz
    period_ns = round(period_s * _NS_PER_S)
    total_samples = max(1, round(duration * rate_hz))
    start_time = anyio.current_time()
    start_ns = time.perf_counter_ns()
    samples: list[JitterSample] = []

    for index in range(total_samples):
        target_time = start_time + index * period_s
        target_ns = start_ns + index * period_ns
        await _wait_until_target(target_time, target_ns, spin_ms=spin_ms)
        wake_ns = time.perf_counter_ns()
        requested_ns = time.perf_counter_ns()
        decoded: JitterDecodedFrame | None
        error: str | None = None
        error_type: str | None = None
        try:
            reply = await balance.raw_xbpi(
                0x1E,
                _XBPI_LONG_ARGS if frame == "long" else b"",
                timeout=timeout,
            )
            received_ns = time.perf_counter_ns()
            decoded = _decode_frame(reply, frame_mode=frame)
        except SartoriusError as exc:
            received_ns = time.perf_counter_ns()
            decoded = None
            error = str(exc)
            error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
        samples.append(
            JitterSample(
                index=index,
                target_ns=target_ns,
                wake_ns=wake_ns,
                requested_ns=requested_ns,
                received_ns=received_ns,
                decoded=decoded,
                error=error,
                error_type=error_type,
            )
        )
    return samples


def summarize_samples(
    samples: Sequence[JitterSample],
    *,
    rate_hz: float,
    duration: float,
) -> dict[str, JsonValue]:
    """Compute aggregate timing diagnostics for an acquisition run."""
    _validate_run_args(rate_hz, duration)
    period_ms = _MS_PER_S / rate_hz
    successful = [s for s in samples if s.decoded is not None]
    rows = _rows(samples, rate_hz=rate_hz)
    sequence_deltas = [
        int(row["sequence_delta"]) for row in rows if isinstance(row["sequence_delta"], int)
    ]

    duplicate_sequences = sum(1 for delta in sequence_deltas if delta == 0)
    sequence_gaps = sum(max(0, delta - 1) for delta in sequence_deltas if delta > 1)

    return {
        "rate_hz": rate_hz,
        "period_ms": period_ms,
        "duration_s": duration,
        "target_samples": max(1, round(duration * rate_hz)),
        "attempts": len(samples),
        "ok": len(successful),
        "errors": len(samples) - len(successful),
        "deadline_misses": sum(
            1 for s in samples if _ns_to_ms(s.wake_ns - s.target_ns) > period_ms
        ),
        "latency_over_period": sum(
            1 for s in samples if s.latency_ms is not None and s.latency_ms > period_ms
        ),
        "sequence_duplicates": duplicate_sequences,
        "sequence_gaps": sequence_gaps,
        "scheduler_drift_ms": _stats(_ns_to_ms(s.wake_ns - s.target_ns) for s in samples),
        "latency_ms": _stats(s.latency_ms for s in samples),
        "requested_interval_ms": _stats(_only_float(row["requested_interval_ms"]) for row in rows),
        "received_interval_ms": _stats(_only_float(row["received_interval_ms"]) for row in rows),
        "midpoint_interval_ms": _stats(_only_float(row["midpoint_interval_ms"]) for row in rows),
        "requested_jitter_ms": _jitter_stats(
            _only_float(row["requested_jitter_ms"]) for row in rows
        ),
        "received_jitter_ms": _jitter_stats(_only_float(row["received_jitter_ms"]) for row in rows),
        "midpoint_jitter_ms": _jitter_stats(_only_float(row["midpoint_jitter_ms"]) for row in rows),
    }


def _validate_run_args(
    rate_hz: float,
    duration: float,
    *,
    spin_ms: float = _DEFAULT_SPIN_MS,
) -> None:
    if rate_hz <= 0:
        raise SartoriusValidationError(f"--rate must be > 0, got {rate_hz!r}")
    if duration <= 0:
        raise SartoriusValidationError(f"--duration must be > 0, got {duration!r}")
    if spin_ms < 0:
        raise SartoriusValidationError(f"--spin-ms must be >= 0, got {spin_ms!r}")
    period_ms = _MS_PER_S / rate_hz
    if spin_ms >= period_ms:
        raise SartoriusValidationError(
            f"--spin-ms must be shorter than the {period_ms:.3f} ms sample period",
        )


async def _wait_until_target(target_time: float, target_ns: int, *, spin_ms: float) -> None:
    """Sleep until target, optionally spinning through the final window."""
    if spin_ms <= 0:
        now = anyio.current_time()
        if now < target_time:
            await anyio.sleep_until(target_time)
        return

    spin_s = spin_ms / _MS_PER_S
    sleep_target = target_time - spin_s
    now = anyio.current_time()
    if now < sleep_target:
        await anyio.sleep_until(sleep_target)

    while True:
        remaining_ns = target_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            return
        if remaining_ns > _CHECKPOINT_REMAINING_NS:
            await checkpoint()


async def _resolve_jitter_open_args(
    args: argparse.Namespace,
) -> tuple[str | Transport, ProtocolKind, SerialSettings | None]:
    if args.fixture is None and args.port is None:
        args.port = await _auto_single_port()
    return resolve_open_args(args)


async def _auto_single_port() -> str:
    try:
        ports = sorted(await list_serial_ports(), key=lambda p: p.device)
    except Exception as exc:
        raise SartoriusConnectionError(f"could not enumerate serial ports: {exc}") from exc
    if not ports:
        raise SartoriusValidationError("no serial ports found; pass PORT explicitly")
    if len(ports) == 1:
        port = ports[0]
        sys.stderr.write(f"auto-selected serial port: {_format_port(port)}\n")
        return port.device
    formatted = "\n".join(f"  {_format_port(p)}" for p in ports)
    raise SartoriusValidationError(
        f"multiple serial ports found; pass the Sartorius balance PORT explicitly:\n{formatted}",
    )


def _format_port(port: PortInfo) -> str:
    details = ", ".join(
        part
        for part in (port.description, port.manufacturer, port.product, port.serial_number)
        if part
    )
    return f"{port.device} ({details})" if details else port.device


def _port_label(port: str | Transport) -> str:
    return port if isinstance(port, str) else port.label


def _frame_mode(value: str) -> FrameMode:
    if value == "long":
        return "long"
    if value == "short":
        return "short"
    raise AssertionError(f"unreachable frame mode: {value!r}")


def _decode_frame(reply: XbpiFrame, *, frame_mode: FrameMode) -> JitterDecodedFrame:
    if frame_mode == "long":
        decoded = decode_long_measurement_body(reply.body)
        measurement = decoded.measurement
        status = decoded.status
        return JitterDecodedFrame(
            value=measurement.value,
            unit=measurement.unit.value,
            sign=measurement.sign.value,
            stable=measurement.stable,
            off_scale=measurement.off_scale,
            overload=status.overload,
            underload=status.underload,
            decimals=measurement.decimals,
            sequence=status.sequence,
            adc_trusted=status.adc_trusted,
            isocal_due=status.isocal_due,
            raw=reply.raw.hex(),
        )
    measurement = decode_measurement_body(reply.body)
    return JitterDecodedFrame(
        value=measurement.value,
        unit=measurement.unit.value,
        sign=measurement.sign.value,
        stable=measurement.stable,
        off_scale=measurement.off_scale,
        overload=None,
        underload=None,
        decimals=measurement.decimals,
        sequence=None,
        adc_trusted=None,
        isocal_due=None,
        raw=reply.raw.hex(),
    )


def _rows(samples: Sequence[JitterSample], *, rate_hz: float) -> list[dict[str, RowValue]]:
    period_ms = _MS_PER_S / rate_hz
    if not samples:
        return []
    start_ns = samples[0].target_ns
    prev_requested_ns: int | None = None
    prev_received_ns: int | None = None
    prev_midpoint_ns: int | None = None
    prev_sequence: int | None = None
    rows: list[dict[str, RowValue]] = []
    for sample in samples:
        requested_interval = _interval_ms(sample.requested_ns, prev_requested_ns)
        received_interval = _interval_ms(sample.received_ns, prev_received_ns)
        midpoint = sample.midpoint_ns
        midpoint_interval = _interval_ms(midpoint, prev_midpoint_ns)
        sequence = sample.decoded.sequence if sample.decoded is not None else None
        sequence_delta = _sequence_delta(sequence, prev_sequence)
        row = _base_row(
            sample,
            start_ns=start_ns,
            requested_interval_ms=requested_interval,
            received_interval_ms=received_interval,
            midpoint_interval_ms=midpoint_interval,
            sequence_delta=sequence_delta,
            period_ms=period_ms,
        )
        rows.append(row)
        prev_requested_ns = sample.requested_ns
        if sample.received_ns is not None:
            prev_received_ns = sample.received_ns
        if midpoint is not None:
            prev_midpoint_ns = midpoint
        if sequence is not None:
            prev_sequence = sequence
    return rows


def _base_row(
    sample: JitterSample,
    *,
    start_ns: int,
    requested_interval_ms: float | None,
    received_interval_ms: float | None,
    midpoint_interval_ms: float | None,
    sequence_delta: int | None,
    period_ms: float,
) -> dict[str, RowValue]:
    decoded = sample.decoded
    latency_ms = sample.latency_ms
    midpoint_ns = sample.midpoint_ns
    return {
        "index": sample.index,
        "target_elapsed_s": _ns_to_s(sample.target_ns - start_ns),
        "wake_elapsed_s": _ns_to_s(sample.wake_ns - start_ns),
        "wake_drift_ms": _ns_to_ms(sample.wake_ns - sample.target_ns),
        "requested_elapsed_s": _ns_to_s(sample.requested_ns - start_ns),
        "received_elapsed_s": (
            _ns_to_s(sample.received_ns - start_ns) if sample.received_ns is not None else None
        ),
        "midpoint_elapsed_s": _ns_to_s(midpoint_ns - start_ns) if midpoint_ns is not None else None,
        "latency_ms": latency_ms,
        "requested_interval_ms": requested_interval_ms,
        "received_interval_ms": received_interval_ms,
        "midpoint_interval_ms": midpoint_interval_ms,
        "requested_jitter_ms": _jitter_ms(requested_interval_ms, period_ms),
        "received_jitter_ms": _jitter_ms(received_interval_ms, period_ms),
        "midpoint_jitter_ms": _jitter_ms(midpoint_interval_ms, period_ms),
        "value": decoded.value if decoded is not None else None,
        "unit": decoded.unit if decoded is not None else None,
        "sign": decoded.sign if decoded is not None else None,
        "stable": decoded.stable if decoded is not None else None,
        "off_scale": decoded.off_scale if decoded is not None else None,
        "overload": decoded.overload if decoded is not None else None,
        "underload": decoded.underload if decoded is not None else None,
        "decimals": decoded.decimals if decoded is not None else None,
        "sequence": decoded.sequence if decoded is not None else None,
        "sequence_delta": sequence_delta,
        "sequence_gap": max(0, sequence_delta - 1) if sequence_delta is not None else None,
        "adc_trusted": decoded.adc_trusted if decoded is not None else None,
        "isocal_due": decoded.isocal_due if decoded is not None else None,
        "raw": decoded.raw if decoded is not None else None,
        "error_type": sample.error_type,
        "error_message": sample.error,
    }


def _write_csv(path: Path, samples: Sequence[JitterSample], *, rate_hz: float) -> None:
    rows = _rows(samples, rate_hz=rate_hz)
    columns = _row_columns()
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, JsonValue]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def _row_columns() -> list[str]:
    return [
        "index",
        "target_elapsed_s",
        "wake_elapsed_s",
        "wake_drift_ms",
        "requested_elapsed_s",
        "received_elapsed_s",
        "midpoint_elapsed_s",
        "latency_ms",
        "requested_interval_ms",
        "received_interval_ms",
        "midpoint_interval_ms",
        "requested_jitter_ms",
        "received_jitter_ms",
        "midpoint_jitter_ms",
        "value",
        "unit",
        "sign",
        "stable",
        "off_scale",
        "overload",
        "underload",
        "decimals",
        "sequence",
        "sequence_delta",
        "sequence_gap",
        "adc_trusted",
        "isocal_due",
        "raw",
        "error_type",
        "error_message",
    ]


def _format_summary(summary: dict[str, JsonValue]) -> str:
    lines = [
        "xBPI jitter acquisition complete:",
        f"  port:                 {summary.get('port')}",
        f"  frame:                {summary.get('frame')}",
        f"  spin_ms:              {_fmt(summary.get('spin_ms'))}",
        f"  rate_hz:              {_fmt(summary['rate_hz'])}",
        f"  period_ms:            {_fmt(summary['period_ms'])}",
        f"  duration_s:           {_fmt(summary['duration_s'])}",
        f"  samples:              {summary['ok']}/{summary['attempts']} ok",
        f"  errors:               {summary['errors']}",
        f"  deadline_misses:      {summary['deadline_misses']}",
        f"  latency_over_period:  {summary['latency_over_period']}",
        f"  sequence_gaps:        {summary['sequence_gaps']}",
        f"  sequence_duplicates:  {summary['sequence_duplicates']}",
        _format_stats_line("scheduler_drift_ms", summary["scheduler_drift_ms"]),
        _format_stats_line("latency_ms", summary["latency_ms"]),
        _format_stats_line("requested_jitter_ms", summary["requested_jitter_ms"]),
        _format_stats_line("received_jitter_ms", summary["received_jitter_ms"]),
        _format_stats_line("midpoint_jitter_ms", summary["midpoint_jitter_ms"]),
    ]
    if "out" in summary:
        lines.append(f"  out:                  {summary['out']}")
    return "\n".join(lines) + "\n"


def _format_stats_line(name: str, stats: JsonValue) -> str:
    if not isinstance(stats, dict) or stats.get("count") == 0:
        return f"  {name}:       n/a"
    if "max_abs" in stats:
        return (
            f"  {name}:       "
            f"std={_fmt(stats['std'])} p95_abs={_fmt(stats['p95_abs'])} "
            f"max_abs={_fmt(stats['max_abs'])}"
        )
    return (
        f"  {name}:       "
        f"mean={_fmt(stats['mean'])} p95={_fmt(stats['p95'])} "
        f"max={_fmt(stats['max'])}"
    )


def _stats(values: Iterable[float | None]) -> dict[str, JsonValue]:
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(finite),
        "min": min(finite),
        "max": max(finite),
        "mean": statistics.fmean(finite),
        "std": statistics.pstdev(finite) if len(finite) > 1 else 0.0,
        "p50": _percentile(finite, 50.0),
        "p95": _percentile(finite, 95.0),
        "p99": _percentile(finite, 99.0),
    }


def _jitter_stats(values: Iterable[float | None]) -> dict[str, JsonValue]:
    all_values = list(values)
    base = _stats(all_values)
    finite = [abs(v) for v in all_values if v is not None and math.isfinite(v)]
    if not finite:
        base.update({"max_abs": None, "p95_abs": None, "p99_abs": None})
        return base
    base.update(
        {
            "max_abs": max(finite),
            "p95_abs": _percentile(finite, 95.0),
            "p99_abs": _percentile(finite, 99.0),
        }
    )
    return base


def _percentile(values: Sequence[float], pct: float) -> float:
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _sequence_delta(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return (current - previous) % 256


def _interval_ms(current_ns: int | None, previous_ns: int | None) -> float | None:
    if current_ns is None or previous_ns is None:
        return None
    return _ns_to_ms(current_ns - previous_ns)


def _jitter_ms(interval_ms: float | None, period_ms: float) -> float | None:
    if interval_ms is None:
        return None
    return interval_ms - period_ms


def _only_float(value: RowValue) -> float | None:
    return value if isinstance(value, float) else None


def _ns_to_ms(ns: int) -> float:
    return ns / 1_000_000.0


def _ns_to_s(ns: int) -> float:
    return ns / _NS_PER_S


def _fmt(value: JsonValue) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "n/a"
    return str(value)
