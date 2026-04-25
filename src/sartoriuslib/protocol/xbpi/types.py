"""Immutable xBPI wire-level types.

:class:`XbpiFrame` is what :func:`sartoriuslib.protocol.xbpi.framing.parse_frame`
produces from a balance→host reply: parsed but not yet interpreted.
:class:`SubtypeFamily` groups the reply subtype byte into the families from
``docs/protocol.md`` §4. Decoded bodies (measurement, status, typed-float,
error) live here as frozen dataclasses too.

Per-protocol types live here; the protocol-neutral public :class:`Reading`
that the :class:`Balance` facade returns is composed *from* these.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sartoriuslib.registry.units import Sign, Unit

__all__ = [
    "ErrorBody",
    "LongMeasurementBody",
    "MeasurementBody",
    "StatusBlockBody",
    "SubtypeFamily",
    "TypedFloatBody",
    "XbpiFrame",
]


class SubtypeFamily(IntEnum):
    """Top-level classifier for the reply subtype byte.

    The xBPI subtype byte packs a family (high nibble) and a body-length
    hint (low nibble, per ``docs/protocol.md`` §4). Parsers dispatch on
    family, not on the raw subtype, so a new same-family subtype does not
    need a parser change.
    """

    ACK = 0x00
    ERROR = 0x01
    BARGRAPH = 0x10
    STRUCTURED_U32 = 0x14
    SHORT_DATA = 0x20
    TYPED_FLOAT_ALT = 0x34
    TYPED_FLOAT = 0x35
    SHORT_BLOB = 0x40
    MEASUREMENT = 0x48
    LONG_DATA = 0x50
    UNKNOWN = 0xFF


@dataclass(frozen=True, slots=True)
class XbpiFrame:
    """One fully-validated balance→host xBPI frame.

    ``length`` is the value of byte[0] — the count of bytes that follow
    the length byte (so ``len(raw) == length + 1``). ``marker`` is
    byte[1], always ``0x41`` for device-to-host replies;
    :func:`parse_frame` raises :class:`SartoriusFrameError` otherwise.
    ``body`` is everything between the subtype and the checksum; ``raw``
    is the full on-wire bytes.
    """

    length: int
    marker: int
    subtype: int
    body: bytes
    checksum: int
    raw: bytes


@dataclass(frozen=True, slots=True)
class MeasurementBody:
    """8-byte short measurement body (subtype ``0x48``, non-status-block form).

    Raw fields mirror ``docs/protocol.md`` §8.1 byte-for-byte. Derived
    fields (``value``, ``unit``, ``sign``, ``stable``, ``decimals``,
    ``off_scale``) are the decoded view callers build a :class:`Reading`
    from.

    ``value`` is ``None`` on the off-scale sentinel (bytes[0..4] ==
    ``7f ff ff ff ff``). Distinguishing overload from underload requires
    the status block; the measurement alone cannot tell them apart.
    """

    raw: bytes
    value: float | None
    aux: int
    decimals: int
    unit: Unit
    sign: Sign
    stable: bool
    off_scale: bool
    unit_raw: int
    flags: int


@dataclass(frozen=True, slots=True)
class StatusBlockBody:
    """8-byte status block (subtype ``0x48``, from opcode ``0x30``).

    See ``docs/protocol.md`` §8.2. State/status encoding is family-specific;
    the derived ``stable`` / ``overload`` / ``underload`` flags extract the
    portable bits. ``adc_trusted`` and ``isocal_due`` are MSE-only signals
    and are ``None`` when the source is not a Cubis.
    """

    raw: bytes
    aux_flag: int
    state: int
    status: int
    sequence: int
    stable: bool
    overload: bool
    underload: bool
    adc_trusted: bool | None
    isocal_due: bool | None


@dataclass(frozen=True, slots=True)
class LongMeasurementBody:
    """17-byte long streaming-measurement body (subtype ``0x48``).

    Emitted when the caller requests ``0x1E 09 30`` (short + status-block
    concatenation per ``docs/protocol.md`` §8.3). The delimiter byte is
    always ``0x48`` — the same value as the subtype — and separates the
    two 8-byte halves.
    """

    measurement: MeasurementBody
    delimiter: int
    status: StatusBlockBody


@dataclass(frozen=True, slots=True)
class TypedFloatBody:
    """5-byte typed-float body (subtype ``0x35``).

    Used by temperature, capacity, and increment reads. The ``aux`` byte
    is an extra payload byte that carries unit-family or decimal-place
    information depending on the opcode.
    """

    value: float
    aux: int


@dataclass(frozen=True, slots=True)
class ErrorBody:
    """1-byte error body (subtype ``0x01``).

    ``code`` is the raw device error code; mapping to typed exceptions
    happens at the protocol-client / session layer so the codec can stay
    agnostic.
    """

    code: int
