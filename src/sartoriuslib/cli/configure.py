"""``sarto-configure`` — confirmed configuration operations.

Per design doc §13 (and the dual-path note in §16 Q6), three
maintenance subcommands wrap :mod:`sartoriuslib.maintenance`:

    sarto-configure switch-protocol PORT --target {xbpi,sbi} \
        [--baudrate CURRENT] [--new-baudrate NEW] [--new-parity P] \
        [--new-stopbits {1,2}] --confirm

    sarto-configure set-baud-rate PORT \
        --wire-code N --target-baudrate N \
        [--baudrate CURRENT] [--new-parity P] [--new-stopbits {1,2}] \
        --confirm

    sarto-configure write-sbn-address PORT --sbn N \
        [--update-session-dst] --confirm

Every subcommand refuses without ``--confirm``. Output is a
human-readable summary of the post-change :class:`DeviceInfo`.

The standard ``--baudrate`` / ``--parity`` / ``--stopbits`` flags
describe the *current* serial framing the host opens at; the
``--new-*`` and ``--target-*`` flags describe what to switch to. The
front-panel menu change (where applicable) is the user's
responsibility before invoking this command — the CLI only reconciles
the host with the new device-side state.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from sartoriuslib.cli._common import (
    PARITY_CHOICES,
    STOPBITS_CHOICES,
    add_open_args,
    parity_from_name,
    resolve_open_args,
    run_cli,
    stopbits_from_number,
)
from sartoriuslib.errors import SartoriusValidationError
from sartoriuslib.maintenance import (
    set_baud_rate,
    switch_protocol,
    write_sbn_address,
)
from sartoriuslib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from sartoriuslib.devices.models import DeviceInfo

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    if not args.confirm:
        sys.stderr.write(
            f"error: sarto-configure {args.op} is destructive; pass --confirm to execute\n",
        )
        return 2
    return run_cli(lambda: _async_main(args))


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sarto-configure",
        description=(
            "Confirmed configuration operations — protocol switch, "
            "baud-rate change, SBN address write. Every subcommand "
            "requires --confirm."
        ),
    )
    subs = parser.add_subparsers(dest="op", required=True)

    sp_switch = subs.add_parser(
        "switch-protocol",
        help="Reconcile the host with a device-side protocol change (e.g. WZA flip).",
    )
    add_open_args(sp_switch)
    sp_switch.add_argument(
        "--target",
        choices=("xbpi", "sbi"),
        required=True,
        help="Target wire protocol the device is now in.",
    )
    sp_switch.add_argument(
        "--new-baudrate",
        type=int,
        default=None,
        help="Reopen the host at this baud after the switch (None = keep).",
    )
    sp_switch.add_argument(
        "--new-parity",
        choices=PARITY_CHOICES,
        default=None,
        help="Reopen the host at this parity after the switch (None = keep).",
    )
    sp_switch.add_argument(
        "--new-stopbits",
        type=int,
        choices=STOPBITS_CHOICES,
        default=None,
        help="Reopen the host at this stopbit count after the switch (None = keep).",
    )
    sp_switch.add_argument(
        "--confirm",
        action="store_true",
        help="Required: acknowledge the destructive nature of the operation.",
    )

    sp_baud = subs.add_parser(
        "set-baud-rate",
        help="Send xBPI 0x5C to change device baud, then reopen at the new baud.",
    )
    add_open_args(sp_baud)
    sp_baud.add_argument(
        "--wire-code",
        type=lambda s: int(s, 0),
        required=True,
        help="On-wire encoding from docs/protocol.md §7.10 "
        "(0x00=9600, 0x01=19200, 0x02=38400, 0x03=57600).",
    )
    sp_baud.add_argument(
        "--target-baudrate",
        type=int,
        required=True,
        help="Host-side baud the transport should reopen at.",
    )
    sp_baud.add_argument(
        "--new-parity",
        choices=PARITY_CHOICES,
        default=None,
        help="Reopen at this parity after the switch (None = keep).",
    )
    sp_baud.add_argument(
        "--new-stopbits",
        type=int,
        choices=STOPBITS_CHOICES,
        default=None,
        help="Reopen at this stopbit count (None = keep).",
    )
    sp_baud.add_argument(
        "--confirm",
        action="store_true",
        help="Required: acknowledge the destructive nature of the operation.",
    )

    sp_sbn = subs.add_parser(
        "write-sbn-address",
        help="Send xBPI 0x72 to change the device's bus address; verify via 0x71.",
    )
    add_open_args(sp_sbn)
    sp_sbn.add_argument(
        "--sbn",
        type=lambda s: int(s, 0),
        required=True,
        help="New SBN value (0..255).",
    )
    sp_sbn.add_argument(
        "--update-session-dst",
        action="store_true",
        help="Multidrop only: update dst_sbn so subsequent calls address the device.",
    )
    sp_sbn.add_argument(
        "--confirm",
        action="store_true",
        help="Required: acknowledge the destructive nature of the operation.",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    if args.op == "switch-protocol":
        return await _run_switch_protocol(args)
    if args.op == "set-baud-rate":
        return await _run_set_baud_rate(args)
    if args.op == "write-sbn-address":
        return await _run_write_sbn_address(args)
    raise SartoriusValidationError(  # pragma: no cover — argparse guards this
        f"unknown op {args.op!r}",
    )


async def _run_switch_protocol(args: argparse.Namespace) -> int:
    port, current_protocol, settings = resolve_open_args(args)
    info = await switch_protocol(
        port,
        ProtocolKind(args.target),
        current_protocol=current_protocol,
        serial_settings=settings,
        new_baudrate=args.new_baudrate,
        new_parity=parity_from_name(args.new_parity) if args.new_parity else None,
        new_stopbits=(stopbits_from_number(args.new_stopbits) if args.new_stopbits else None),
        timeout=args.timeout,
        confirm=True,
    )
    sys.stdout.write(_format_info(info, op="switch-protocol"))
    return 0


async def _run_set_baud_rate(args: argparse.Namespace) -> int:
    port, _current_protocol, settings = resolve_open_args(args)
    info = await set_baud_rate(
        port,
        wire_code=args.wire_code,
        baudrate=args.target_baudrate,
        serial_settings=settings,
        parity=parity_from_name(args.new_parity) if args.new_parity else None,
        stopbits=(stopbits_from_number(args.new_stopbits) if args.new_stopbits else None),
        timeout=args.timeout,
        confirm=True,
    )
    sys.stdout.write(_format_info(info, op="set-baud-rate"))
    return 0


async def _run_write_sbn_address(args: argparse.Namespace) -> int:
    port, _current_protocol, settings = resolve_open_args(args)
    readback = await write_sbn_address(
        port,
        args.sbn,
        serial_settings=settings,
        timeout=args.timeout,
        confirm=True,
    )
    sys.stdout.write(
        f"write-sbn-address complete:\n"
        f"  requested:  0x{args.sbn:02x}\n"
        f"  readback:   0x{readback:02x}\n"
        f"  verified:   {readback == args.sbn}\n",
    )
    return 0


def _format_info(info: DeviceInfo, *, op: str) -> str:
    lines = [
        f"{op} complete:",
        f"  model:        {info.model or '<unknown>'}",
        f"  family:       {info.family.value}",
        f"  protocol:     {info.protocol.value}",
        f"  baudrate:     {info.serial_settings.baudrate}",
        f"  parity:       {info.serial_settings.parity.value}",
        f"  stopbits:     {info.serial_settings.stopbits.value}",
    ]
    return "\n".join(lines) + "\n"
