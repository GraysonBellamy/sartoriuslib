"""xBPI subtype decoders — measurement, status block, typed float, errors.

This module turns an :class:`XbpiFrame`'s ``body`` into one of the
decoded-body dataclasses from :mod:`sartoriuslib.protocol.xbpi.types`.
It knows nothing about opcodes or sessions; callers at the command
layer pick the right decoder based on what they sent.

References: ``docs/protocol.md`` §8 (measurement/status decoding), §9
(temperature), §4 (subtype encoding).
"""

from __future__ import annotations

import struct

from sartoriuslib.errors import ErrorContext, SartoriusParseError
from sartoriuslib.protocol.xbpi.types import (
    ErrorBody,
    LongMeasurementBody,
    MeasurementBody,
    StatusBlockBody,
    TypedFloatBody,
)
from sartoriuslib.protocol.xbpi.units import decode_decimals, decode_sign, decode_unit

__all__ = [
    "OFF_SCALE_SENTINEL",
    "STABLE_FLAG",
    "STATUS_BLOCK_MARKER_PREFIX",
    "STATUS_BLOCK_MARKER_SUFFIX",
    "decode_error_body",
    "decode_long_measurement_body",
    "decode_measurement_body",
    "decode_status_block_body",
    "decode_typed_float_body",
    "is_status_block_body",
]


#: Bytes [0..4] of a short measurement body on an off-scale reading.
#: See ``docs/protocol.md`` §8.1.
OFF_SCALE_SENTINEL: bytes = b"\x7f\xff\xff\xff\xff"

#: Bit in a measurement-body flags byte (byte [7]) that marks a stable
#: reading. Universal across MSE/WZA/BCE per the design note in §7 —
#: preferred over the status-block state byte.
STABLE_FLAG: int = 0x40

#: Status-block marker bytes [1..2]. Reliably ``00 81`` (§8.2).
STATUS_BLOCK_MARKER_PREFIX: bytes = b"\x00\x81"
#: Status-block marker bytes [5..6]. Reliably ``10 00`` (§8.2).
STATUS_BLOCK_MARKER_SUFFIX: bytes = b"\x10\x00"

#: Body lengths for the fixed-size subtypes handled here.
_SHORT_MEASUREMENT_LEN: int = 8
_STATUS_BLOCK_LEN: int = 8
_LONG_MEASUREMENT_LEN: int = 17
_TYPED_FLOAT_LEN: int = 5
_ERROR_BODY_LEN: int = 1

#: Long-measurement delimiter byte (always ``0x48``, same value as the
#: subtype).
_LONG_MEASUREMENT_DELIMITER: int = 0x48

#: Status-block state-byte values that mark off-scale conditions on Cubis
#: (``docs/protocol.md`` §8.2). Non-Cubis captures of these states are not
#: yet available.
_STATE_OVERLOAD: int = 0x82
_STATE_UNDERLOAD: int = 0x84

#: "Measurement valid" base bit present in every Cubis state byte; absent
#: on WZA/BCE.
_CUBIS_STATE_BASE: int = 0x80

#: Status-block stability bit (universal — masked from ``state`` on
#: Cubis, same position on non-Cubis).
_STATE_STABLE_BIT: int = 0x08

#: Cubis status-byte bits: ADC trust and isoCAL attention.
_STATUS_ADC_TRUSTED: int = 0x08
_STATUS_ISOCAL_DUE: int = 0x10


# ---------------------------------------------------------------------------
# Measurement (short, 8-byte body — subtype 0x48)
# ---------------------------------------------------------------------------


def is_status_block_body(body: bytes) -> bool:
    """Heuristic: does this 8-byte body look like a status block?

    Short measurements and status blocks share subtype ``0x48`` and both
    carry 8-byte bodies. Disambiguation uses the §8.2 marker pattern:
    bytes [1..2] == ``00 81`` and bytes [5..6] == ``10 00`` reliably
    identify a status block; short measurements only match by accident.
    """
    return (
        len(body) == _STATUS_BLOCK_LEN
        and body[1:3] == STATUS_BLOCK_MARKER_PREFIX
        and body[5:7] == STATUS_BLOCK_MARKER_SUFFIX
    )


