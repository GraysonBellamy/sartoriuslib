"""Destructive-operation gate shared by ``sarto-diag`` subcommands.

Lives in its own module to break the import cycle that would otherwise
form between :mod:`sartoriuslib.cli.diagnostics` (the dispatcher) and
each subcommand module that needs to call :func:`require_destructive_ack`.
"""

from __future__ import annotations

import sys

__all__ = ["DESTRUCTIVE_FLAG", "require_destructive_ack"]


DESTRUCTIVE_FLAG: str = "--i-understand-this-is-destructive"


def require_destructive_ack(*, acked: bool, op: str) -> None:
    """Refuse a destructive sub-op when the user hasn't acked the gate.

    Writes a clear stderr message and raises :class:`SystemExit(2)` —
    keeps the failure mode loud and parseable in CI.
    """
    if acked:
        return
    sys.stderr.write(
        f"error: sarto-diag {op} can change persistent device state.\n"
        f"       Pass {DESTRUCTIVE_FLAG} to acknowledge and execute.\n",
    )
    raise SystemExit(2)
