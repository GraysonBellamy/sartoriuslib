"""Diagnostics / reverse-engineering tools.

**Not on the default install path.** Destructive operations require
``--i-understand-this-is-destructive``. Never invoked from normal
discovery or open. See design doc §13.

Six subcommands under the ``sarto-diag`` namespace:

- ``snapshot`` — read-only battery of every safe-listed xBPI opcode;
  capability-discovery aid.
- ``jitter`` - read-only 50 Hz xBPI acquisition timing probe.
- ``tap`` — passive line / byte capture; never writes.
- ``stream`` — raw-byte capture for protocol work.
- ``sweep`` — xBPI opcode sweep across a range; **destructive** —
  some opcodes mutate persistent state.
- ``argfuzz`` — argument fuzzer for a single opcode; **destructive**.

Entry-point dispatcher::

    sarto-diag snapshot PORT [--out FILE]
    sarto-diag jitter [PORT] [--duration 10] [--out FILE]
    sarto-diag tap PORT [--duration 5]
    sarto-diag stream PORT [--duration 5]
    sarto-diag sweep PORT --i-understand-this-is-destructive
    sarto-diag argfuzz PORT --opcode 0xNN --i-understand-this-is-destructive
"""

from __future__ import annotations

import argparse

from sartoriuslib.cli.diagnostics._gate import (
    DESTRUCTIVE_FLAG,
    require_destructive_ack,
)

__all__ = ["DESTRUCTIVE_FLAG", "main", "require_destructive_ack"]


def main(argv: list[str] | None = None) -> int:
    """``sarto-diag`` dispatcher entry point.

    Parses the leading subcommand token (``snapshot``, ``tap``, …)
    and forwards the remaining argv to the corresponding module's
    ``main``. Unknown subcommands print the available list and exit
    with code 2.
    """
    parser = argparse.ArgumentParser(
        prog="sarto-diag",
        description=(
            "Diagnostics namespace — RE tools, not on the default install path. "
            "Destructive operations require --i-understand-this-is-destructive."
        ),
        add_help=True,
    )
    parser.add_argument(
        "subcommand",
        choices=("snapshot", "jitter", "tap", "stream", "sweep", "argfuzz"),
        help="Diagnostic subcommand to run.",
    )
    parser.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        help="Arguments passed to the subcommand.",
    )
    ns = parser.parse_args(argv)
    # Lazy imports keep the gate-only path (e.g. ``sarto-diag --help``)
    # cheap and avoid the partially-initialised circle on first import.
    if ns.subcommand == "snapshot":
        from sartoriuslib.cli.diagnostics import snapshot  # noqa: PLC0415

        return snapshot.main(ns.rest)
    if ns.subcommand == "jitter":
        from sartoriuslib.cli.diagnostics import jitter  # noqa: PLC0415

        return jitter.main(ns.rest)
    if ns.subcommand == "tap":
        from sartoriuslib.cli.diagnostics import tap  # noqa: PLC0415

        return tap.main(ns.rest)
    if ns.subcommand == "stream":
        from sartoriuslib.cli.diagnostics import stream  # noqa: PLC0415

        return stream.main(ns.rest)
    if ns.subcommand == "sweep":
        from sartoriuslib.cli.diagnostics import sweep  # noqa: PLC0415

        return sweep.main(ns.rest)
    if ns.subcommand == "argfuzz":
        from sartoriuslib.cli.diagnostics import argfuzz  # noqa: PLC0415

        return argfuzz.main(ns.rest)
    raise AssertionError(f"unreachable: argparse choices guard {ns.subcommand!r}")
