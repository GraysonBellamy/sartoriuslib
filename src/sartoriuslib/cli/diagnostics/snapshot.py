"""``sarto-diag snapshot`` — dump everything the balance will tell us.

Walks every opcode in
:data:`sartoriuslib.commands.raw.SAFE_READ_ONLY_OPCODES`, sends each
through ``raw_xbpi``, and records the result. Useful for capability
discovery on a new firmware revision: the output reveals which
opcodes the family supports, which are sticky-unsupported, and what
their reply shapes look like.

Read-only. Never writes anything destructive.
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
from sartoriuslib.commands.raw import SAFE_READ_ONLY_OPCODES
from sartoriuslib.devices.factory import open_device
from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.xbpi.tables import OPCODE_NAMES

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag snapshot",
        description=(
            "Send every read-only safe-listed xBPI opcode and record the result. "
            "Capability-discovery aid; read-only by construction."
        ),
    )
    add_open_args(parser)
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write JSON results to FILE instead of human-readable text on stdout.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        type=lambda s: int(s, 0),
        default=None,
        help="Restrict the sweep to this explicit opcode list (default: all safe).",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    port, protocol, settings = resolve_open_args(args)
    bal = await open_device(
        port,
        protocol=protocol,
        serial_settings=settings,
        timeout=args.timeout,
        identify=False,
    )
    try:
        results = await _probe_all(
            bal,
            opcodes=(args.include if args.include is not None else sorted(SAFE_READ_ONLY_OPCODES)),
            timeout=args.timeout,
        )
    finally:
        await bal.aclose()

    if args.out is not None:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        sys.stdout.write(f"snapshot: wrote {len(results)} results to {args.out}\n")
    else:
        sys.stdout.write(_format_text(results))
    return 0


async def _probe_all(
    bal: Balance,
    *,
    opcodes: list[int],
    timeout: float,
) -> list[dict[str, object]]:
    """Send each ``opcode`` through ``raw_xbpi`` and record the outcome.

    Each entry in the returned list is JSON-friendly: ``opcode``,
    ``name``, ``status`` (``ok`` / ``error``), ``raw`` / ``body`` /
    ``subtype`` on success, ``error_type`` / ``error_message`` on
    failure.
    """
    results: list[dict[str, object]] = []
    for opcode in opcodes:
        entry: dict[str, object] = {
            "opcode": f"0x{opcode:02x}",
            "name": OPCODE_NAMES.get(opcode),
        }
        try:
            frame = await bal.raw_xbpi(opcode, timeout=timeout)
        except SartoriusError as exc:
            entry["status"] = "error"
            entry["error_type"] = type(exc).__name__
            entry["error_message"] = str(exc)
        else:
            entry["status"] = "ok"
            entry["subtype"] = f"0x{frame.subtype:02x}"
            entry["raw"] = frame.raw.hex()
            entry["body"] = frame.body.hex()
        results.append(entry)
    return results


def _format_text(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    n_ok = sum(1 for r in results if r["status"] == "ok")
    lines.append(f"snapshot: {n_ok}/{len(results)} opcodes responded")
    lines.append("")
    for r in results:
        opcode = r["opcode"]
        name = r["name"] or "(unmapped)"
        status = r["status"]
        if status == "ok":
            lines.append(
                f"  {opcode}  {name:32s}  ok       subtype={r['subtype']}  body={r['body']}",
            )
        else:
            err_t = r.get("error_type", "")
            msg = r.get("error_message", "")
            lines.append(f"  {opcode}  {name:32s}  {err_t}: {msg}")
    return "\n".join(lines) + "\n"
