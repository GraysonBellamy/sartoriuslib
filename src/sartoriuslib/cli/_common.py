"""Shared helpers for the ``sarto-*`` CLIs.

Every command that opens a balance accepts the same surface: a
positional ``port``, optional serial-framing overrides
(``--baudrate`` / ``--parity`` / ``--stopbits``), a ``--protocol``
selector, a ``--timeout``, and a ``--fixture`` test-injection flag.
This module factors that into ``add_open_args`` plus the resolution
helpers used by each command's ``main``.

The ``--fixture`` flag is the integration-test seam: pass a §8.2
text-fixture file and the CLI runs against a scripted
:class:`FakeTransport` instead of a real serial port. It works for
both xBPI fixtures (``> hex / < hex``) and SBI fixtures
(``> ESC P / < line``); the parser is auto-detected from the file's
first request marker.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from anyserial import Parity, StopBits

from sartoriuslib.errors import SartoriusError, SartoriusValidationError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.testing import (
    FakeTransport,
    parse_sbi_fixture,
    parse_xbpi_fixture,
)
from sartoriuslib.transport.base import SerialSettings

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable, Callable

    from sartoriuslib.transport.base import Transport

__all__ = [
    "PARITY_CHOICES",
    "STOPBITS_CHOICES",
    "add_open_args",
    "load_fixture_transport",
    "parity_from_name",
    "resolve_open_args",
    "run_cli",
    "stopbits_from_number",
]


PARITY_CHOICES: tuple[str, ...] = ("odd", "even", "none")
STOPBITS_CHOICES: tuple[int, ...] = (1, 2)


def parity_from_name(name: str) -> Parity:
    """Resolve the CLI ``--parity`` choice string to an :class:`anyserial.Parity`."""
    return _PARITY_BY_NAME[name]


def stopbits_from_number(number: int) -> StopBits:
    """Resolve the CLI ``--stopbits`` integer to an :class:`anyserial.StopBits`."""
    return _STOPBITS_BY_NUMBER[number]


_PARITY_BY_NAME: dict[str, Parity] = {
    "odd": Parity.ODD,
    "even": Parity.EVEN,
    "none": Parity.NONE,
}

_STOPBITS_BY_NUMBER: dict[int, StopBits] = {
    1: StopBits.ONE,
    2: StopBits.TWO,
}


def add_open_args(
    parser: argparse.ArgumentParser,
    *,
    port_required: bool = True,
    protocol_default: str = "auto",
) -> None:
    """Register the shared ``open_device`` arguments on ``parser``.

    ``port_required=False`` makes ``port`` optional — used by
    :mod:`sartoriuslib.cli.discover` where the no-port form is reserved
    for future port-listing.
    """
    parser.add_argument(
        "port",
        nargs=None if port_required else "?",
        help='Serial-port path ("/dev/ttyUSB0", "COM3", ...). '
        "Ignored when --fixture is supplied; pass any placeholder.",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=None,
        help="Override the default 9600 baud (e.g. 1200 for WZA in SBI mode).",
    )
    parser.add_argument(
        "--parity",
        choices=sorted(_PARITY_BY_NAME),
        default=None,
        help="Override the default odd parity.",
    )
    parser.add_argument(
        "--stopbits",
        type=int,
        choices=sorted(_STOPBITS_BY_NUMBER),
        default=None,
        help="Override the default 1 stop bit.",
    )
    protocol_default_help = "auto-detect" if protocol_default == "auto" else protocol_default
    parser.add_argument(
        "--protocol",
        choices=("auto", "xbpi", "sbi"),
        default=protocol_default,
        help=f"Wire protocol to speak (default: {protocol_default_help}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Per-call I/O timeout in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--fixture",
        type=str,
        default=None,
        help=(
            "Test seam: path to a §8.2 text-fixture file. "
            "When supplied, CLI runs against a scripted FakeTransport "
            "instead of a real serial port."
        ),
    )


def resolve_open_args(
    args: argparse.Namespace,
) -> tuple[str | Transport, ProtocolKind, SerialSettings | None]:
    """Return ``(port_or_transport, protocol, serial_settings)``.

    When ``--fixture`` is supplied, ``port_or_transport`` is the
    scripted :class:`FakeTransport` and ``serial_settings`` is ``None``
    (the placeholder serial settings inside :func:`open_device` are
    used). Otherwise the positional ``port`` is returned along with
    framing overrides (``baudrate`` / ``parity`` / ``stopbits``).
    """
    if args.fixture is not None:
        transport = load_fixture_transport(args.fixture)
        return transport, ProtocolKind(args.protocol), None
    if args.port is None:
        raise SartoriusValidationError(
            "port is required when --fixture is not supplied",
        )
    settings = _build_serial_settings(
        args.port,
        baudrate=args.baudrate,
        parity_name=args.parity,
        stopbits_number=args.stopbits,
    )
    return args.port, ProtocolKind(args.protocol), settings


def load_fixture_transport(path: str) -> FakeTransport:
    """Build a scripted :class:`FakeTransport` from a §8.2 fixture file.

    The fixture parser to use (xBPI vs SBI) is detected from the first
    request line: bytes starting with ``ESC`` (or hex starting with
    ``1b``) parse as SBI; everything else as xBPI. The detection is
    best-effort — both parsers raise :class:`SartoriusValidationError`
    on malformed input, which the CLI surfaces verbatim.
    """
    text = Path(path).read_text(encoding="utf-8")
    is_sbi = _looks_like_sbi_fixture(text)
    parse = parse_sbi_fixture if is_sbi else parse_xbpi_fixture
    script = parse(text)
    return FakeTransport(script, label=f"fixture://{path}")


def _looks_like_sbi_fixture(text: str) -> bool:
    r"""Detect an SBI fixture by inspecting the first ``>`` line.

    SBI fixtures' first request line starts with the readable token
    ``ESC`` (the human form of ``\x1b``) or with ``1b`` hex; xBPI
    requests start with a length byte that is never ``ESC`` and never
    fits into the SBI shape, so the heuristic is reliable.
    """
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.startswith(">"):
            continue
        payload = line[1:].strip()
        if not payload:
            continue
        if payload.upper().startswith("ESC"):
            return True
        compact = payload.replace(" ", "").lower()
        return compact.startswith("1b")
    return False


def _build_serial_settings(
    port: str,
    *,
    baudrate: int | None,
    parity_name: str | None,
    stopbits_number: int | None,
) -> SerialSettings | None:
    """Produce a :class:`SerialSettings` only when at least one override is set.

    Falling through to ``None`` lets :func:`open_device` apply its own
    8-O-1 @ 9600 default — keeping the helper minimal so the CLI and
    library stay in sync on framing defaults.
    """
    if baudrate is None and parity_name is None and stopbits_number is None:
        return None
    return SerialSettings(
        port=port,
        baudrate=baudrate if baudrate is not None else 9600,
        parity=_PARITY_BY_NAME[parity_name] if parity_name is not None else Parity.ODD,
        stopbits=(
            _STOPBITS_BY_NUMBER[stopbits_number] if stopbits_number is not None else StopBits.ONE
        ),
    )


def run_cli(coro_factory: Callable[[], Awaitable[int]]) -> int:
    """Run an async CLI body, mapping :class:`SartoriusError` to a clean exit.

    On success the coroutine's return value is the exit code. On
    :class:`SartoriusError` the message is written to stderr and the
    exit code is ``1`` — keeps the user-facing failure mode quiet
    instead of dumping a traceback for an expected condition (no
    response, framing error, port not found, etc.).
    """
    try:
        return anyio.run(coro_factory)
    except SartoriusError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
