"""SBI line parser — command replies and autoprint output."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from sartoriuslib.devices.models import Reading
from sartoriuslib.errors import ErrorContext, SartoriusParseError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi.framing import split_lines, strip_line_terminator
from sartoriuslib.protocol.sbi.tables import unit_from_sbi
from sartoriuslib.protocol.sbi.types import SbiLine, SbiLineKind, SbiReply
from sartoriuslib.registry.units import Sign, Unit

__all__ = [
    "parse_line",
    "parse_reply",
    "parse_weight_line",
    "require_identity_text",
    "require_reading",
]


# Prefix character class deliberately excludes ``.`` to avoid matching
# SBI identity replies such as ``SerNo.    0031801165`` as weight lines.
# This mirrors the ``_AUTOPRINT_RE`` rule in :mod:`framing`; keeping the
# two character classes in lockstep is the regression test for the
# 2026-04-25 MSE1203S finding (parser misclassified SerNo. as a Reading
# of value 31801165.0). Status replies that legitimately contain periods
# (``Cal.Int.``) are matched separately via ``_SPECIAL_NON_WEIGHT_MARKERS``.
_WEIGHT_RE = re.compile(
    r"\A\s*"
    r"(?P<unstable>\?)?\s*"
    r"(?:(?P<id>[A-Za-z][A-Za-z0-9 ]{0,5})\s+)?"
    r"(?P<sign>[+-])?\s*"
    r"(?P<number>(?:\d+(?:\.\d*)?|\.\d+))"
    r"\s*(?P<unit>[A-Za-zµ/_\.]+)?"
    r"\s*\Z",
)

_OVERLOAD_MARKERS: frozenset[str] = frozenset(
    {"H", "HH", "HIGH", "HI", "OVER", "OVERLOAD", "OL"},
)
_UNDERLOAD_MARKERS: frozenset[str] = frozenset(
    {"L", "LL", "LOW", "UNDER", "UNDERLOAD", "UL"},
)
_REFUSAL_MARKERS: frozenset[str] = frozenset(
    {
        "APP. ERR",
        "DIS. ERR",
        "ERR",
        "ERROR",
        "INVALID",
        "NO",
        "PRT. ERR",
        "REJECT",
        "REJECTED",
    },
)
_SPECIAL_NON_WEIGHT_MARKERS: frozenset[str] = frozenset(
    {
        "CAL. EXT.",
        "CAL. INT.",
        "CAL.INT.",
    }
)
_MIN_FORMATTED_WEIGHT_BODY_CHARS = 14


def parse_reply(raw: bytes) -> SbiReply:
    """Parse one raw SBI payload into an :class:`SbiReply`."""
    lines = tuple(parse_line(line) for line in split_lines(raw))
    return SbiReply(lines=lines, raw=raw)


def parse_line(raw: bytes) -> SbiLine:
    """Parse one SBI line.

    Weight/autoprint lines get a decoded :class:`Reading`; other printable
    lines are preserved as identity text. Truly undecodable bytes raise
    :class:`SartoriusParseError`.
    """
    body = strip_line_terminator(raw)
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SartoriusParseError(
            "SBI line is not ASCII",
            context=ErrorContext(raw_response=raw, protocol="sbi"),
        ) from exc
    stripped = text.strip()
    if not stripped:
        return SbiLine(raw=raw, text="", kind=SbiLineKind.EMPTY)
    marker = _status_marker(stripped)
    if _is_refusal_marker(marker):
        return SbiLine(raw=raw, text=stripped, kind=SbiLineKind.REFUSAL)
    if marker in _SPECIAL_NON_WEIGHT_MARKERS:
        return SbiLine(raw=raw, text=stripped, kind=SbiLineKind.UNKNOWN)
    if _looks_like_weight(text):
        return SbiLine(
            raw=raw,
            text=stripped,
            kind=SbiLineKind.WEIGHT,
            reading=parse_weight_line(raw),
        )
    return SbiLine(raw=raw, text=stripped, kind=SbiLineKind.IDENTITY)


def parse_weight_line(raw: bytes | str) -> Reading:
    """Decode an SBI weight/autoprint line into a protocol-neutral reading."""
    line_bytes = raw.encode("ascii") if isinstance(raw, str) else raw
    body = strip_line_terminator(line_bytes)
    text = body.decode("ascii", errors="replace")
    stripped = text.strip()
    marker = _status_marker(stripped)
    if marker in _OVERLOAD_MARKERS or marker in _UNDERLOAD_MARKERS:
        overload = marker in _OVERLOAD_MARKERS
        underload = marker in _UNDERLOAD_MARKERS
        return Reading(
            value=None,
            unit=Unit.UNKNOWN,
            sign=Sign.UNKNOWN,
            stable=False,
            overload=overload,
            underload=underload,
            decimals=None,
            sequence=None,
            status_flags={
                "stable": False,
                "overload": overload,
                "underload": underload,
            },
            protocol=ProtocolKind.SBI,
            received_at=datetime.now(UTC),
            monotonic_ns=time.monotonic_ns(),
            raw=line_bytes,
        )

    match = _WEIGHT_RE.match(text)
    if match is None:
        raise SartoriusParseError(
            f"unparseable SBI weight line {stripped!r}",
            context=ErrorContext(raw_response=line_bytes, protocol="sbi"),
        )

    number_text = match.group("number")
    sign_char = match.group("sign") or "+"
    unit_text = match.group("unit") or ""
    value = float(number_text)
    if sign_char == "-":
        value = -value
    decimals = _decimal_places(number_text)
    sign = _sign_from_wire(sign_char, value)
    stable = match.group("unstable") != "?" and bool(unit_text.strip())

    return Reading(
        value=value,
        unit=unit_from_sbi(unit_text),
        sign=sign,
        stable=stable,
        overload=False,
        underload=False,
        decimals=decimals,
        sequence=None,
        status_flags={"stable": stable},
        protocol=ProtocolKind.SBI,
        received_at=datetime.now(UTC),
        monotonic_ns=time.monotonic_ns(),
        raw=line_bytes,
    )


def require_reading(reply: SbiReply) -> Reading:
    """Return the first reading in ``reply`` or raise a parse error."""
    for line in reply.lines:
        if line.reading is not None:
            return line.reading
    raise SartoriusParseError(
        "SBI reply did not contain a weight line",
        context=ErrorContext(raw_response=reply.raw, protocol="sbi"),
    )


def require_identity_text(
    reply: SbiReply,
    *,
    allow_weight_like: bool = True,
) -> str:
    """Return the first text-bearing line suitable for identity commands.

    SBI identity fields may be numeric-only (serial numbers, software
    versions), so a line that also looks like a numeric display value is still
    valid text in this command context by default. Callers that expect a
    non-numeric identity field, such as the model string, can set
    ``allow_weight_like=False`` to avoid mistaking an autoprint reading for a
    command reply. Refusal and special status lines are excluded.
    """
    for line in reply.lines:
        if line.kind not in (
            SbiLineKind.EMPTY,
            SbiLineKind.REFUSAL,
            SbiLineKind.UNKNOWN,
        ) and (allow_weight_like or line.kind is not SbiLineKind.WEIGHT):
            return line.text
    raise SartoriusParseError(
        "SBI reply did not contain identity text",
        context=ErrorContext(raw_response=reply.raw, protocol="sbi"),
    )


def _looks_like_weight(text: str) -> bool:
    marker = _status_marker(text.strip())
    match = _WEIGHT_RE.match(text)
    return (
        marker in _OVERLOAD_MARKERS
        or marker in _UNDERLOAD_MARKERS
        or (match is not None and not _is_bare_number_fragment(text, match))
    )


def _is_bare_number_fragment(text: str, match: re.Match[str]) -> bool:
    """Reject mid-stream fragments that contain only a number.

    SBI weight output is fixed-width or annotated with a sign, ID/status
    prefix, or unit field. When we attach to autoprint mid-line, the first
    fragment can be just the numeric tail; treating that as a full reading is
    worse than skipping it and waiting for the next formatted line.
    """
    return (
        match.group("unstable") is None
        and match.group("id") is None
        and match.group("sign") is None
        and match.group("unit") is None
        and (text == text.strip() or len(text) < _MIN_FORMATTED_WEIGHT_BODY_CHARS)
    )


def _status_marker(text: str) -> str:
    """Normalize 22-character status prefixes such as ``Stat High``."""
    marker = " ".join(text.upper().split())
    if marker.startswith("STAT "):
        return marker.removeprefix("STAT ").strip()
    return marker


def _is_refusal_marker(marker: str) -> bool:
    return marker in _REFUSAL_MARKERS or marker.startswith("ERR ")


def _decimal_places(number_text: str) -> int:
    _, dot, rest = number_text.partition(".")
    return len(rest) if dot else 0


def _sign_from_wire(sign_char: str, value: float) -> Sign:
    if value == 0:
        return Sign.ZERO
    if sign_char == "-":
        return Sign.NEGATIVE
    if sign_char == "+":
        return Sign.POSITIVE
    return Sign.UNKNOWN
