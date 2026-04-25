"""Unit enum + display-unit parameter mapping.

Two maps touch this file:

1. **Measurement-frame wire bytes** → :class:`Unit`. Lives in
   :mod:`sartoriuslib.protocol.xbpi.units` (``0x02 g`` / ``0x03 kg`` /
   ``0x0D mg`` / ``0x17 N``). That map decodes what a balance *reports*
   in an 8-byte measurement body.

2. **Parameter-table index 7 (display unit)** → :class:`Unit`. Lives
   here as :data:`DISPLAY_UNIT_CODE_TO_UNIT`. That map decodes what a
   balance *is configured to display*; it is the full 24-entry table
   from ``docs/protocol.md`` §10.1 idx 7.

The two address spaces are different — the display-unit codes are a
dense 1..24 enumeration, while the measurement-frame bytes are a
sparse 6-bit ID space (0x02, 0x03, 0x0D, 0x17, …). Keeping the maps
separate keeps each unit of code honest about what it actually knows.

Unknown codes decode to :attr:`Unit.UNKNOWN` rather than raising —
forward-compatibility for firmware we have not captured.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "DISPLAY_UNIT_CODE_TO_UNIT",
    "Sign",
    "Unit",
    "unit_to_display_code",
]


class Unit(StrEnum):
    """Physical unit of a measurement / display unit.

    Values are the short symbol (or short tag for units without a
    single canonical symbol) so ``str(Unit.G) == "g"`` for log lines
    and CSV columns.

    Membership covers the 24-entry p07 display-unit table plus
    :attr:`UNKNOWN` for forward-compat on measurement-frame decoding.
    """

    USERDEF = "userdef"
    """User-defined unit (p07 idx 1). A multiplier + label live in a
    separate register not yet located; the balance displays it scaled
    from grams."""

    G = "g"
    KG = "kg"
    CT = "ct"
    """Metric carat (1 ct = 0.2 g)."""
    LB = "lb"
    OZ = "oz"
    OZT = "ozt"
    """Troy ounce."""
    TAEL_HK = "tl.hk"
    """Hong Kong tael."""
    TAEL_SG = "tl.sg"
    """Singapore / Malaysia tael."""
    TAEL_TW = "tl.tw"
    """Taiwan tael."""
    GR = "gr"
    """Grain (1 gr ≈ 64.79891 mg)."""
    DWT = "dwt"
    """Pennyweight (1 dwt = 24 gr)."""
    MG = "mg"
    PARTS_PER_POUND = "/lb"
    """Parts per pound (p07 ``ptplb``)."""
    TAEL_CN = "tl.cn"
    """Chinese tael."""
    MOMME = "momme"
    """Japanese momme."""
    CT_AU = "ct_au"
    """Austrian carat — non-metric; used in p07 idx 17."""
    TOLA = "tola"
    """South Asian tola."""
    BAHT = "baht"
    """Thai baht weight."""
    MESGAL = "mesgal"
    """Mesghal (p07 idx 20)."""
    T = "t"
    """Metric ton."""
    LB_OZ = "lb_oz"
    """Pounds-and-ounces combined display."""
    NEWTON = "N"
    UG = "µg"
    """Microgram."""

    UNKNOWN = "unknown"


class Sign(StrEnum):
    """Sign of a measurement as encoded on the wire."""

    ZERO = "zero"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


#: Parameter-table index 7 (``display_unit``) code → :class:`Unit`.
#:
#: Source: ``docs/protocol.md`` §10.1 idx 7. The balance returns the
#: 1-based code in the first TLV of a ``read_parameter(7)`` reply; the
#: table below turns that code into a :class:`Unit`.
DISPLAY_UNIT_CODE_TO_UNIT: dict[int, Unit] = {
    1: Unit.USERDEF,
    2: Unit.G,
    3: Unit.KG,
    4: Unit.CT,
    5: Unit.LB,
    6: Unit.OZ,
    7: Unit.OZT,
    8: Unit.TAEL_HK,
    9: Unit.TAEL_SG,
    10: Unit.TAEL_TW,
    11: Unit.GR,
    12: Unit.DWT,
    13: Unit.MG,
    14: Unit.PARTS_PER_POUND,
    15: Unit.TAEL_CN,
    16: Unit.MOMME,
    17: Unit.CT_AU,
    18: Unit.TOLA,
    19: Unit.BAHT,
    20: Unit.MESGAL,
    21: Unit.T,
    22: Unit.LB_OZ,
    23: Unit.NEWTON,
    24: Unit.UG,
}


#: Reverse of :data:`DISPLAY_UNIT_CODE_TO_UNIT` — built once at import
#: time. Used by the typed setter for the display-unit parameter.
_UNIT_TO_DISPLAY_CODE: dict[Unit, int] = {u: c for c, u in DISPLAY_UNIT_CODE_TO_UNIT.items()}


def unit_to_display_code(unit: Unit) -> int:
    """Turn a :class:`Unit` into its p07 display-unit code.

    Raises :class:`ValueError` for :attr:`Unit.UNKNOWN` and any
    :class:`Unit` member not in the display-unit table (should be
    unreachable today — every member is in the table — but stays
    defensive for future additions).
    """
    try:
        return _UNIT_TO_DISPLAY_CODE[unit]
    except KeyError as exc:
        raise ValueError(
            f"{unit!r} has no display-unit code in the p07 parameter table",
        ) from exc
