"""``sarto-decode`` — decode xBPI hex or an SBI line offline (no hardware).

The intended use cases are debugging captures, RE work on unfamiliar
firmware, and post-mortem analysis of error logs. The CLI takes wire
bytes from the command line and writes a structured human-readable
report to stdout. No serial port is opened.

Examples::

    # The docs/protocol.md §3.3 worked example (typo'd checksum).
    sarto-decode --xbpi 0b 41 48 bb a3 d7 0a 3d 30 82 45 55

    # The same with the corrected checksum — the body decodes cleanly.
    sarto-decode --xbpi 0b 41 48 bb a3 d7 0a 3d 30 82 45 07

    # An SBI weight line.
    sarto-decode --sbi '+     0.00 g'
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Final

from sartoriuslib.errors import SartoriusError
from sartoriuslib.protocol.sbi.framing import LINE_TERMINATOR
from sartoriuslib.protocol.sbi.parser import parse_line
from sartoriuslib.protocol.sbi.types import SbiLine, SbiLineKind
from sartoriuslib.protocol.xbpi.framing import RX_MARKER, checksum, parse_frame
from sartoriuslib.protocol.xbpi.parser import (
    decode_error_body,
    decode_long_measurement_body,
    decode_measurement_body,
    decode_status_block_body,
    decode_typed_float_body,
    is_status_block_body,
)
from sartoriuslib.protocol.xbpi.tables import (
    ERROR_CODE_REASONS,
    subtype_family,
)
from sartoriuslib.protocol.xbpi.types import SubtypeFamily, XbpiFrame

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["decode_sbi_line", "decode_xbpi_bytes", "main"]


_MIN_FRAME_BYTES: Final[int] = 4
_SHORT_MEASUREMENT_LEN: Final[int] = 8
_LONG_MEASUREMENT_LEN: Final[int] = 17


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code.

    ``0`` on a successful decode (even when the frame carries an error
    subtype or a malformed checksum — the report is still produced),
    ``2`` on argument errors, ``1`` on a hard decode failure where no
    structural information could be recovered.
    """
    parser = _build_argparser()
    args = parser.parse_args(argv)
    if args.xbpi is not None:
        try:
            data = _hex_tokens_to_bytes(args.xbpi)
        except ValueError as exc:
            parser.error(str(exc))
        report = decode_xbpi_bytes(data)
        sys.stdout.write(report)
        return 0
    if args.sbi is not None:
        report = decode_sbi_line(args.sbi)
        sys.stdout.write(report)
        return 0
    # argparse's mutually-exclusive `required=True` already forces one
    # of the two; this branch is defensive only.
    parser.error("one of --xbpi or --sbi is required")


def decode_xbpi_bytes(data: bytes) -> str:
    """Return a human-readable report describing ``data`` as an xBPI frame.

    The report is multi-line text with a trailing newline. Framing
    errors (bad length, marker, or checksum) are surfaced with a
    "frame error:" line; the function still tries to display whatever
    structural information was already parsed (length, marker, subtype)
    so the caller can spot the problem visually.
    """
    lines: list[str] = []
    lines.append(f"xBPI frame: {data.hex(' ')}")
    lines.append(f"  byte count: {len(data)}")
    if len(data) < _MIN_FRAME_BYTES:
        lines.append(
            f"  frame error: too short for an xBPI reply (need >= {_MIN_FRAME_BYTES} bytes)",
        )
        return _join(lines)

    length = data[0]
    marker = data[1]
    subtype = data[2]
    family = subtype_family(subtype)
    marker_note = " (RX)" if marker == RX_MARKER else " (UNEXPECTED)"

    lines.append(f"  length:     0x{length:02x} ({length} bytes follow)")
    lines.append(f"  marker:     0x{marker:02x}{marker_note}")
    lines.append(f"  subtype:    0x{subtype:02x} ({_describe_family(family)})")

    expected_total = length + 1
    if len(data) != expected_total:
        lines.append(
            f"  frame error: length byte says {expected_total} bytes total, got {len(data)}",
        )
        # Even with a length mismatch we can still show what looks like
        # the body. parse_frame will refuse, so we stop there.
        return _join(lines)

    body = data[3:-1]
    actual_chk = data[-1]
    expected_chk = checksum(data[:-1])
    lines.append(f"  body:       {body.hex(' ') if body else '(empty)'} ({len(body)} bytes)")
    lines.append(
        f"  checksum:   0x{actual_chk:02x} "
        f"({'valid' if actual_chk == expected_chk else f'INVALID, expected 0x{expected_chk:02x}'})",
    )

    # Try the strict parse for the typed-body decoders below. On
    # checksum mismatch parse_frame raises; we still attempt body
    # decoding from the raw bytes since the body itself may be sound.
    try:
        frame = parse_frame(data)
    except SartoriusError as exc:
        lines.append(f"  parse note: {exc}")
        # Synthesize a frame from the raw bytes so body decoders can
        # still run. The caller has already seen the framing error
        # message, so this lenient mode is opt-in via the report
        # being a best-effort tool.
        frame = XbpiFrame(
            length=length,
            marker=marker,
            subtype=subtype,
            body=body,
            checksum=actual_chk,
            raw=data,
        )
    _append_body_decode(lines, frame)
    return _join(lines)


