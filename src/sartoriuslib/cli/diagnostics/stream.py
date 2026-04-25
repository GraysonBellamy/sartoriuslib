"""``sarto-diag stream`` — raw byte capture for protocol work.

Opens the transport, reads whatever bytes arrive over the configured
duration, and dumps them as space-separated hex to stdout (or a
file). Unlike :mod:`sartoriuslib.cli.diagnostics.tap`, this does not
require CRLF-terminated framing — useful for binary xBPI traffic
analysis where the framing itself is the unknown.

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
from sartoriuslib.transport.base import SerialSettings
from sartoriuslib.transport.serial import SerialTransport

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport

__all__ = ["capture_bytes", "main"]

_DEFAULT_DURATION_S: float = 5.0
_DEFAULT_IDLE_TIMEOUT_S: float = 0.1
_DEFAULT_CHUNK_BYTES: int = 256


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-diag stream",
        description=(
            "Raw byte capture — reads whatever the device emits for a fixed "
            "duration. Output is space-separated hex; never writes to the device."
        ),
    )
    add_open_args(parser)
    parser.add_argument(
        "--duration",
        type=float,
        default=_DEFAULT_DURATION_S,
        help=f"Capture window in seconds (default: {_DEFAULT_DURATION_S}).",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=_DEFAULT_IDLE_TIMEOUT_S,
        help=(
            f"Per-iteration idle timeout passed to read_available "
            f"(default: {_DEFAULT_IDLE_TIMEOUT_S})."
        ),
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=_DEFAULT_CHUNK_BYTES,
        help=f"Max bytes per read_available call (default: {_DEFAULT_CHUNK_BYTES}).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write captured hex to this file instead of stdout.",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    port, _protocol, settings = resolve_open_args(args)
    transport = _resolve_transport(port, settings)
    if not transport.is_open:
        await transport.open()
    try:
        captured = await capture_bytes(
            transport,
            duration=args.duration,
            idle_timeout=args.idle_timeout,
            chunk=args.chunk,
        )
    finally:
        await transport.close()

    hex_text = captured.hex(" ") if captured else ""
    payload = f"{hex_text}\n" if hex_text else ""
    if args.out is not None:
        Path(args.out).write_text(payload, encoding="utf-8")
    elif payload:
        sys.stdout.write(payload)
    sys.stdout.write(f"stream: captured {len(captured)} byte(s)\n")
    return 0


async def capture_bytes(
    transport: Transport,
    *,
    duration: float,
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_S,
    chunk: int = _DEFAULT_CHUNK_BYTES,
) -> bytes:
    """Read raw bytes from ``transport`` for ``duration`` seconds.

    Used both by :func:`_async_main` and by tests that want to feed a
    pre-loaded :class:`FakeTransport`. The transport must already be
    open; the caller owns close().
    """
    captured = bytearray()
    deadline = anyio.current_time() + duration
    while anyio.current_time() < deadline:
        remaining = max(0.001, deadline - anyio.current_time())
        bytes_chunk = await transport.read_available(
            idle_timeout=min(idle_timeout, remaining),
            max_bytes=chunk,
        )
        if bytes_chunk:
            captured.extend(bytes_chunk)
    return bytes(captured)


def _resolve_transport(
    port_or_transport: str | Transport,
    settings: SerialSettings | None,
) -> Transport:
    """Build a :class:`SerialTransport` from a port string, or pass through."""
    if isinstance(port_or_transport, str):
        s = settings if settings is not None else SerialSettings(port=port_or_transport)
        return SerialTransport(s)
    return port_or_transport
