"""``sarto-discover`` — probe a port and report which protocol it speaks.

Per design doc §13:

    sarto-discover [PORT] [--baudrate ...] [--parity ...] [--stopbits ...]

Wraps :func:`sartoriuslib.devices.discovery.discover_port` — opens the
port at the given serial settings, runs the conservative
:func:`detect_protocol`, and prints a structured
:class:`DiscoveryResult` to stdout. No opcode sweeps, no fuzzing.

Wider serial-settings sweeps are deferred — the design doc leans
toward "user supplies serial params" (§16 Q2). This command answers
*which protocol is speaking now* at the framing the caller specified.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from sartoriuslib.cli._common import (
    add_open_args,
    resolve_open_args,
    run_cli,
)
from sartoriuslib.devices.discovery import discover_port

if TYPE_CHECKING:
    from sartoriuslib.devices.discovery import SartoriusDiscoveryResult

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-discover",
        description=(
            "Probe a serial port and report which Sartorius protocol it speaks. "
            "Conservative — no opcode sweeps, no baud sweeps."
        ),
    )
    add_open_args(parser, port_required=True)
    parser.add_argument(
        "--sniff-window",
        type=float,
        default=0.25,
        help="Passive autoprint sniff window in seconds (default: 0.25).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the DiscoveryResult as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    port, _protocol, settings = resolve_open_args(args)
    result = await discover_port(
        port,
        serial_settings=settings,
        timeout=args.timeout,
        sniff_window=args.sniff_window,
    )
    if args.json:
        sys.stdout.write(_format_json(result))
    else:
        sys.stdout.write(_format_text(result))
    # Return non-zero on detection failure so CI / scripts can branch.
    return 0 if result.ok else 2


def _format_text(result: SartoriusDiscoveryResult) -> str:
    lines = [
        f"port:        {result.port}",
        f"baudrate:    {result.baudrate}",
        f"parity:      {result.parity}",
        f"stopbits:    {result.stopbits}",
        f"elapsed_s:   {result.elapsed_s:.3f}",
    ]
    if result.protocol is None:
        lines.append("protocol:    <none — no responsive device>")
        if result.error:
            lines.append(f"error:       {result.error}")
    else:
        lines.append(f"protocol:    {result.protocol.value}")
        if result.address is not None:
            lines.append(f"address:     {result.address}")
        lines.append(f"autoprint:   {result.autoprint_active}")
        if result.pending_lines:
            lines.append(f"sniffed:     {len(result.pending_lines)} line(s)")
            lines.extend(f"             {line.rstrip()!r}" for line in result.pending_lines)
    return "\n".join(lines) + "\n"


def _format_json(result: SartoriusDiscoveryResult) -> str:
    payload = {
        "ok": result.ok,
        "port": result.port,
        "address": result.address,
        "baudrate": result.baudrate,
        "parity": result.parity,
        "stopbits": result.stopbits,
        "protocol": result.protocol.value if result.protocol is not None else None,
        "autoprint_active": result.autoprint_active,
        "pending_lines": [line.decode("ascii", errors="replace") for line in result.pending_lines],
        "error": str(result.error) if result.error is not None else None,
        "elapsed_s": result.elapsed_s,
    }
    return json.dumps(payload, indent=2) + "\n"