def decode_sbi_line(text: str) -> str:
    """Return a human-readable report describing ``text`` as an SBI line.

    Trailing CR/LF is added if missing so :func:`parse_line` sees a
    well-formed record. The line kind, the parsed identity text, and
    (for weight lines) the decoded value/unit/stability flags are
    printed in fixed columns for easy diffing.
    """
    raw = text.encode("ascii", errors="replace")
    if not raw.endswith(LINE_TERMINATOR) and not raw.endswith(b"\n"):
        raw = raw + LINE_TERMINATOR
    line = parse_line(raw)
    return _format_sbi_line(line)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sarto-decode",
        description=(
            "Decode an xBPI frame from hex bytes, or an SBI reply line, "
            "without opening a serial port."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--xbpi",
        nargs="+",
        metavar="HEX",
        help=(
            "xBPI frame as space-separated hex bytes "
            "(e.g. '0b 41 48 bb a3 d7 0a 3d 30 82 45 07'). "
            "Tokens may be concatenated; whitespace and ':' separators are "
            "tolerated."
        ),
    )
    group.add_argument(
        "--sbi",
        metavar="LINE",
        help="SBI reply line (CR/LF added automatically if missing).",
    )
    return parser


def _hex_tokens_to_bytes(tokens: Sequence[str]) -> bytes:
    """Concatenate hex tokens into bytes, tolerating whitespace and ``:``."""
    cleaned = "".join(tokens).replace(" ", "").replace(":", "").replace(",", "")
    if cleaned == "":
        raise ValueError("--xbpi: empty hex input")
    if len(cleaned) % 2 != 0:
        raise ValueError(
            f"--xbpi: hex length must be even, got {len(cleaned)} chars: {cleaned!r}",
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"--xbpi: invalid hex digits: {exc}") from exc


def _describe_family(family: SubtypeFamily) -> str:
    return f"family={family.name.lower()}"


def _append_body_decode(lines: list[str], frame: XbpiFrame) -> None:
    """Append per-family decoded fields when we recognise the subtype."""
    family = subtype_family(frame.subtype)
    if family is SubtypeFamily.ERROR:
        _append_error_decode(lines, frame.body)
    elif family is SubtypeFamily.MEASUREMENT:
        _append_measurement_decode(lines, frame.body)
    elif family is SubtypeFamily.TYPED_FLOAT:
        _append_typed_float_decode(lines, frame.body)
    elif family is SubtypeFamily.SHORT_DATA:
        _append_short_data_decode(lines, frame)
    elif family is SubtypeFamily.LONG_DATA:
        _append_long_data_decode(lines, frame.body)
    elif family is SubtypeFamily.ACK:
        lines.append("  decoded: ACK")
    # SHORT_BLOB / BARGRAPH / STRUCTURED_U32 / UNKNOWN: leave the raw
    # body — the user can read it and we don't claim more than we know.


def _append_error_decode(lines: list[str], body: bytes) -> None:
    if len(body) != 1:
        lines.append(f"  decoded error: malformed (body must be 1 byte, got {len(body)})")
        return
    err = decode_error_body(body)
    reason = ERROR_CODE_REASONS.get(err.code, "unknown")
    lines.append("  decoded error:")
    lines.append(f"    code:   0x{err.code:02x}")
    lines.append(f"    reason: {reason}")