def decode_measurement_body(body: bytes) -> MeasurementBody:
    """Decode an 8-byte short measurement body.

    The value is ``float32`` big-endian in bytes [0..3]. Bytes [5..6]
    pack decimals (high nibble of [5]), sign (top 2 bits of [6]), and
    the base-unit ID (low 6 bits of [6]). Byte [7]'s ``0x40`` bit marks
    a stable reading universally across families. An off-scale reading
    presents the ``7f ff ff ff ff`` sentinel in bytes [0..4]; the
    status block disambiguates overload vs underload, so this decoder
    just reports ``off_scale`` and leaves ``value`` as ``None``.

    .. note::

        The MSE Cubis emits the same ``7f ff ff ff ff`` sentinel for
        ~6 frames (~2 s) immediately after :meth:`Balance.zero` while
        the cell recomputes its zero point. The wire is ambiguous —
        from the body alone we can't tell "cell busy" apart from
        "overload" or "underload". Callers that need that distinction
        invoke :meth:`Balance.status` (xBPI ``0x32``); the status
        block's ``state`` byte carries the disambiguation
        (``0x82``/``0x84`` for overload/underload, plain stable/
        unstable for a settling cell). Observed on hardware day —
        :class:`Reading` faithfully reflects ``value=None`` /
        ``off_scale=True`` either way.
    """
    if len(body) != _SHORT_MEASUREMENT_LEN:
        raise SartoriusParseError(
            f"short measurement body must be {_SHORT_MEASUREMENT_LEN} bytes, got {len(body)}",
            context=ErrorContext(raw_response=bytes(body)),
        )
    raw = bytes(body)
    off_scale = raw[0:5] == OFF_SCALE_SENTINEL
    if off_scale:
        value: float | None = None
    else:
        value = struct.unpack(">f", raw[0:4])[0]
    aux = raw[4]
    byte5 = raw[5]
    byte6 = raw[6]
    flags = raw[7]
    decimals = decode_decimals(byte5)
    sign = decode_sign(byte6)
    unit = decode_unit(byte6)
    stable = bool(flags & STABLE_FLAG)
    return MeasurementBody(
        raw=raw,
        value=value,
        aux=aux,
        decimals=decimals,
        unit=unit,
        sign=sign,
        stable=stable,
        off_scale=off_scale,
        unit_raw=byte6,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Status block (8-byte body — subtype 0x48 from opcode 0x30)
# ---------------------------------------------------------------------------


def decode_status_block_body(body: bytes) -> StatusBlockBody:
    """Decode an 8-byte status block.

    See ``docs/protocol.md`` §8.2. The portable stability bit is
    ``state & 0x08`` — Cubis encodes ``state=0x88`` stable vs ``0x80``
    unstable (the ``0x80`` base marks "measurement valid"), while
    WZA/BCE use ``state=0x08`` vs ``0x00``. Overload (state ``0x82``)
    and underload (``0x84``) have only been captured on Cubis; on
    non-Cubis this decoder still reports them when the exact pattern
    matches, and ``False`` otherwise.

    ``adc_trusted`` and ``isocal_due`` are MSE-only; decoded as ``None``
    when the frame does not look like a Cubis-style status block (base
    bit ``0x80`` clear in ``state``).
    """
    if len(body) != _STATUS_BLOCK_LEN:
        raise SartoriusParseError(
            f"status block must be {_STATUS_BLOCK_LEN} bytes, got {len(body)}",
            context=ErrorContext(raw_response=bytes(body)),
        )
    raw = bytes(body)
    aux_flag = raw[0]
    state = raw[3]
    status = raw[4]
    sequence = raw[7]
    stable = bool(state & _STATE_STABLE_BIT)
    overload = state == _STATE_OVERLOAD
    underload = state == _STATE_UNDERLOAD
    # Cubis signature: the "measurement valid" base bit 0x80 is set on
    # stable/unstable/overload/underload states (0x88/0x80/0x82/0x84).
    # Non-Cubis never sets 0x80, so treat ``adc_trusted``/``isocal_due``
    # as Cubis-only signals.
    cubis_shape = bool(state & _CUBIS_STATE_BASE)
    adc_trusted: bool | None = bool(status & _STATUS_ADC_TRUSTED) if cubis_shape else None
    isocal_due: bool | None = bool(status & _STATUS_ISOCAL_DUE) if cubis_shape else None
    return StatusBlockBody(
        raw=raw,
        aux_flag=aux_flag,
        state=state,
        status=status,
        sequence=sequence,
        stable=stable,
        overload=overload,
        underload=underload,
        adc_trusted=adc_trusted,
        isocal_due=isocal_due,
    )


# ---------------------------------------------------------------------------
# Long measurement (17-byte body — subtype 0x48 from 0x1E 09 30)
# ---------------------------------------------------------------------------


def decode_long_measurement_body(body: bytes) -> LongMeasurementBody:
    """Decode a 17-byte long streaming measurement (§8.3).

    Layout is short measurement (8 B) + delimiter (1 B, always ``0x48``)
    + status block (8 B). The delimiter matches the subtype byte by
    coincidence.
    """
    if len(body) != _LONG_MEASUREMENT_LEN:
        raise SartoriusParseError(
            f"long measurement body must be {_LONG_MEASUREMENT_LEN} bytes, got {len(body)}",
            context=ErrorContext(raw_response=bytes(body)),
        )
    raw = bytes(body)
    measurement = decode_measurement_body(raw[0:_SHORT_MEASUREMENT_LEN])
    delimiter = raw[_SHORT_MEASUREMENT_LEN]
    if delimiter != _LONG_MEASUREMENT_DELIMITER:
        raise SartoriusParseError(
            f"long measurement delimiter must be 0x{_LONG_MEASUREMENT_DELIMITER:02x}, "
            f"got 0x{delimiter:02x}",
            context=ErrorContext(raw_response=raw),
        )
    status = decode_status_block_body(raw[_SHORT_MEASUREMENT_LEN + 1 :])
    return LongMeasurementBody(
        measurement=measurement,
        delimiter=delimiter,
        status=status,
    )


# ---------------------------------------------------------------------------
# Typed float (5-byte body — subtype 0x35)
# ---------------------------------------------------------------------------


def decode_typed_float_body(body: bytes) -> TypedFloatBody:
    """Decode a 5-byte typed-float body: ``float32 BE`` + 1-byte aux."""
    if len(body) != _TYPED_FLOAT_LEN:
        raise SartoriusParseError(
            f"typed_float body must be {_TYPED_FLOAT_LEN} bytes, got {len(body)}",
            context=ErrorContext(raw_response=bytes(body)),
        )
    raw = bytes(body)
    value = struct.unpack(">f", raw[0:4])[0]
    aux = raw[4]
    return TypedFloatBody(value=value, aux=aux)


# ---------------------------------------------------------------------------
# Error (1-byte body — subtype 0x01)
# ---------------------------------------------------------------------------


def decode_error_body(body: bytes) -> ErrorBody:
    """Decode a 1-byte error body into :class:`ErrorBody`.

    The single byte is the device error code (``0x03`` / ``0x04`` /
    ``0x06`` / ``0x07`` / ``0x10`` / ``0x11`` — see
    ``docs/protocol.md`` §6). Mapping to typed exceptions happens at
    the session layer; this decoder stays neutral.
    """
    if len(body) != _ERROR_BODY_LEN:
        raise SartoriusParseError(
            f"error body must be {_ERROR_BODY_LEN} byte, got {len(body)}",
            context=ErrorContext(raw_response=bytes(body)),
        )
    return ErrorBody(code=body[0])
