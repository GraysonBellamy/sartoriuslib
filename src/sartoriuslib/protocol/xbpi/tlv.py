"""xBPI TLV (tag-length-value) helpers.

Per ``docs/protocol.md`` §5, Cubis MSE requires request args wrapped as
TLV records rather than plain byte arguments. The tag's low nibble encodes
the value size:

+------+-------+---------------------------------------------+
| Tag  | Size  | Meaning                                     |
+======+=======+=============================================+
| 0x11 | 1 B   | u8 (rare in requests)                       |
| 0x12 | 2 B   | u16 BE                                      |
| 0x14 | 4 B   | u32 BE                                      |
| 0x21 | 1 B   | u8 — the most common request-arg wrapper    |
| 0x22 | 2 B   | u16 BE (seen in response bodies)            |
| 0x24 | 4 B   | u32 BE (seen in response bodies)            |
+------+-------+---------------------------------------------+

Response bodies may contain multiple concatenated TLVs (see §5.2), so this
module also exposes :func:`parse_tlv_sequence` for walking them.
"""

from __future__ import annotations

from sartoriuslib.errors import ErrorContext, SartoriusFrameError

__all__ = [
    "TLV_TAG_SIZES",
    "decode_tlv",
    "encode_tlv",
    "parse_tlv_sequence",
    "tlv_value_as_int",
]


#: Known TLV tag bytes → value size in bytes.
TLV_TAG_SIZES: dict[int, int] = {
    0x11: 1,
    0x12: 2,
    0x14: 4,
    0x21: 1,
    0x22: 2,
    0x24: 4,
}


def encode_tlv(tag: int, value: int | bytes) -> bytes:
    """Encode a single TLV record.

    ``value`` may be an ``int`` (encoded big-endian into the tag's size)
    or raw ``bytes`` (emitted verbatim, length-checked against the tag).
    """
    if tag not in TLV_TAG_SIZES:
        raise SartoriusFrameError(
            f"unknown TLV tag 0x{tag:02x}",
            context=ErrorContext(extra={"tag": tag}),
        )
    size = TLV_TAG_SIZES[tag]
    if isinstance(value, int):
        if value < 0:
            raise SartoriusFrameError(
                f"TLV value must be non-negative (got {value})",
                context=ErrorContext(extra={"tag": tag, "value": value}),
            )
        try:
            payload = value.to_bytes(size, "big")
        except OverflowError as exc:
            raise SartoriusFrameError(
                f"TLV value {value} does not fit in {size} byte(s) for tag 0x{tag:02x}",
                context=ErrorContext(extra={"tag": tag, "value": value}),
            ) from exc
        return bytes([tag]) + payload
    if len(value) != size:
        raise SartoriusFrameError(
            f"TLV value for tag 0x{tag:02x} must be {size} byte(s), got {len(value)}",
            context=ErrorContext(extra={"tag": tag, "value_len": len(value)}),
        )
    return bytes([tag]) + bytes(value)


def decode_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    """Decode one TLV record starting at ``offset``.

    Returns ``(tag, value_bytes, next_offset)``. Raises
    :class:`SartoriusFrameError` on unknown tags or truncated values.
    """
    if offset >= len(data):
        raise SartoriusFrameError(
            "TLV sequence truncated at tag position",
            context=ErrorContext(extra={"offset": offset, "total": len(data)}),
        )
    tag = data[offset]
    if tag not in TLV_TAG_SIZES:
        raise SartoriusFrameError(
            f"unknown TLV tag 0x{tag:02x} at offset {offset}",
            context=ErrorContext(extra={"tag": tag, "offset": offset}),
        )
    size = TLV_TAG_SIZES[tag]
    value_start = offset + 1
    value_end = value_start + size
    if value_end > len(data):
        raise SartoriusFrameError(
            f"TLV value for tag 0x{tag:02x} truncated "
            f"(needed {size} bytes, have {len(data) - value_start})",
            context=ErrorContext(extra={"tag": tag, "offset": offset}),
        )
    return tag, bytes(data[value_start:value_end]), value_end


def parse_tlv_sequence(body: bytes) -> list[tuple[int, bytes]]:
    """Walk ``body`` as a concatenation of TLV records.

    Returns a list of ``(tag, value_bytes)`` tuples. Raises
    :class:`SartoriusFrameError` if any record is truncated or any tag is
    unknown.

    Note: parameter-table replies (opcode ``0x55``) have the subtype byte
    double as the first TLV tag per §5.3 — the caller must prepend the
    subtype byte before passing the body here.
    """
    out: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(body):
        tag, value, offset = decode_tlv(body, offset)
        out.append((tag, value))
    return out


def tlv_value_as_int(value: bytes) -> int:
    """Decode a TLV value as a big-endian unsigned integer."""
    return int.from_bytes(value, "big")
