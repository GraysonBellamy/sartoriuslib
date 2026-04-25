"""xBPI opcode, subtype, and error-code tables.

Tables in this module are *lookup data* — pure-Python dicts with no
behaviour. The codec treats every table as an open set: unknown values
decode to :attr:`SubtypeFamily.UNKNOWN` or an otherwise-empty mapping
lookup rather than raising. Forward-compatibility beats strictness here
because Sartorius firmware revisions routinely introduce new subtypes.

References: ``docs/protocol.md`` §4 (subtype families), §6 (error codes),
§7 (opcode inventory).
"""

from __future__ import annotations

from sartoriuslib.protocol.xbpi.types import SubtypeFamily

__all__ = [
    "ERROR_CODE_REASONS",
    "OPCODE_NAMES",
    "body_length_for_subtype",
    "subtype_family",
]


# ---------------------------------------------------------------------------
# Error codes (subtype 0x01 body byte) — ``docs/protocol.md`` §6.
# ---------------------------------------------------------------------------

ERROR_CODE_REASONS: dict[int, str] = {
    0x03: "value out of range",
    0x04: "unknown opcode",
    0x06: "operation not applicable",
    0x07: "invalid or missing args",
    0x10: "index out of range",
    0x11: "unknown (BCE variant of 0x10)",
}


# ---------------------------------------------------------------------------
# Subtype classification — ``docs/protocol.md`` §4.
# ---------------------------------------------------------------------------

# Concrete subtype values we know about, mapped to their family. Anything
# not in this map is classified by :func:`subtype_family` via high-nibble
# inspection, with ``UNKNOWN`` as the last-resort return.
_KNOWN_SUBTYPES: dict[int, SubtypeFamily] = {
    0x00: SubtypeFamily.ACK,
    0x01: SubtypeFamily.ERROR,
    0x12: SubtypeFamily.BARGRAPH,
    0x14: SubtypeFamily.STRUCTURED_U32,
    0x21: SubtypeFamily.SHORT_DATA,
    0x22: SubtypeFamily.SHORT_DATA,
    0x24: SubtypeFamily.SHORT_DATA,
    0x34: SubtypeFamily.TYPED_FLOAT_ALT,
    0x35: SubtypeFamily.TYPED_FLOAT,
    0x41: SubtypeFamily.SHORT_BLOB,
    0x43: SubtypeFamily.LONG_DATA,
    0x44: SubtypeFamily.LONG_DATA,
    0x45: SubtypeFamily.LONG_DATA,
    0x48: SubtypeFamily.MEASUREMENT,
    0x4A: SubtypeFamily.LONG_DATA,
    0x50: SubtypeFamily.LONG_DATA,
    0x51: SubtypeFamily.LONG_DATA,
    0x54: SubtypeFamily.LONG_DATA,
}


#: High-nibble fallback map: when a subtype isn't explicitly tabulated,
#: its high nibble decides its family. The low nibble is typically a
#: body-length hint for the 0x2X / 0x4X / 0x5X families, so unknown
#: subtypes in those ranges still classify sensibly.
_HIGH_NIBBLE_FAMILIES: dict[int, SubtypeFamily] = {
    0x10: SubtypeFamily.BARGRAPH,
    0x20: SubtypeFamily.SHORT_DATA,
    0x40: SubtypeFamily.LONG_DATA,
    0x50: SubtypeFamily.LONG_DATA,
}


def subtype_family(subtype: int) -> SubtypeFamily:
    """Return the family classifier for a reply subtype byte.

    Falls back to high-nibble dispatch for subtypes not explicitly
    tabulated, and to :attr:`SubtypeFamily.UNKNOWN` when even that fails.
    """
    if subtype in _KNOWN_SUBTYPES:
        return _KNOWN_SUBTYPES[subtype]
    return _HIGH_NIBBLE_FAMILIES.get(subtype & 0xF0, SubtypeFamily.UNKNOWN)


