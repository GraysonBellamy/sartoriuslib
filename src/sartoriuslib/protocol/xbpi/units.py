"""xBPI unit-code decoding.

xBPI measurement frames pack the unit and sign into byte [6] of the 8-byte
body and the decimal-place count into byte [5]'s high nibble. See
``docs/protocol.md`` §8.4 for the full encoding.
"""

from __future__ import annotations

from sartoriuslib.registry.units import Sign, Unit

__all__ = [
    "SIGN_MASK",
    "UNIT_ID_MASK",
    "decode_decimals",
    "decode_sign",
    "decode_unit",
    "unit_byte_to_unit",
]

SIGN_MASK: int = 0xC0
UNIT_ID_MASK: int = 0x3F

#: Sign-bit values in byte [6] (top 2 bits of the byte).
SIGN_BITS_ZERO: int = 0x00
SIGN_BITS_POSITIVE: int = 0x40
SIGN_BITS_NEGATIVE: int = 0x80

# Base-unit IDs (low 6 bits of byte [6]) → Unit. Any value not in this
# map decodes to :attr:`Unit.UNKNOWN` — forward-compatibility for
# firmware we have not captured.
_UNIT_ID_TO_UNIT: dict[int, Unit] = {
    0x02: Unit.G,
    0x03: Unit.KG,
    0x0D: Unit.MG,
    0x17: Unit.NEWTON,
}


def decode_decimals(byte5: int) -> int:
    """Return the displayed decimal-place count encoded in byte [5].

    High nibble = decimals (``docs/protocol.md`` §8.4). The low nibble
    has a WZA-mg quirk that is *advisory* and ignored here.
    """
    return (byte5 >> 4) & 0x0F


def decode_sign(byte6: int) -> Sign:
    """Decode the sign bits (top 2) of byte [6].

    ``0x00`` = exactly zero, ``0x40`` = positive, ``0x80`` = negative.
    Any other combination (shouldn't occur — only one bit of the 2-bit
    field is set at a time on the wire) decodes to :attr:`Sign.UNKNOWN`.
    """
    bits = byte6 & SIGN_MASK
    if bits == SIGN_BITS_ZERO:
        return Sign.ZERO
    if bits == SIGN_BITS_POSITIVE:
        return Sign.POSITIVE
    if bits == SIGN_BITS_NEGATIVE:
        return Sign.NEGATIVE
    return Sign.UNKNOWN


def unit_byte_to_unit(unit_id: int) -> Unit:
    """Map a 6-bit base-unit ID to a :class:`Unit`.

    Unknown IDs decode to :attr:`Unit.UNKNOWN` — never raises — because
    new unit codes are expected as more of the parameter-table display
    enum gets mapped (see ``docs/protocol.md`` §10.1 index 7).
    """
    return _UNIT_ID_TO_UNIT.get(unit_id & UNIT_ID_MASK, Unit.UNKNOWN)


def decode_unit(byte6: int) -> Unit:
    """Full decode of byte [6] to a :class:`Unit` (strips the sign bits)."""
    return unit_byte_to_unit(byte6 & UNIT_ID_MASK)