def _append_measurement_decode(lines: list[str], body: bytes) -> None:
    if is_status_block_body(body):
        try:
            sb = decode_status_block_body(body)
        except SartoriusError as exc:
            lines.append(f"  decoded status: malformed ({exc})")
            return
        lines.append("  decoded status block:")
        lines.append(f"    state:        0x{sb.state:02x}")
        lines.append(f"    status:       0x{sb.status:02x}")
        lines.append(f"    sequence:     {sb.sequence}")
        lines.append(f"    stable:       {sb.stable}")
        lines.append(f"    overload:     {sb.overload}")
        lines.append(f"    underload:    {sb.underload}")
        if sb.adc_trusted is not None:
            lines.append(f"    adc_trusted:  {sb.adc_trusted}")
        if sb.isocal_due is not None:
            lines.append(f"    isocal_due:   {sb.isocal_due}")
        return
    if len(body) == _SHORT_MEASUREMENT_LEN:
        try:
            m = decode_measurement_body(body)
        except SartoriusError as exc:
            lines.append(f"  decoded measurement: malformed ({exc})")
            return
        m_value = "<off-scale>" if m.value is None else f"{m.value:g}"
        lines.append("  decoded measurement:")
        lines.append(f"    value:    {m_value}")
        lines.append(f"    unit:     {m.unit.value}")
        lines.append(f"    sign:     {m.sign.value}")
        lines.append(f"    decimals: {m.decimals}")
        lines.append(f"    stable:   {m.stable}")
        lines.append(f"    off-scale: {m.off_scale}")
        return
    if len(body) == _LONG_MEASUREMENT_LEN:
        try:
            lm = decode_long_measurement_body(body)
        except SartoriusError as exc:
            lines.append(f"  decoded long-measurement: malformed ({exc})")
            return
        lm_value = "<off-scale>" if lm.measurement.value is None else f"{lm.measurement.value:g}"
        lines.append("  decoded long measurement:")
        lines.append(f"    value:    {lm_value}")
        lines.append(f"    unit:     {lm.measurement.unit.value}")
        lines.append(f"    stable:   {lm.measurement.stable}")
        lines.append(f"    state:    0x{lm.status.state:02x}")
        lines.append(f"    sequence: {lm.status.sequence}")
        return
    lines.append(
        f"  decoded measurement: unknown body length {len(body)} for subtype 0x48",
    )


def _append_typed_float_decode(lines: list[str], body: bytes) -> None:
    try:
        tf = decode_typed_float_body(body)
    except SartoriusError as exc:
        lines.append(f"  decoded typed-float: malformed ({exc})")
        return
    lines.append("  decoded typed-float:")
    lines.append(f"    value: {tf.value:g}")
    lines.append(f"    aux:   0x{tf.aux:02x}")


def _append_short_data_decode(lines: list[str], frame: XbpiFrame) -> None:
    body = frame.body
    if not body:
        lines.append("  decoded short_data: (empty body)")
        return
    lines.append("  decoded short_data:")
    if len(body) == 1:
        lines.append(f"    u8:     0x{body[0]:02x} ({body[0]})")
        return
    lines.append(f"    bytes:  {body.hex(' ')}")


def _append_long_data_decode(lines: list[str], body: bytes) -> None:
    if not body:
        lines.append("  decoded long_data: (empty body)")
        return
    text = body.rstrip(b"\x00").decode("ascii", errors="replace")
    lines.append("  decoded long_data:")
    lines.append(f"    ascii:  {text!r}")
    lines.append(f"    bytes:  {body.hex(' ')}")


def _format_sbi_line(line: SbiLine) -> str:
    out: list[str] = []
    out.append(f"SBI line: {line.text!r}")
    out.append(f"  bytes:  {line.raw.hex(' ')}")
    out.append(f"  kind:   {line.kind.value}")
    if line.kind is SbiLineKind.WEIGHT and line.reading is not None:
        r = line.reading
        value_str = f"{r.value:g}" if r.value is not None else "<none>"
        out.append("  decoded weight:")
        out.append(f"    value:     {value_str}")
        out.append(f"    unit:      {r.unit.value}")
        out.append(f"    sign:      {r.sign.value}")
        out.append(f"    stable:    {r.stable}")
        out.append(f"    overload:  {r.overload}")
        out.append(f"    underload: {r.underload}")
        out.append(f"    decimals:  {r.decimals}")
    return _join(out)


def _join(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"