def body_length_for_subtype(subtype: int) -> int | None:
    """Expected body length for ``subtype``, or ``None`` if variable.

    Implements the §4 formula: "high nibble = type class, low nibble =
    body length for types 0..4; for type 5, length = 16 + low". Returns
    ``None`` for subtypes whose body length is genuinely variable
    (notably ``0x48`` which carries either 8 or 17 bytes, and ``0x00``
    which usually carries 0 but occasionally a variable body — e.g.
    opcode ``0xBC`` module list).
    """
    # 0x48 measurement: 8 *or* 17 — caller disambiguates by the length
    # byte on the outer frame. 0x00 ACK: usually empty but 0xBC's module
    # list rides this subtype with a variable body. Both are variable.
    if subtype in _VARIABLE_BODY_SUBTYPES:
        return None
    high = subtype & 0xF0
    low = subtype & 0x0F
    if high in _FIXED_LOW_NIBBLE_LENGTH_FAMILIES:
        return low
    if high == _LONG_STRING_FAMILY_HIGH:
        return _LONG_STRING_FAMILY_OFFSET + low
    return None


#: Subtypes whose body length is genuinely variable.
_VARIABLE_BODY_SUBTYPES: frozenset[int] = frozenset({0x00, 0x48})

#: High-nibble families where the low nibble directly encodes body length.
_FIXED_LOW_NIBBLE_LENGTH_FAMILIES: frozenset[int] = frozenset({0x00, 0x10, 0x20, 0x30, 0x40})

#: 0x5X subtypes use ``low + 16`` instead of ``low`` (per §4).
_LONG_STRING_FAMILY_HIGH: int = 0x50
_LONG_STRING_FAMILY_OFFSET: int = 16


# ---------------------------------------------------------------------------
# Opcode name lookup — best-effort, for logs, errors, and the ``sarto-decode``
# CLI. Missing opcodes decode to ``None`` so callers fall back to ``0xXX``
# numeric form.
# ---------------------------------------------------------------------------

OPCODE_NAMES: dict[int, str] = {
    # Device information (§7.1)
    0x00: "read_software_version",
    0x01: "read_factory_number",
    0x02: "read_weigh_cell_model",
    0x03: "read_user_id",
    0x05: "read_oem_text",
    0x07: "read_manufacturer",
    0x0A: "read_configuration_data",
    0x0F: "read_balance_info",
    # Metrology (§7.2)
    0x0B: "read_threshold_0b",
    0x0C: "read_max",
    0x0D: "read_increment",
    0x0E: "read_threshold_0e",
    # Tare/zero (§7.3)
    0x14: "tare",
    0x15: "abort_combined_tare",
    0x17: "abort_tare",
    0x18: "zero",
    0x19: "abort_zeroing",
    # Weight reads (§7.4)
    0x1C: "read_appl_tare",
    0x1E: "read_net_weight",
    0x1F: "read_net_weight_hires",
    0x20: "read_gross_weight",
    0x21: "read_gross_weight_hires",
    0x22: "read_tare",
    # Filter/weighing mode (§7.5)
    0x26: "read_weighing_mode",
    0x2C: "write_weighing_mode",
    0x57: "read_cycle_time",
    # Status (§7.6)
    0x2F: "read_gross_bargraph",
    0x30: "read_balance_status_block",
    0x32: "read_balance_status",
    0x35: "read_time_stamp",
    0x36: "read_on_off_status",
    # Cal/adjust (§7.7)
    0x28: "start_adjustment",
    0x29: "abort_adjustment",
    0x76: "read_temperature_sensors",
    0x78: "read_adjustment_unit",
    0x79: "write_adjustment_unit",
    # Menus / presets (§7.8)
    0x46: "read_menu_from_eeprom",
    0x47: "save_menu_to_eeprom",
    0x54: "read_stop_flags",
    0x55: "read_parameter_table",
    0x56: "write_parameter_table",
    # System (§7.9)
    0x58: "initiate_reset",
    # Data interface (§7.10)
    0x5C: "set_baud_rate",
    0x71: "read_sbn_address",
    0x72: "write_sbn_address",
    # Extended — Cubis-dominant, partial BCE (§7.11)
    0xB9: "read_last_cal_record",
    0xBA: "config_generation_counter",
    0xBC: "read_module_list",
}
