"""``sarto-diag sweep`` — xBPI opcode sweep across a range.

Sends every opcode in ``[--start, --end]`` (inclusive) through
``raw_xbpi`` and records the response shape. Used for capability
discovery on unfamiliar firmware: the output reveals which opcodes
respond, which return error subtypes, which time out.

**Destructive.** A blanket sweep includes opcodes that mutate
persistent state (``0x5C`` set_baud_rate, ``0x72`` write_sbn_address,
``0x47`` save_menu_to_eeprom, ``0x46`` reload_menu_from_eeprom,
``0x28`` start_adjustment, ``0x58`` initiate_reset, ``0x56``
write_parameter_table, ``0x59`` / ``0x5A`` clear_xbpi_app_flag).
A built-in default exclude list shields those; pass ``--include-all``
to disable the shield. The ``--i-understand-this-is-destructive``
flag is required even with the shield because the underlying probe
still issues writes the library cannot fully predict.
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
from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.xbpi.tables import OPCODE_NAMES

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance

__all__ = ["DEFAULT_SWEEP_EXCLUDE", "main"]


_MAX_BYTE: int = 0xFF


# Opcodes whose effect on persistent state we do not want to trigger by
# accident during a discovery sweep. Each is documented as PERSISTENT
# or DANGEROUS in docs/protocol.md §7 / §16.
DEFAULT_SWEEP_EXCLUDE: frozenset[int] = frozenset(
    {
        0x28,  # start_adjustment
        0x46,  # reload_menu_from_eeprom
        0x47,  # save_menu_to_eeprom
        0x56,  # write_parameter_table
        0x58,  # initiate_reset
        0x59,  # clear_xbpi_app_flag
        0x5A,  # alias_of_0x59
        0x5C,  # set_baud_rate
        0x72,  # write_sbn_address
        0x79,  # write_adjustment_unit
    },
)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag sweep",
        description=(
            "Send every xBPI opcode in [start, end] and record the response "
            "shape. Destructive — pass --i-understand-this-is-destructive."
        ),
    )
    add_open_args(parser)
    parser.add_argument(
        "--start",
        type=lambda s: int(s, 0),
        default=0x00,
        help="First opcode to sweep (default: 0x00).",
    )
    parser.add_argument(
        "--end",
        type=lambda s: int(s, 0),
        default=0xFF,
        help="Last opcode to sweep, inclusive (default: 0xFF).",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help=(
            "Disable the built-in exclude shield "
            "(0x28/0x46/0x47/0x56/0x58/0x59/0x5A/0x5C/0x72/0x79)."
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
        help="Required: acknowledge that the sweep may mutate persistent state.",
    )
    args = parser.parse_args(argv)
    require_destructive_ack(acked=args.ack_destructive, op="sweep")
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    if not 0 <= args.start <= _MAX_BYTE or not 0 <= args.end <= _MAX_BYTE:
        sys.stderr.write("error: --start and --end must each fit in one byte (0..0xFF)\n")
        return 1
    if args.start > args.end:
        sys.stderr.write(f"error: --start (0x{args.start:02x}) > --end (0x{args.end:02x})\n")
        return 1
    excluded: frozenset[int] = frozenset() if args.include_all else DEFAULT_SWEEP_EXCLUDE
    opcodes = [op for op in range(args.start, args.end + 1) if op not in excluded]

    port, protocol, settings = resolve_open_args(args)
    bal = await open_device(
        port,
        protocol=protocol,
        serial_settings=settings,
        timeout=args.timeout,
        identify=False,
    )
    try:
        results = await _sweep(bal, opcodes=opcodes, timeout=args.timeout)
    finally:
        await bal.close()

    if args.out is not None:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        sys.stdout.write(f"sweep: wrote {len(results)} results to {args.out}\n")
    else:
        sys.stdout.write(_format_text(results, excluded=excluded))
    return 0


async def _sweep(
    bal: Balance,
    *,
    opcodes: list[int],
    timeout: float,
) -> list[dict[str, object]]:
    """Run :meth:`Balance.raw_xbpi` against each opcode with ``confirm=True``."""
    results: list[dict[str, object]] = []
    for opcode in opcodes:
        entry: dict[str, object] = {
            "opcode": f"0x{opcode:02x}",
            "name": OPCODE_NAMES.get(opcode),
        }
        try:
            frame = await bal.raw_xbpi(opcode, confirm=True, timeout=timeout)
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


def _format_text(
    results: list[dict[str, object]],
    *,
    excluded: frozenset[int],
) -> str:
    n_ok = sum(1 for r in results if r["status"] == "ok")
    lines = [
        f"sweep: {n_ok}/{len(results)} opcodes responded "
        f"(excluded: {len(excluded)} default-shielded)",
        "",
    ]
    for r in results:
        opcode = r["opcode"]
        name = r["name"] or "(unmapped)"
        if r["status"] == "ok":
            lines.append(f"  {opcode}  {name:32s}  ok       subtype={r['subtype']}")
        else:
            err_t = r.get("error_type", "")
            msg = r.get("error_message", "")
            lines.append(f"  {opcode}  {name:32s}  {err_t}: {msg}")
    return "\n".join(lines) + "\n"
