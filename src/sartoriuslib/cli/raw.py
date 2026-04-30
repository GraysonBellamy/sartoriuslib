"""``sarto-raw`` — send one explicit xBPI opcode or SBI command, dump the reply.

Per design doc §13:

    sarto-raw PORT --xbpi 0x1E [HEX ...]
    sarto-raw PORT --sbi "ESC P" [--expect-lines 1]

Read-only safe-list opcodes / tokens run freely; anything else
requires ``--confirm``. The reply is rendered through the same
formatter as :mod:`sartoriuslib.cli.decode` so the on-wire bytes are
always recoverable from stdout.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from sartoriuslib.cli._common import (
    add_open_args,
    resolve_open_args,
    run_cli,
)
from sartoriuslib.cli.decode import decode_sbi_line, decode_xbpi_bytes
from sartoriuslib.devices.factory import open_device
from sartoriuslib.errors import SartoriusValidationError
from sartoriuslib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance

__all__ = ["main"]


_MAX_BYTE: int = 0xFF


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-raw",
        description="Send one xBPI opcode or SBI command and dump the response.",
    )
    add_open_args(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--xbpi",
        nargs="+",
        metavar="HEX",
        help=(
            "xBPI opcode + optional argument bytes as hex tokens. "
            "First token is the opcode (e.g. '0x1E' or '1e'); "
            "the remainder are concatenated TLV/argument bytes."
        ),
    )
    group.add_argument(
        "--sbi",
        metavar="TOKEN",
        help='SBI command token (e.g. "ESC P", "ESC x1_").',
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge a non-safe-listed opcode/token (DANGEROUS / PERSISTENT).",
    )
    parser.add_argument(
        "--expect-lines",
        type=int,
        default=1,
        help="SBI: how many CRLF reply lines to read (default: 1).",
    )
    args = parser.parse_args(argv)
    return run_cli(lambda: _async_main(args))


async def _async_main(args: argparse.Namespace) -> int:
    requested = ProtocolKind(args.protocol)
    if args.xbpi is not None and requested is ProtocolKind.SBI:
        raise SartoriusValidationError(
            "--xbpi requires an xBPI session; pass --protocol xbpi or auto.",
        )
    if args.sbi is not None and requested is ProtocolKind.XBPI:
        raise SartoriusValidationError(
            "--sbi requires an SBI session; pass --protocol sbi or auto.",
        )

    port, protocol, settings = resolve_open_args(args)
    bal = await open_device(
        port,
        protocol=protocol,
        serial_settings=settings,
        timeout=args.timeout,
        # No identify on raw — keeps the wire trace minimal so the user
        # sees only the single command they asked for.
        identify=False,
    )
    try:
        if args.xbpi is not None:
            await _run_xbpi(bal, args)
        else:
            await _run_sbi(bal, args)
    finally:
        await bal.close()
    return 0


async def _run_xbpi(bal: Balance, args: argparse.Namespace) -> None:
    opcode_hex, *arg_tokens = args.xbpi
    opcode = _parse_opcode_byte(opcode_hex)
    arg_bytes = _parse_arg_tokens(arg_tokens) if arg_tokens else b""

    frame = await bal.raw_xbpi(
        opcode,
        arg_bytes,
        confirm=args.confirm,
        timeout=args.timeout,
    )
    sys.stdout.write(decode_xbpi_bytes(frame.raw))


async def _run_sbi(bal: Balance, args: argparse.Namespace) -> None:
    reply = await bal.raw_sbi(
        args.sbi,
        confirm=args.confirm,
        timeout=args.timeout,
        expect_lines=args.expect_lines,
    )
    if not reply.lines:
        sys.stdout.write("(no reply — control token, expect_lines=0)\n")
        return
    for line in reply.lines:
        sys.stdout.write(decode_sbi_line(line.text))


def _parse_opcode_byte(token: str) -> int:
    """Parse one hex byte (with optional ``0x`` prefix) into an int 0..255."""
    cleaned = token.lower().removeprefix("0x")
    if not cleaned:
        raise SartoriusValidationError(f"--xbpi: empty opcode token {token!r}")
    try:
        value = int(cleaned, 16)
    except ValueError as exc:
        raise SartoriusValidationError(
            f"--xbpi: invalid opcode hex {token!r}",
        ) from exc
    if not 0 <= value <= _MAX_BYTE:
        raise SartoriusValidationError(
            f"--xbpi: opcode {token!r} must fit in one byte (0x00..0xFF)",
        )
    return value


def _parse_arg_tokens(tokens: list[str]) -> bytes:
    """Concatenate hex tokens into argument bytes, tolerating ``0x`` prefixes."""
    cleaned_parts: list[str] = []
    for token in tokens:
        cleaned = token.lower().removeprefix("0x").replace(":", "").replace(",", "")
        cleaned_parts.append(cleaned)
    cleaned = "".join(cleaned_parts)
    if not cleaned:
        return b""
    if len(cleaned) % 2 != 0:
        raise SartoriusValidationError(
            f"--xbpi: hex argument length must be even, got {len(cleaned)} chars",
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise SartoriusValidationError(
            f"--xbpi: invalid hex argument {tokens!r}",
        ) from exc
