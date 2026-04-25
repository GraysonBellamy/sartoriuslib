"""SBI line codec and ESC-token helpers."""

from __future__ import annotations

import re

from sartoriuslib.errors import ErrorContext, SartoriusFrameError, SartoriusValidationError
from sartoriuslib.protocol.sbi.tables import ESC

__all__ = [
    "LINE_TERMINATOR",
    "build_command",
    "is_autoprint_line",
    "normalize_token",
    "split_lines",
    "strip_line_terminator",
]


LINE_TERMINATOR: bytes = b"\r\n"
# Optional mode prefix (e.g. ``N``, ``T``, ``G``, ``Qnt``, ``Stat``):
# 1-6 alphanumeric/space characters followed by whitespace. Periods are
# deliberately excluded from the prefix character class — the SBI
# identity reply ``SerNo.    0031801165`` would otherwise match this
# pattern and be misclassified as a weight line (verified on MSE1203S
# BAC 00-39-21, 2026-04-25). Status replies that contain periods
# (e.g. ``Cal.Int.``) are caught separately via ``_AUTOPRINT_SPECIALS``.
_AUTOPRINT_RE = re.compile(
    r"\A\s*"
    r"\??\s*"
    r"(?:[A-Za-z][A-Za-z0-9 ]{0,5}\s+)?"
    r"[+-]?\s*"
    r"(?:\d+(?:\.\d*)?|\.\d+)"
    r"\s*[A-Za-zµ/_\.]*"
    r"\s*\Z",
)
_BARE_NUMBER_RE = re.compile(r"\A\s*(?:\d+(?:\.\d*)?|\.\d+)\s*\Z")
_AUTOPRINT_SPECIALS = {
    "CAL. EXT.",
    "CAL. INT.",
    "CAL.INT.",
    "H",
    "HH",
    "HIGH",
    "HI",
    "L",
    "LL",
    "LOW",
}
_MIN_FORMATTED_WEIGHT_BODY_CHARS = 14


def normalize_token(command: bytes | str) -> bytes:
    """Normalize a user-facing SBI command into on-wire bytes.

    Accepts raw bytes, strings containing the literal escape character, or
    readable forms like ``"ESC P"`` and ``"ESC x1_"``. Trailing CR/LF is
    stripped because Sartorius documents command terminators as optional and
    the existing fake-transport fixtures use the bare token.
    """
    if isinstance(command, bytes):
        token = command
    else:
        text = command.strip()
        if text.upper().startswith("ESC"):
            text = "\x1b" + text[3:].lstrip()
        token = text.encode("ascii", errors="strict")
    token = token.rstrip(b"\r\n")
    if not token:
        raise SartoriusValidationError(
            "SBI command token cannot be empty",
            context=ErrorContext(protocol="sbi"),
        )
    if not token.startswith(ESC):
        raise SartoriusValidationError(
            "SBI command token must start with ESC",
            context=ErrorContext(
                protocol="sbi",
                sbi_token=token,
                extra={"token": token.hex()},
            ),
        )
    return token


def build_command(command: bytes | str, *, terminator: bytes = b"") -> bytes:
    """Build an SBI command token, optionally appending a terminator."""
    token = normalize_token(command)
    return token + terminator


def strip_line_terminator(line: bytes) -> bytes:
    """Remove one SBI line terminator if present."""
    if line.endswith(LINE_TERMINATOR):
        return line[: -len(LINE_TERMINATOR)]
    if line.endswith(b"\n"):
        return line[:-1].rstrip(b"\r")
    return line


def split_lines(raw: bytes) -> tuple[bytes, ...]:
    r"""Split a raw SBI payload into complete lines.

    Every non-empty line must include ``\r\n`` (or at least ``\n`` for
    lenient fixture parsing). Returns lines including their terminators.
    """
    if raw == b"":
        return ()
    out: list[bytes] = []
    start = 0
    while start < len(raw):
        idx = raw.find(b"\n", start)
        if idx < 0:
            raise SartoriusFrameError(
                "SBI payload ended before a line terminator",
                context=ErrorContext(raw_response=raw, protocol="sbi"),
            )
        out.append(raw[start : idx + 1])
        start = idx + 1
    return tuple(out)


def is_autoprint_line(line: bytes | str) -> bool:
    """Return ``True`` when ``line`` looks like an unsolicited weight line."""
    raw = line.encode("ascii", errors="ignore") if isinstance(line, str) else line
    body = strip_line_terminator(raw).decode("ascii", errors="replace")
    text = body.strip()
    if not text:
        return False
    if _BARE_NUMBER_RE.fullmatch(body) and (
        body == text or len(body) < _MIN_FORMATTED_WEIGHT_BODY_CHARS
    ):
        return False
    marker = " ".join(text.upper().split())
    if marker.startswith("STAT "):
        marker = marker.removeprefix("STAT ").strip()
    return _AUTOPRINT_RE.match(body) is not None or marker in _AUTOPRINT_SPECIALS
