"""Pint-compatible unit string mapping — :func:`to_pint`.

Cross-library uniform helper (unified spec §K). Every sibling library
(alicatlib, watlowlib, nidaqlib) exposes the same free-function
``to_pint(unit) -> str | None`` in ``<lib>.units`` so consumers that
project sample data into pint-aware downstream tools (e.g. capa's
report generator) can resolve units uniformly without knowing which
device produced a row.

``pint`` is **not** a runtime dependency — ``to_pint`` returns plain
strings; passing them to ``pint`` happens in the consumer.

Lossy by design — units pint does not natively model (Hong Kong /
Singapore / Taiwan tael, Austrian carat, exotic Asian weight units,
parts-per-pound) return ``None``. Don't try to encode these
out-of-band: the consumer decides whether to fall back to a raw
multiplier or drop the row.

The free function is canonical — :class:`Quantity` deliberately does
**not** grow a ``to_pint()`` method so the cross-lib surface stays
uniform (sibling libs don't have a ``Quantity`` wrapper to hang one
off, and adding a method here would break symmetric callers).
"""

from __future__ import annotations

from sartoriuslib.registry.units import Unit

__all__ = ["to_pint"]


#: Map from :class:`Unit` to its pint-canonical string, or ``None``
#: where pint cannot model the unit (every exotic Asian weight, the
#: ``ptplb`` parts-per-pound display unit, the combined ``lb_oz``).
_UNIT_TO_PINT: dict[Unit, str | None] = {
    Unit.G: "gram",
    Unit.KG: "kilogram",
    Unit.MG: "milligram",
    Unit.UG: "microgram",
    Unit.T: "metric_ton",
    Unit.LB: "pound",
    Unit.OZ: "ounce",
    Unit.OZT: "troy_ounce",
    Unit.CT: "carat",
    Unit.GR: "grain",
    Unit.DWT: "pennyweight",
    Unit.NEWTON: "newton",
    # Units pint doesn't model out of the box — return None and let
    # the consumer decide what to do.
    Unit.USERDEF: None,
    Unit.PARTS_PER_POUND: None,
    Unit.TAEL_HK: None,
    Unit.TAEL_SG: None,
    Unit.TAEL_TW: None,
    Unit.TAEL_CN: None,
    Unit.MOMME: None,
    Unit.CT_AU: None,
    Unit.TOLA: None,
    Unit.BAHT: None,
    Unit.MESGAL: None,
    Unit.LB_OZ: None,
    Unit.UNKNOWN: None,
}


def _check_unit_coverage() -> None:
    """Fail fast if a :class:`Unit` member is missing from the map.

    Runs at import. A new ``Unit`` enum value that slips through review
    without an explicit pint mapping (even ``None``) would silently
    fall through to ``dict.get`` and return ``None`` from
    :func:`to_pint` — which is the safe default but masks the real
    problem: nobody thought about how to map the new unit. Force the
    decision at import time.
    """
    missing = [u for u in Unit if u not in _UNIT_TO_PINT]
    if missing:  # pragma: no cover — guarded by test_to_pint_coverage
        raise RuntimeError(
            f"Unit members missing from sartoriuslib.units._UNIT_TO_PINT: {missing!r}",
        )


_check_unit_coverage()


def to_pint(unit: Unit | str | None) -> str | None:
    """Return a pint-compatible unit string for ``unit``, or ``None``.

    Accepts a :class:`Unit` enum member, the matching string value
    (``"g"``, ``"kg"``, ...), or ``None``. Unknown strings and units
    pint cannot model return ``None`` — never raise.

    Example::

        >>> from sartoriuslib.units import to_pint
        >>> to_pint(Unit.G)
        'gram'
        >>> to_pint("kg")
        'kilogram'
        >>> to_pint("tl.hk")  # Hong Kong tael — pint can't model
        >>> to_pint(None)
    """
    if unit is None:
        return None
    # ``Unit`` is a :class:`StrEnum`, so it satisfies ``isinstance(_, str)``.
    # Match enum first so we don't re-coerce members back through ``Unit(...)``.
    if isinstance(unit, Unit):
        return _UNIT_TO_PINT.get(unit)
    try:
        return _UNIT_TO_PINT.get(Unit(unit))
    except ValueError:
        return None
