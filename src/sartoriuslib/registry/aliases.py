"""Fuzzy string → typed-value resolvers.

Scripts, REPL use, and the ``sarto-*`` CLI all want to accept
``"Stable"`` / ``"stable"`` / ``"STABLE"`` / ``"very stable"`` /
``"very_stable"`` as the same :class:`FilterMode`. Resolvers here do
one pass of normalisation (lowercase, collapse whitespace / hyphens to
underscores, strip punctuation) then look the canonical form up.

Design §16 Q4 leans toward inline tables for v1; if the alias maps
grow past what is readable here, promote them to a build-time artefact
then.
"""

from __future__ import annotations

import re
from enum import IntEnum

from sartoriuslib.errors import (
    ErrorContext,
    SartoriusValidationError,
    UnknownUnitError,
)
from sartoriuslib.registry.modes import (
    AutoZeroMode,
    DisplayAccuracyMode,
    FilterMode,
    IsoCalMode,
    MenuAccessMode,
    OutputMode,
    TareBehavior,
)
from sartoriuslib.registry.units import Unit

__all__ = [
    "normalise",
    "resolve_auto_zero",
    "resolve_display_accuracy",
    "resolve_filter_mode",
    "resolve_isocal_mode",
    "resolve_menu_access",
    "resolve_output_mode",
    "resolve_tare_behavior",
    "resolve_unit",
]


#: Collapse runs of whitespace / hyphens / periods into a single ``_``.
_NORMALISE_SEPARATORS = re.compile(r"[\s\-.]+")
#: Strip punctuation that remains after separator collapse. Uses ``\W``
#: in default (Unicode-aware) mode so ``µ`` and other letters survive.
_NORMALISE_STRIPS = re.compile(r"\W")


def normalise(raw: str) -> str:
    """Canonicalise ``raw`` to ``lower_snake_case_ish``.

    ``"Very Stable"`` / ``"very-stable"`` / ``"very.stable"`` all
    collapse to ``"very_stable"``. Unicode is lowercased but not
    folded — ``"µg"`` stays ``"µg"``.
    """
    text = raw.strip().lower()
    text = _NORMALISE_SEPARATORS.sub("_", text)
    return _NORMALISE_STRIPS.sub("", text)


# ---------------------------------------------------------------------------
# Unit aliases.
# ---------------------------------------------------------------------------


#: Units where ``normalise(member.value)`` collides with a more natural
#: alias — ``"/lb" → "lb"`` would shadow :attr:`Unit.LB`. These rely on
#: the explicit long-form aliases below instead of the auto-generated
#: short-form ones.
_UNIT_AMBIGUOUS_SHORTS: frozenset[Unit] = frozenset({Unit.PARTS_PER_POUND})

#: Human aliases for :class:`Unit`. Includes the StrEnum value itself
#: (so ``resolve_unit("g")`` works), common long forms, and obvious
#: typos / plurals.
_UNIT_ALIASES: dict[str, Unit] = {
    # canonical StrEnum values first — skipping ambiguous ones that would
    # shadow simpler units.
    **{
        normalise(u.value): u
        for u in Unit
        if u is not Unit.UNKNOWN and u not in _UNIT_AMBIGUOUS_SHORTS
    },
    # parts-per-pound only reachable via long form
    "parts_per_pound": Unit.PARTS_PER_POUND,
    "ptplb": Unit.PARTS_PER_POUND,
    # long forms / synonyms
    "gram": Unit.G,
    "grams": Unit.G,
    "gramme": Unit.G,
    "kilogram": Unit.KG,
    "kilograms": Unit.KG,
    "milligram": Unit.MG,
    "milligrams": Unit.MG,
    "microgram": Unit.UG,
    "micrograms": Unit.UG,
    "ug": Unit.UG,
    "carat": Unit.CT,
    "carats": Unit.CT,
    "pound": Unit.LB,
    "pounds": Unit.LB,
    "ounce": Unit.OZ,
    "ounces": Unit.OZ,
    "troy_ounce": Unit.OZT,
    "troy_ounces": Unit.OZT,
    "grain": Unit.GR,
    "grains": Unit.GR,
    "pennyweight": Unit.DWT,
    "newton": Unit.NEWTON,
    "newtons": Unit.NEWTON,
    "ton": Unit.T,
    "tons": Unit.T,
    "tonne": Unit.T,
    "tonnes": Unit.T,
    "metric_ton": Unit.T,
    "user_defined": Unit.USERDEF,
    "user_def": Unit.USERDEF,
    # tael variants — the short forms include the dot; normalise strips it
    "hongkong_tael": Unit.TAEL_HK,
    "hk_tael": Unit.TAEL_HK,
    "singapore_tael": Unit.TAEL_SG,
    "sg_tael": Unit.TAEL_SG,
    "taiwan_tael": Unit.TAEL_TW,
    "tw_tael": Unit.TAEL_TW,
    "chinese_tael": Unit.TAEL_CN,
    "cn_tael": Unit.TAEL_CN,
}


