"""xBPI frame codec.

Two concrete operations:

- :func:`build_command` — assemble a host→balance TX frame.
- :func:`parse_frame` — validate and decompose a balance→host RX frame
  into an :class:`XbpiFrame`.

TX frame::

    [len] [src_sbn] [dst_sbn] [opcode] [args...] [chk]

RX frame::

    [len] [marker=0x41] [subtype] [body...] [chk]

``len`` counts every byte that follows the length byte (including the
checksum). ``chk`` is ``sum(every preceding byte) & 0xFF``. Defaults:
``src_sbn=0x01`` (host convention) and ``dst_sbn=0x09`` (balance factory
default). See ``docs/protocol.md`` §3.
"""

from __future__ import annotations

from sartoriuslib.errors import (
    ErrorContext,
    SartoriusFrameError,
    SartoriusTransientTransportError,
)
from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "BALANCE_SBN_DEFAULT",
    "HOST_SBN_DEFAULT",
    "MIN_FRAME_SIZE",
    "RX_MARKER",
    "build_command",
    "checksum",
    "parse_frame",
]

#: Host SBN convention per ``docs/protocol.md`` §2.2.
HOST_SBN_DEFAULT: int = 0x01
#: Balance SBN factory default. The balance accepts this on a direct
#: point-to-point link regardless of its own configured SBN.
BALANCE_SBN_DEFAULT: int = 0x09
#: Balance→host marker byte. Always ``0x41``.
RX_MARKER: int = 0x41
#: Minimum possible frame size: length + marker + subtype + chk = 4 bytes.
MIN_FRAME_SIZE: int = 4
#: Max value of the single-byte length field.
_MAX_LENGTH: int = 0xFF
#: Max value of any single wire byte.
_MAX_BYTE: int = 0xFF


def checksum(data: bytes) -> int:
    """Return ``sum(data) & 0xFF`` — the xBPI frame checksum.

    Trivial by design. No CRC, no seed; see ``docs/protocol.md`` §12.
    """
    return sum(data) & 0xFF


def build_command(
    opcode: int,
    args: bytes = b"",
    *,
    src_sbn: int = HOST_SBN_DEFAULT,
    dst_sbn: int = BALANCE_SBN_DEFAULT,
) -> bytes:
    """Assemble a host→balance frame.

    Arguments:
        opcode: Command byte (``0x00`` – ``0xFF``).
        args: Pre-encoded argument bytes (usually one or more TLVs — see
            :mod:`sartoriuslib.protocol.xbpi.tlv`). Empty for no-arg
            commands.
        src_sbn: Source SBN; defaults to the host convention ``0x01``.
        dst_sbn: Destination SBN; defaults to the balance factory
            default ``0x09``.

    Returns:
        The complete frame bytes, length-prefixed and checksummed, ready
        to hand to :meth:`Transport.write`.
    """
    _require_byte(opcode, "opcode")
    _require_byte(src_sbn, "src_sbn")
    _require_byte(dst_sbn, "dst_sbn")
    payload = bytes([src_sbn, dst_sbn, opcode]) + bytes(args)
    # ``length`` counts every byte that will follow the length byte —
    # including the not-yet-appended checksum.
    length = len(payload) + 1
    if length > _MAX_LENGTH:
        raise SartoriusFrameError(
            f"frame too long: {length + 1} bytes (max 256)",
            context=ErrorContext(opcode=opcode, extra={"length": length}),
        )
    pre_chk = bytes([length]) + payload
    return pre_chk + bytes([checksum(pre_chk)])


def parse_frame(data: bytes) -> XbpiFrame:
    """Validate and decompose a balance→host frame.

    Raises:
        SartoriusFrameError: Frame too short, length byte inconsistent
            with buffer size, marker is not ``0x41``, or checksum
            mismatch.
    """
    raw = bytes(data)
    if len(raw) < MIN_FRAME_SIZE:
        # Underrun reclassifies as a transient: the cold-open USB
        # race surfaces here when the device drops the first byte or
        # two of its reply. Callers (and ``open_device``'s identify
        # retry loop) may retry without reopening. Non-underrun
        # framing corruption — bad marker, length mismatch, bad
        # checksum — stays under :class:`SartoriusFrameError` below.
        raise SartoriusTransientTransportError(
            f"frame too short: got {len(raw)} bytes (min {MIN_FRAME_SIZE})",
            context=ErrorContext(raw_response=raw),
        )
    length = raw[0]
    expected_total = length + 1
    if len(raw) != expected_total:
        raise SartoriusFrameError(
            f"frame length mismatch: length byte says {expected_total} bytes, got {len(raw)}",
            context=ErrorContext(
                raw_response=raw,
                extra={"declared_length": length, "buffer_size": len(raw)},
            ),
        )
    marker = raw[1]
    if marker != RX_MARKER:
        raise SartoriusFrameError(
            f"bad marker byte 0x{marker:02x} (expected 0x{RX_MARKER:02x})",
            context=ErrorContext(raw_response=raw, extra={"marker": marker}),
        )
    subtype = raw[2]
    body = raw[3:-1]
    chk = raw[-1]
    expected_chk = checksum(raw[:-1])
    if chk != expected_chk:
        raise SartoriusFrameError(
            f"bad checksum 0x{chk:02x} (expected 0x{expected_chk:02x})",
            context=ErrorContext(
                raw_response=raw,
                extra={"checksum": chk, "expected_checksum": expected_chk},
            ),
        )
    return XbpiFrame(
        length=length,
        marker=marker,
        subtype=subtype,
        body=bytes(body),
        checksum=chk,
        raw=raw,
    )


def _require_byte(value: int, name: str) -> None:
    if not (0 <= value <= _MAX_BYTE):
        raise SartoriusFrameError(
            f"{name} out of range (0..255): {value}",
            context=ErrorContext(extra={name: value}),
        )
