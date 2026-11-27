"""``sarto-diag adjust-trace`` — record what a balance reports during an internal adjust.

**Destructive.** Sends xBPI ``0x28 21 <cal-type>`` (a real internal
adjustment: the internal weight moves and the calibration is rewritten)
unless ``--dry-run``. Requires ``--i-understand-this-is-destructive``.

Polls the balance before, during and after the adjustment and writes every
exchange to JSONL with monotonic timestamps. Partial or late frames are
captured verbatim (the transport is driven directly, not through the
protocol client). This is how ``docs/protocol.md`` §7.7.1 was produced. Run
it on another family or firmware to see whether the same signals apply.

Modes:

* default: every ``--period`` seconds, ``0x30`` status block, ``0x1E``
  net weight, ``0xB9`` cal record, plus ``--extra-ops``; every fifth
  cycle also ``0x32``, ``0xBA``, ``0x20``, ``0x76 21 00``. Each JSONL line
  carries ``t``, ``wall``, ``tag``, ``op``, ``args``, ``kind``
  (``ok``/``err``/``timeout``/``frame_error``), ``lat_ms``, ``raw``,
  ``subtype``, ``body``, ``err`` and a decoded view ``dec``.
* ``--fast``: back-to-back ``0x32 ×4, 0x1E, 0xB9`` for sub-10 ms
  resolution of state transitions. Lines are ``{t, op, body}``.

Stops ``--post`` seconds after ``0xB9`` changes, or ``--max`` seconds
after ``0x28``. ``--abort-after`` / ``--reenter-after`` send ``0x29`` /
a second ``0x28`` that many seconds in. Prints a run-length summary of the
state/status bytes relative to ``0x28``.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import anyio

from sartoriuslib.cli._common import add_open_args, resolve_open_args, run_cli
from sartoriuslib.cli.diagnostics._gate import require_destructive_ack
from sartoriuslib.devices.factory import open_device
from sartoriuslib.errors import (
    SartoriusError,
    SartoriusTimeoutError,
    SartoriusTransientTransportError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import (
    build_command,
    decode_measurement_body,
    decode_status_block_body,
    parse_frame,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sartoriuslib.devices.balance import Balance

__all__ = ["AdjustTraceSummary", "main", "trace_adjustment"]

_ADJUST_OPCODE = 0x28
_ABORT_OPCODE = 0x29
_STATUS_BLOCK = 0x30
_STATUS_SHORT = 0x32
_NET = 0x1E
_CAL_RECORD = 0xB9
_ERROR_SUBTYPE = 0x01
_DEFAULT_CAL_TYPE = 0x78
_SLOW_EVERY = 5
_STATUS_BLOCK_LEN = 8
_STATUS_SHORT_LEN = 2
_CAL_RECORD_LEN = 17
_STRAGGLER_IDLE_S = 0.15
_MAX_DIGIT = 9
_FAST_PATTERN: tuple[int, ...] = (
    _STATUS_SHORT,
    _STATUS_SHORT,
    _STATUS_SHORT,
    _STATUS_SHORT,
    _NET,
    _CAL_RECORD,
)
_SLOW_OPS: tuple[tuple[int, bytes], ...] = (
    (_STATUS_SHORT, b""),
    (0xBA, b""),
    (0x20, b""),
    (0x76, b"\x21\x00"),
)


@dataclass(frozen=True, slots=True)
class AdjustTraceSummary:
    """What one trace saw. Times are seconds since the trace started."""

    t_adjust: float | None
    adjust_reply: str | None
    t_record_changed: float | None
    transitions: tuple[tuple[float, int, int], ...]
    exchanges: int
    failures: int


@dataclass
class _Tracer:
    bal: Balance
    out: TextIO
    fast: bool
    poll_timeout: float
    t0: float = field(default_factory=time.monotonic)
    exchanges: int = 0
    failures: int = 0
    transitions: list[tuple[float, int, int]] = field(default_factory=list[tuple[float, int, int]])
    baseline_record: str | None = None
    t_record_changed: float | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    async def exchange(
        self, opcode: int, args: bytes = b"", *, tag: str = "poll"
    ) -> dict[str, Any]:
        session = self.bal.session
        transport = session.transport
        request = build_command(opcode, args, src_sbn=session.src_sbn, dst_sbn=session.dst_sbn)
        t_send = self.now()
        rec: dict[str, Any] = {"t": round(t_send, 4), "tag": tag, "op": f"0x{opcode:02x}"}
        raw = b""
        try:
            await transport.write(request, timeout=self.poll_timeout)
            length = await transport.read_exact(1, timeout=self.poll_timeout)
            raw = length + await transport.read_exact(length[0], timeout=self.poll_timeout)
            rec["kind"] = "ok"
        except (SartoriusTimeoutError, SartoriusTransientTransportError) as exc:
            raw = await transport.read_available(idle_timeout=_STRAGGLER_IDLE_S)
            await transport.drain_input()
            rec["kind"] = "timeout"
            rec["exc"] = type(exc).__name__
        rec["lat_ms"] = round((self.now() - t_send) * 1000, 1)
        rec["raw"] = raw.hex(" ")
        body: bytes | None = None
        if rec["kind"] == "ok":
            try:
                frame = parse_frame(raw)
            except SartoriusError as exc:
                rec["kind"] = "frame_error"
                rec["exc"] = repr(exc)
                await transport.drain_input()
            else:
                body = bytes(frame.body)
                rec["subtype"] = f"0x{frame.subtype:02x}"
                rec["body"] = body.hex(" ")
                if frame.subtype == _ERROR_SUBTYPE:
                    rec["kind"] = "err"
                    rec["err"] = f"0x{body[0]:02x}" if body else None
                    body = None
        self.exchanges += 1
        if rec["kind"] != "ok":
            self.failures += 1
        if body is not None:
            self._observe(opcode, body, t_send, rec)
        self._write(rec, args, body)
        return rec

    def _observe(self, opcode: int, body: bytes, t: float, rec: dict[str, Any]) -> None:
        pair: tuple[int, int] | None = None
        if opcode == _STATUS_BLOCK and len(body) == _STATUS_BLOCK_LEN:
            pair = (body[3], body[4])
        elif opcode == _STATUS_SHORT and len(body) == _STATUS_SHORT_LEN:
            pair = (body[0], body[1])
        if pair is not None and (not self.transitions or self.transitions[-1][1:] != pair):
            self.transitions.append((t, *pair))
        if opcode == _CAL_RECORD and len(body) == _CAL_RECORD_LEN:
            hexed = body.hex()
            if self.baseline_record is None:
                self.baseline_record = hexed
            elif hexed != self.baseline_record and self.t_record_changed is None:
                self.t_record_changed = t
                rec["record_changed"] = True

    def _write(self, rec: dict[str, Any], args: bytes, body: bytes | None) -> None:
        if self.fast and rec["tag"] == "poll":
            line: dict[str, Any] = {
                "t": rec["t"],
                "op": rec["op"],
                "body": rec.get("body", "").replace(" ", "") if body is not None else None,
            }
        else:
            line = {"wall": datetime.now(UTC).isoformat(), "args": args.hex(" "), **rec}
            if body is not None:
                line["dec"] = _decode(int(rec["op"], 16), body)
        self.out.write(json.dumps(line) + "\n")


def _decode(opcode: int, body: bytes) -> dict[str, Any] | None:
    """Best-effort decoded view for the JSONL (never raises)."""
    try:
        if opcode == _STATUS_BLOCK and len(body) == _STATUS_BLOCK_LEN:
            sb = decode_status_block_body(body)
            return {
                "state": sb.state,
                "status": sb.status,
                "seq": sb.sequence,
                "stable": sb.stable,
                "adjusting": sb.adjusting,
                "isocal_due": sb.isocal_due,
            }
        if opcode in (_NET, 0x20) and len(body) == _STATUS_BLOCK_LEN:
            m = decode_measurement_body(body)
            return {
                "value": m.value,
                "stable": m.stable,
                "off_scale": m.off_scale,
                "flags": m.flags,
            }
        if opcode == _CAL_RECORD and len(body) == _CAL_RECORD_LEN:
            digits = body[4:16]
            temp = None if body[:4] == bytes(4) else struct.unpack(">f", body[:4])[0]
            stamp = "".join(map(str, digits)) if all(d <= _MAX_DIGIT for d in digits) else None
            return {"temp": temp, "stamp_ddmmyyhhmmss": stamp}
    except (SartoriusError, struct.error):
        return None
    return None


async def trace_adjustment(
    bal: Balance,
    out: TextIO,
    *,
    fast: bool = False,
    dry_run: bool = False,
    pre_s: float = 5.0,
    max_s: float = 300.0,
    post_s: float = 30.0,
    period_s: float = 0.2,
    poll_timeout: float = 0.4,
    abort_after: float | None = None,
    reenter_after: float | None = None,
    extra_ops: Sequence[int] = (),
    cal_type: int = _DEFAULT_CAL_TYPE,
) -> AdjustTraceSummary:
    """Trace one internal adjustment (or, with ``dry_run``, just ``pre_s`` of rest)."""
    tracer = _Tracer(bal=bal, out=out, fast=fast, poll_timeout=poll_timeout)
    plan = _Plan(
        adjust_args=bytes([0x21, cal_type]),
        send_adjust_at=None if dry_run else pre_s,
        abort_after=abort_after,
        reenter_after=reenter_after,
    )
    cycle = 0
    while True:
        t_cycle = tracer.now()
        await plan.act(tracer, t_cycle)
        await _poll_cycle(tracer, cycle, extra_ops)
        cycle += 1
        now = tracer.now()
        if dry_run and now >= pre_s:
            break
        if tracer.t_record_changed is not None and now >= tracer.t_record_changed + post_s:
            break
        if plan.t_adjust is not None and now >= plan.t_adjust + max_s:
            break
        if not fast:
            await anyio.sleep(max(0.0, t_cycle + period_s - tracer.now()))
    return AdjustTraceSummary(
        t_adjust=plan.t_adjust,
        adjust_reply=plan.adjust_reply,
        t_record_changed=tracer.t_record_changed,
        transitions=tuple(tracer.transitions),
        exchanges=tracer.exchanges,
        failures=tracer.failures,
    )


@dataclass
class _Plan:
    """The scripted actions: 0x28 at ``send_adjust_at``, then optional 0x29 / re-0x28."""

    adjust_args: bytes
    send_adjust_at: float | None
    abort_after: float | None
    reenter_after: float | None
    t_adjust: float | None = None
    adjust_reply: str | None = None
    aborted: bool = False
    reentered: bool = False

    async def act(self, tracer: _Tracer, now: float) -> None:
        if self.t_adjust is None:
            if self.send_adjust_at is not None and now >= self.send_adjust_at:
                rec = await tracer.exchange(_ADJUST_OPCODE, self.adjust_args, tag="ADJUST")
                self.t_adjust = rec["t"]
                self.adjust_reply = rec.get("err") or rec["kind"]
            return
        since = now - self.t_adjust
        if self.abort_after is not None and not self.aborted and since >= self.abort_after:
            await tracer.exchange(_ABORT_OPCODE, tag="ABORT")
            self.aborted = True
        if self.reenter_after is not None and not self.reentered and since >= self.reenter_after:
            await tracer.exchange(_ADJUST_OPCODE, self.adjust_args, tag="REENTER")
            self.reentered = True


async def _poll_cycle(tracer: _Tracer, cycle: int, extra_ops: Sequence[int]) -> None:
    if tracer.fast:
        for opcode in _FAST_PATTERN:
            await tracer.exchange(opcode)
        return
    for opcode in (_STATUS_BLOCK, _NET, _CAL_RECORD, *extra_ops):
        await tracer.exchange(opcode)
    if cycle % _SLOW_EVERY == 0:
        for opcode, args in _SLOW_OPS:
            await tracer.exchange(opcode, args, tag="slow")


def format_summary(summary: AdjustTraceSummary) -> str:
    """Human-readable run-length table of the state/status bytes."""
    origin = summary.t_adjust or 0.0
    label = "0x28" if summary.t_adjust is not None else "start"
    lines = [
        f"adjust-trace: {summary.exchanges} exchanges, {summary.failures} without a usable reply",
        f"  0x28 reply: {summary.adjust_reply or '(not sent)'}",
    ]
    if summary.t_record_changed is not None:
        lines.append(f"  0xB9 changed at {summary.t_record_changed - origin:+.3f} s after {label}")
    else:
        lines.append("  0xB9 unchanged")
    lines.append(f"  state/status runs (seconds after {label}):")
    ends = [t for t, _, _ in summary.transitions[1:]] + [None]
    for (t, state, status), end in zip(summary.transitions, ends, strict=True):
        dur = f"{end - t:8.3f} s" if end is not None else "     ..."
        lines.append(f"    {t - origin:+9.3f}  {dur}  state=0x{state:02x} status=0x{status:02x}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag adjust-trace",
        description=(
            "Run an internal adjustment and record every exchange while it runs. "
            "Destructive — pass --i-understand-this-is-destructive (not needed "
            "with --dry-run)."
        ),
    )
    add_open_args(parser, protocol_default="xbpi")
    parser.add_argument("--out", required=True, help="JSONL output file.")
    parser.add_argument("--fast", action="store_true", help="Back-to-back status polls.")
    parser.add_argument("--dry-run", action="store_true", help="Never send 0x28.")
    parser.add_argument("--pre", type=float, default=5.0, help="Seconds before 0x28.")
    parser.add_argument("--max", type=float, default=300.0, help="Cap, seconds after 0x28.")
    parser.add_argument("--post", type=float, default=30.0, help="Seconds after 0xB9 changes.")
    parser.add_argument("--period", type=float, default=0.2, help="Cycle period (default mode).")
    parser.add_argument("--abort-after", type=float, default=None, help="Send 0x29 after S s.")
    parser.add_argument("--reenter-after", type=float, default=None, help="Resend 0x28 after S s.")
    parser.add_argument(
        "--extra-ops",
        default="",
        help="Comma-separated hex opcodes to also poll each cycle, no args (e.g. 77,7a).",
    )
    parser.add_argument(
        "--cal-type",
        type=lambda s: int(s, 0),
        default=_DEFAULT_CAL_TYPE,
        help="TLV-21 cal selector for 0x28 (default 0x78, internal adjust).",
    )
    parser.add_argument(
        "--i-understand-this-is-destructive",
        action="store_true",
        dest="ack_destructive",
        help="Required unless --dry-run: this runs a real internal adjustment.",
    )
    args = parser.parse_args(argv)
    if not args.dry_run:
        require_destructive_ack(acked=args.ack_destructive, op="adjust-trace")
    extra_ops = [int(tok, 16) for tok in args.extra_ops.split(",") if tok.strip()]
    with Path(args.out).open("w", encoding="utf-8") as out:
        return run_cli(lambda: _async_main(args, out, extra_ops))


async def _async_main(args: argparse.Namespace, out: TextIO, extra_ops: list[int]) -> int:
    port, protocol, settings = resolve_open_args(args)
    if protocol is not ProtocolKind.XBPI:
        sys.stderr.write("error: adjust-trace needs --protocol xbpi\n")
        return 1
    bal = await open_device(
        port, protocol=protocol, serial_settings=settings, timeout=args.timeout, identify=False
    )
    try:
        summary = await trace_adjustment(
            bal,
            out,
            fast=args.fast,
            dry_run=args.dry_run,
            pre_s=args.pre,
            max_s=args.max,
            post_s=args.post,
            period_s=args.period,
            abort_after=args.abort_after,
            reenter_after=args.reenter_after,
            extra_ops=extra_ops,
            cal_type=args.cal_type,
        )
    finally:
        await bal.close()
    sys.stdout.write(format_summary(summary))
    return 0