def resolve_unit(raw: str | Unit) -> Unit:
    """Fuzzy-match ``raw`` to a :class:`Unit` member.

    Raises :class:`UnknownUnitError` if no alias matches.
    """
    if isinstance(raw, Unit):
        return raw
    key = normalise(raw)
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    raise UnknownUnitError(
        f"unknown unit {raw!r}",
        context=ErrorContext(extra={"input": raw, "normalised": key}),
    )


# ---------------------------------------------------------------------------
# Mode aliases (one resolver per typed-accessor pair).
# ---------------------------------------------------------------------------


def _build_mode_aliases[E: IntEnum](
    enum_cls: type[E],
    extra: dict[str, E] | None = None,
) -> dict[str, E]:
    """Build an alias dict from an enum's member names plus extras.

    Every :class:`IntEnum` member ``NAME`` → ``{normalise("NAME"): member}``.
    Callers pass ``extra`` for human-readable synonyms beyond the
    member names.
    """
    aliases: dict[str, E] = {
        normalise(m.name): m
        for m in enum_cls
        if m.value != 0  # skip UNKNOWN
    }
    if extra:
        aliases.update({normalise(k): v for k, v in extra.items()})
    return aliases


_FILTER_ALIASES = _build_mode_aliases(
    FilterMode,
    extra={
        "v_stable": FilterMode.VERY_STABLE,
        "vs": FilterMode.VERY_STABLE,
        "v_unstable": FilterMode.VERY_UNSTABLE,
        "vu": FilterMode.VERY_UNSTABLE,
    },
)

_AUTO_ZERO_ALIASES = _build_mode_aliases(
    AutoZeroMode,
    extra={
        "enabled": AutoZeroMode.ON,
        "disabled": AutoZeroMode.OFF,
        "true": AutoZeroMode.ON,
        "false": AutoZeroMode.OFF,
    },
)

_TARE_ALIASES = _build_mode_aliases(
    TareBehavior,
    extra={
        "wo_stab": TareBehavior.WITHOUT_STABILITY,
        "w_stab": TareBehavior.WITH_STABILITY,
        "at_stab": TareBehavior.AT_STABILITY,
    },
)

_DISPLAY_ACC_ALIASES = _build_mode_aliases(
    DisplayAccuracyMode,
    extra={
        "normal": DisplayAccuracyMode.DEFAULT,
        "lponoff": DisplayAccuracyMode.LOW_POWER_ON_OFF,
        "lp_onoff": DisplayAccuracyMode.LOW_POWER_ON_OFF,
        "low_power": DisplayAccuracyMode.LOW_POWER_ON_OFF,
        "minus_1_digit": DisplayAccuracyMode.MINUS_1_DIGIT,
        "minus_one_digit": DisplayAccuracyMode.MINUS_1_DIGIT,
        # Sartorius front-panel label is literal "-1 digit"; that
        # normalises to "_1_digit" after separator collapse.
        "_1_digit": DisplayAccuracyMode.MINUS_1_DIGIT,
        "1_digit": DisplayAccuracyMode.MINUS_1_DIGIT,
        "reduced": DisplayAccuracyMode.MINUS_1_DIGIT,
    },
)

