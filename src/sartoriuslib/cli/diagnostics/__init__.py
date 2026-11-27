"""Diagnostics / reverse-engineering tools.

**Not on the default install path.** Destructive operations require
``--i-understand-this-is-destructive``. Never invoked from normal
discovery or open. See design doc §13.

Seven subcommands under the ``sarto-diag`` namespace:

- ``snapshot`` — read-only battery of every safe-listed xBPI opcode;
  capability-discovery aid.
- ``jitter`` - read-only 50 Hz xBPI acquisition timing probe.
- ``tap`` — passive line / byte capture; never writes.
- ``stream`` — raw-byte capture for protocol work.
- ``sweep`` — xBPI opcode sweep across a range; **destructive** —
  some opcodes mutate persistent state.
- ``argfuzz`` — argument fuzzer for a single opcode; **destructive**.
- ``adjust-trace`` — runs an internal adjustment and records every
  exchange while it runs; **destructive** (``--dry-run`` is read-only).

Entry-point dispatcher::

    sarto-diag snapshot PORT [--out FILE]
    sarto-diag jitter [PORT] [--duration 10] [--out FILE]
    sarto-diag tap PORT [--duration 5]
    sarto-diag stream PORT [--duration 5]
    sarto-diag sweep PORT --i-understand-this-is-destructive
    sarto-diag argfuzz PORT --opcode 0xNN --i-understand-this-is-destructive
    sarto-diag adjust-trace PORT --out FILE --i-understand-this-is-destructive
"""

from __future__ import annotations

import argparse
import importlib
from typing import TYPE_CHECKING, cast

from sartoriuslib.cli.diagnostics._gate import (
    DESTRUCTIVE_FLAG,
    require_destructive_ack,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
        choices=tuple(_SUBCOMMAND_MODULES),
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
    module = importlib.import_module(
        f"sartoriuslib.cli.diagnostics.{_SUBCOMMAND_MODULES[ns.subcommand]}"
    )
    sub_main = cast("Callable[[list[str]], int]", module.main)
    return sub_main(ns.rest)


#: ``sarto-diag`` subcommand → module under :mod:`sartoriuslib.cli.diagnostics`.
_SUBCOMMAND_MODULES: dict[str, str] = {
    "snapshot": "snapshot",
    "jitter": "jitter",
    "tap": "tap",
    "stream": "stream",
    "sweep": "sweep",
    "argfuzz": "argfuzz",
    "adjust-trace": "adjust_trace",
}
