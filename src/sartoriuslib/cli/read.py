"""``sarto-read`` — open, identify, print one reading.

Per design doc §13:

    sarto-read PORT [--protocol auto|xbpi|sbi]

Opens the balance, runs identify (unless ``--no-identify`` is passed),
calls :meth:`Balance.poll` once, and writes a human-readable summary
to stdout. Identical surface to :func:`sartoriuslib.open_device` plus
the shared serial-framing overrides.
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
from sartoriuslib.devices.factory import open_device

if TYPE_CHECKING:
    from sartoriuslib.devices.balance import Balance
    from sartoriuslib.devices.models import DeviceInfo, Reading

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="sarto-read",
        description="Open a balance, identify it, and print one reading.",
    )
    add_open_args(parser)
    parser.add_argument(
        "--no-identify",
        action="store_true",
        help="Skip the identify pass (smaller round-trip count, no DeviceInfo).",
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
        identify=not args.no_identify,
    )
    try:
        if not args.no_identify and bal.info is not None:
            sys.stdout.write(_format_info(bal.info))
        reading = await bal.poll()
        sys.stdout.write(_format_reading(reading, balance=bal))
    finally:
        await bal.close()
    return 0


def _format_info(info: DeviceInfo) -> str:
    """Multi-line block describing the identified device."""
    lines = [
        "device:",
        f"  model:        {info.model or '<unknown>'}",
        f"  manufacturer: {info.manufacturer or '<unknown>'}",
        f"  family:       {info.family.value}",
        f"  protocol:     {info.protocol.value}",
    ]
    if info.serial:
        lines.append(f"  serial:       {info.serial}")
    if info.software:
        lines.append(f"  software:     {info.software}")
    if info.capacity is not None:
        lines.append(f"  capacity:     {info.capacity.value:g} {info.capacity.unit.value}")
    if info.increment is not None:
        lines.append(f"  increment:    {info.increment.value:g} {info.increment.unit.value}")
    return "\n".join(lines) + "\n"


def _format_reading(reading: Reading, *, balance: Balance) -> str:
    """One-line + flags block for a single weight reading."""
    value_str = "<off-scale>" if reading.value is None else f"{reading.value:g}"
    lines = [
        "reading:",
        f"  value:     {value_str} {reading.unit.value}",
        f"  sign:      {reading.sign.value}",
        f"  stable:    {reading.stable}",
        f"  overload:  {reading.overload}",
        f"  underload: {reading.underload}",
        f"  decimals:  {reading.decimals}",
        f"  protocol:  {reading.protocol.value}",
    ]
    del balance  # reserved for future additions (sequence, status snapshot)
    return "\n".join(lines) + "\n"
