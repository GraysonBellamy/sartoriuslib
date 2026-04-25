"""SBI command-token and decoding tables."""

from __future__ import annotations

from sartoriuslib.registry.units import Unit

__all__ = [
    "ESC",
    "SBI_COMMAND_TOKENS",
    "SBI_READ_ONLY_TOKENS",
    "SBI_UNIT_STRINGS",
    "TOKEN_PRINT",
    "TOKEN_SERIAL",
    "TOKEN_SOFTWARE",
    "TOKEN_TARE",
    "TOKEN_TYPE",
    "TOKEN_ZERO",
    "describe_token",
    "unit_from_sbi",
]


ESC: bytes = b"\x1b"

TOKEN_PRINT: bytes = ESC + b"P"
TOKEN_TARE: bytes = ESC + b"T"
TOKEN_ZERO: bytes = ESC + b"V"
TOKEN_TYPE: bytes = ESC + b"x1_"
TOKEN_SERIAL: bytes = ESC + b"x2_"
TOKEN_SOFTWARE: bytes = ESC + b"x3_"


SBI_COMMAND_TOKENS: dict[bytes, str] = {
    TOKEN_PRINT: "print",
    TOKEN_TARE: "tare_and_zero",
    ESC + b"K": "filter_very_stable",
    ESC + b"L": "filter_stable",
    ESC + b"M": "filter_unstable",
    ESC + b"N": "filter_very_unstable",
    ESC + b"O": "block_keys",
    ESC + b"Q": "beep",
    ESC + b"R": "unblock_keys",
    ESC + b"S": "restart",
    ESC + b"U": "tare",
    TOKEN_ZERO: "zero",
    ESC + b"Z": "internal_adjust",
    ESC + b"W": "external_adjust_default_weight",
    ESC + b"f0_": "menu_key",
    ESC + b"f1_": "start_calibration",
    ESC + b"f2_": "enter_key",
    ESC + b"f5_": "left_draft_shield_key",
    ESC + b"f6_": "right_draft_shield_key",
    ESC + b"p_": "print_key",
    ESC + b"m0_": "ionizer_status",
    ESC + b"m1_": "ionizer_on",
    ESC + b"m2_": "ionizer_off",
    ESC + b"s0_": "hold_menu_key",
    ESC + b"s3_": "cf_back_exit_cancel",
    ESC + b"w0_": "draft_shield_status",
    ESC + b"w1_": "draft_shield_open_left",
    ESC + b"w2_": "draft_shield_close",
    ESC + b"w3_": "draft_shield_open_upper",
    ESC + b"w4_": "draft_shield_open_right",
    ESC + b"w5_": "draft_shield_open_left_upper",
    ESC + b"w6_": "draft_shield_open_left_right",
    ESC + b"w7_": "draft_shield_open_right_upper",
    ESC + b"w8_": "draft_shield_open_all",
    TOKEN_TYPE: "print_weigher_type",
    TOKEN_SERIAL: "print_serial_number",
    TOKEN_SOFTWARE: "print_software_version",
}


SBI_READ_ONLY_TOKENS: frozenset[bytes] = frozenset(
    {
        TOKEN_PRINT,
        ESC + b"p_",
        TOKEN_TYPE,
        TOKEN_SERIAL,
        TOKEN_SOFTWARE,
        ESC + b"m0_",
        ESC + b"w0_",
    },
)


SBI_UNIT_STRINGS: dict[str, Unit] = {
    "g": Unit.G,
    "kg": Unit.KG,
    "mg": Unit.MG,
    "ug": Unit.UG,
    "µg": Unit.UG,
    "ct": Unit.CT,
    "lb": Unit.LB,
    "oz": Unit.OZ,
    "ozt": Unit.OZT,
    "tl": Unit.TAEL_HK,
    "tlh": Unit.TAEL_HK,
    "tl.hk": Unit.TAEL_HK,
    "tlsg": Unit.TAEL_SG,
    "tl.sg": Unit.TAEL_SG,
    "tlt": Unit.TAEL_TW,
    "tl.tw": Unit.TAEL_TW,
    "gr": Unit.GR,
    "dwt": Unit.DWT,
    "n": Unit.NEWTON,
    "N": Unit.NEWTON,
    "t": Unit.T,
}


def unit_from_sbi(text: str) -> Unit:
    """Decode an SBI unit string to :class:`Unit`.

    Unknown strings collapse to :attr:`Unit.UNKNOWN` so new firmware formats
    remain parseable.
    """
    key = text.strip().replace(" ", "").lower()
    return SBI_UNIT_STRINGS.get(key, Unit.UNKNOWN)


def describe_token(token: bytes) -> str:
    """Human-readable token name used in errors and debug output."""
    return SBI_COMMAND_TOKENS.get(token, token.hex())
