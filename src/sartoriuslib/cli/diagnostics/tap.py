"""``sarto-diag tap`` — passive line capture (read-only).

Opens the transport, reads complete CRLF-terminated lines for the
configured duration, and writes them to stdout (or a file) as they
arrive. Never writes to the device. Useful for capturing SBI
autoprint output from a balance whose protocol mode is already
configured for autoprint.

Read-only. No safety gate required.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from sartoriuslib.cli._common import (
    add_open_args,
    resolve_open_args,
    run_cli,
)
from sartoriuslib.errors import SartoriusTimeoutError
from sartoriuslib.protocol.sbi.framing import LINE_TERMINATOR
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport

__all__ = ["capture_lines", "main"]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag tap",
        description=(
            "Passive line capture — reads complete CRLF lines for a fixed "
            "duration and prints them. Never writes to the device."
        ),
    )
    add_open_args(parser)
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Capture window in seconds (default: 5.0).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Append captured lines to this file (default: stdout).",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=None,
        help="Stop after capturing this many lines (default: unlimited).",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    port, _protocol, settings = resolve_open_args(args)
    transport = _resolve_transport(port, settings)
    if not transport.is_open:
        await transport.open()
    try:
        captured_lines = await capture_lines(
            transport,
            duration=args.duration,
            max_lines=args.max_lines,
        )
    finally:
        await transport.close()

    if args.out is not None:
        with Path(args.out).open("a", encoding="utf-8") as fh:
            fh.writelines(f"{line}\n" for line in captured_lines)
    else:
        for line in captured_lines:
            sys.stdout.write(f"{line}\n")
    sys.stdout.write(f"tap: captured {len(captured_lines)} line(s)\n")
    return 0


async def capture_lines(
    transport: Transport,
    *,
    duration: float,
    max_lines: int | None = None,
) -> list[str]:
    """Read complete CRLF lines from ``transport`` for ``duration`` seconds.

    Used both by :func:`_async_main` (the CLI driver) and by tests that
    want to feed a pre-loaded :class:`FakeTransport` without going
    through the argv-parsing path. The transport must already be open;
    the caller owns close().
    """
    captured: list[str] = []
    deadline = anyio.current_time() + duration
    while anyio.current_time() < deadline:
        if max_lines is not None and len(captured) >= max_lines:
            break
        remaining = max(0.001, deadline - anyio.current_time())
        try:
            raw = await transport.read_until(LINE_TERMINATOR, timeout=remaining)
        except SartoriusTimeoutError:
            break
        captured.append(raw.decode("ascii", errors="replace").rstrip("\r\n"))
    return captured


def _resolve_transport(
    port_or_transport: str | Transport,
    settings: SerialSettings | None,
) -> Transport:
    """Build a :class:`SerialTransport` from a port string, or pass through."""
    if isinstance(port_or_transport, str):
        s = settings if settings is not None else SerialSettings(port=port_or_transport)
        return SerialTransport(s)
    return port_or_transport