_ISOCAL_ALIASES = _build_mode_aliases(
    IsoCalMode,
    extra={"enabled": IsoCalMode.ON, "disabled": IsoCalMode.OFF},
)

_OUTPUT_ALIASES = _build_mode_aliases(
    OutputMode,
    extra={
        "ind_no": OutputMode.MANUAL_IMMEDIATE,
        "ind_after": OutputMode.MANUAL_AFTER_STABILITY,
        "ind_at": OutputMode.MANUAL_AT_STABILITY,
        "auto_wo": OutputMode.AUTOPRINT_UNFILTERED,
        "auto_w": OutputMode.AUTOPRINT_STABLE,
        "autoprint": OutputMode.AUTOPRINT_STABLE,
    },
)

_MENU_ACCESS_ALIASES = _build_mode_aliases(
    MenuAccessMode,
    extra={
        "editable": MenuAccessMode.CAN_EDIT,
        "readonly": MenuAccessMode.READ_ONLY,
        "rd_only": MenuAccessMode.READ_ONLY,
        "locked": MenuAccessMode.READ_ONLY,
    },
)


def _resolve_enum[E: IntEnum](
    enum_cls: type[E],
    aliases: dict[str, E],
    raw: str | E | int,
) -> E:
    if isinstance(raw, enum_cls):
        return raw
    if isinstance(raw, int):
        try:
            return enum_cls(raw)
        except ValueError as exc:
            raise SartoriusValidationError(
                f"{raw} is not a valid {enum_cls.__name__}",
                context=ErrorContext(extra={"input": raw, "enum": enum_cls.__name__}),
            ) from exc
    key = normalise(raw)
    if key in aliases:
        return aliases[key]
    raise SartoriusValidationError(
        f"unknown {enum_cls.__name__} {raw!r}",
        context=ErrorContext(
            extra={
                "input": raw,
                "normalised": key,
                "enum": enum_cls.__name__,
                "known": sorted(aliases.keys()),
            },
        ),
    )


def resolve_filter_mode(raw: str | FilterMode | int) -> FilterMode:
    """Fuzzy-match to a :class:`FilterMode`. Raises on unknown input."""
    return _resolve_enum(FilterMode, _FILTER_ALIASES, raw)


def resolve_auto_zero(raw: str | AutoZeroMode | int) -> AutoZeroMode:
    """Fuzzy-match to an :class:`AutoZeroMode`."""
    return _resolve_enum(AutoZeroMode, _AUTO_ZERO_ALIASES, raw)


def resolve_tare_behavior(raw: str | TareBehavior | int) -> TareBehavior:
    """Fuzzy-match to a :class:`TareBehavior`."""
    return _resolve_enum(TareBehavior, _TARE_ALIASES, raw)


def resolve_display_accuracy(raw: str | DisplayAccuracyMode | int) -> DisplayAccuracyMode:
    """Fuzzy-match to a :class:`DisplayAccuracyMode`."""
    return _resolve_enum(DisplayAccuracyMode, _DISPLAY_ACC_ALIASES, raw)


def resolve_isocal_mode(raw: str | IsoCalMode | int) -> IsoCalMode:
    """Fuzzy-match to an :class:`IsoCalMode`."""
    return _resolve_enum(IsoCalMode, _ISOCAL_ALIASES, raw)


def resolve_output_mode(raw: str | OutputMode | int) -> OutputMode:
    """Fuzzy-match to an :class:`OutputMode`."""
    return _resolve_enum(OutputMode, _OUTPUT_ALIASES, raw)


def resolve_menu_access(raw: str | MenuAccessMode | int) -> MenuAccessMode:
    """Fuzzy-match to a :class:`MenuAccessMode`."""
    return _resolve_enum(MenuAccessMode, _MENU_ACCESS_ALIASES, raw)
