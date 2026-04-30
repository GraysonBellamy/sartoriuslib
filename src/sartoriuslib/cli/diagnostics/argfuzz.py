"""``sarto-diag argfuzz`` — argument fuzzer for a single xBPI opcode.

Sends ``--opcode`` repeatedly with sequential or random argument
payloads and records the response per iteration. Used to map an
opcode's argument space — e.g. discover which TLV-21 indices a
parameter-table read accepts on a given firmware.

**Destructive.** The user picks the opcode; the library cannot
predict whether the chosen opcode mutates persistent state. Even an
ostensibly read-only opcode might exhibit unexpected side effects
under malformed args. Requires
``--i-understand-this-is-destructive``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sartoriuslib.cli._common import (
    add_open_args,
    resolve_open_args,
    run_cli,
)
from sartoriuslib.cli.diagnostics._gate import require_destructive_ack
from sartoriuslib.devices.factory import open_device
from sartoriuslib.errors import SartoriusError, SartoriusValidationError
from sartoriuslib.protocol.xbpi import encode_tlv

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance

__all__ = ["main"]


_DEFAULT_TLV_TAG: int = 0x21  # u8 — the most common parameter-table tag.
_MAX_BYTE: int = 0xFF


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag argfuzz",
        description=(
            "Send one xBPI opcode with sequential argument bytes and record "
            "each response. Destructive — pass "
            "--i-understand-this-is-destructive."
        ),
    )
    add_open_args(parser)
    parser.add_argument(
        "--opcode",
        type=lambda s: int(s, 0),
        required=True,
        help="Target opcode (e.g. 0x55 for parameter-table reads).",
    )
    parser.add_argument(
        "--mode",
        choices=("u8-sweep", "tlv21-sweep", "raw-bytes"),
        default="tlv21-sweep",
        help=(
            "u8-sweep: send the opcode with one bare u8 arg byte 0..0xFF. "
            "tlv21-sweep (default): wrap the value in a TLV-21 record. "
            "raw-bytes: send the opcode with each --raw-payload entry."
        ),
    )
    parser.add_argument(
        "--start",
        type=lambda s: int(s, 0),
        default=0x00,
        help="First arg value to try (default: 0x00).",
    )
    parser.add_argument(
        "--end",
        type=lambda s: int(s, 0),
        default=0x0F,
        help="Last arg value to try, inclusive (default: 0x0F).",
    )
    parser.add_argument(
        "--raw-payload",
        nargs="+",
        default=None,
        help=(
            "raw-bytes mode only: explicit hex payloads to try, one per "
            "iteration (e.g. '21 00' '21 01' 'ff ff')."
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write JSON results to FILE instead of human-readable text.",
    )
    parser.add_argument(
        "--i-understand-this-is-destructive",
        action="store_true",
        dest="ack_destructive",
        help="Required: acknowledge that the fuzzer may mutate persistent state.",
    )
    args = parser.parse_args(argv)
    require_destructive_ack(acked=args.ack_destructive, op="argfuzz")
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    if not 0 <= args.opcode <= _MAX_BYTE:
        sys.stderr.write("error: --opcode must fit in one byte (0..0xFF)\n")
        return 1
    payloads = _build_payloads(args)

    port, protocol, settings = resolve_open_args(args)
    bal = await open_device(
        port,
        protocol=protocol,
        serial_settings=settings,
        timeout=args.timeout,
        identify=False,
    )
    try:
        results = await _fuzz(bal, opcode=args.opcode, payloads=payloads, timeout=args.timeout)
    finally:
        await bal.close()

    if args.out is not None:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        sys.stdout.write(f"argfuzz: wrote {len(results)} results to {args.out}\n")
    else:
        sys.stdout.write(_format_text(results, opcode=args.opcode))
    return 0


def _build_payloads(args: argparse.Namespace) -> list[bytes]:
    """Resolve the chosen mode + range / explicit list into argument byte lists."""
    if args.mode == "raw-bytes":
        if not args.raw_payload:
            raise SartoriusValidationError(
                "--mode=raw-bytes requires --raw-payload <hex>...",
            )
        return [_parse_hex_payload(token) for token in args.raw_payload]
    if not 0 <= args.start <= _MAX_BYTE or not 0 <= args.end <= _MAX_BYTE:
        raise SartoriusValidationError(
            "--start and --end must each fit in one byte (0..0xFF)",
        )
    if args.start > args.end:
        raise SartoriusValidationError(
            f"--start (0x{args.start:02x}) > --end (0x{args.end:02x})",
        )
    values = range(args.start, args.end + 1)
    if args.mode == "u8-sweep":
        return [bytes([v]) for v in values]
    # tlv21-sweep
    return [encode_tlv(_DEFAULT_TLV_TAG, v) for v in values]


def _parse_hex_payload(token: str) -> bytes:
    """Parse a payload token from ``--raw-payload`` (whitespace-tolerant hex)."""
    cleaned = token.replace(" ", "").replace(":", "")
    if cleaned and len(cleaned) % 2 != 0:
        raise SartoriusValidationError(
            f"--raw-payload: hex token {token!r} has odd length",
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise SartoriusValidationError(
            f"--raw-payload: invalid hex token {token!r}",
        ) from exc


async def _fuzz(
    bal: Balance,
    *,
    opcode: int,
    payloads: list[bytes],
    timeout: float,
) -> list[dict[str, object]]:
    """Send ``opcode`` with each ``payload``; collect outcomes."""
    results: list[dict[str, object]] = []
    for payload in payloads:
        entry: dict[str, object] = {
            "args": payload.hex(),
        }
        try:
            frame = await bal.raw_xbpi(opcode, payload, confirm=True, timeout=timeout)
        except SartoriusError as exc:
            entry["status"] = "error"
            entry["error_type"] = type(exc).__name__
            entry["error_message"] = str(exc)
        else:
            entry["status"] = "ok"
            entry["subtype"] = f"0x{frame.subtype:02x}"
            entry["raw"] = frame.raw.hex()
        results.append(entry)
    return results


def _format_text(results: list[dict[str, object]], *, opcode: int) -> str:
    n_ok = sum(1 for r in results if r["status"] == "ok")
    lines = [
        f"argfuzz 0x{opcode:02x}: {n_ok}/{len(results)} args responded",
        "",
    ]
    for r in results:
        args = r["args"] or "(empty)"
        if r["status"] == "ok":
            lines.append(f"  args={args:24s}  ok       subtype={r['subtype']}")
        else:
            err_t = r.get("error_type", "")
            msg = r.get("error_message", "")
            lines.append(f"  args={args:24s}  {err_t}: {msg}")
    return "\n".join(lines) + "\n"
