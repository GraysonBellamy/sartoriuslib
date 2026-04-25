"""xBPI wire codec — framing, TLV helpers, subtype decoders.

Pure byte-level code with no I/O, no sessions, no transport dependency.
Everything here operates on :class:`bytes` and returns immutable
dataclasses.

See design doc §4 (protocol-duality seam) and ``docs/protocol.md``.
"""

from __future__ import annotations

from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient
from sartoriuslib.protocol.xbpi.framing import (
    BALANCE_SBN_DEFAULT,
    HOST_SBN_DEFAULT,
    RX_MARKER,
    build_command,
    checksum,
    parse_frame,
)
from sartoriuslib.protocol.xbpi.parser import (
    OFF_SCALE_SENTINEL,
    STABLE_FLAG,
    decode_error_body,
    decode_long_measurement_body,
    decode_measurement_body,
    decode_status_block_body,
    decode_typed_float_body,
    is_status_block_body,
)
from sartoriuslib.protocol.xbpi.tables import (
    ERROR_CODE_REASONS,
    OPCODE_NAMES,
    body_length_for_subtype,
    subtype_family,
)
from sartoriuslib.protocol.xbpi.tlv import (
    TLV_TAG_SIZES,
    decode_tlv,
    encode_tlv,
    parse_tlv_sequence,
    tlv_value_as_int,
)
from sartoriuslib.protocol.xbpi.types import (
    ErrorBody,
    LongMeasurementBody,
    MeasurementBody,
    StatusBlockBody,
    SubtypeFamily,
    TypedFloatBody,
    XbpiFrame,
)
from sartoriuslib.protocol.xbpi.units import (
    SIGN_MASK,
    UNIT_ID_MASK,
    decode_decimals,
    decode_sign,
    decode_unit,
    unit_byte_to_unit,
)

__all__ = [
    "BALANCE_SBN_DEFAULT",
    "ERROR_CODE_REASONS",
    "HOST_SBN_DEFAULT",
    "OFF_SCALE_SENTINEL",
    "OPCODE_NAMES",
    "RX_MARKER",
    "SIGN_MASK",
    "STABLE_FLAG",
    "TLV_TAG_SIZES",
    "UNIT_ID_MASK",
    "ErrorBody",
    "LongMeasurementBody",
    "MeasurementBody",
    "StatusBlockBody",
    "SubtypeFamily",
    "TypedFloatBody",
    "XbpiFrame",
    "XbpiProtocolClient",
    "body_length_for_subtype",
    "build_command",
    "checksum",
    "decode_decimals",
    "decode_error_body",
    "decode_long_measurement_body",
    "decode_measurement_body",
    "decode_sign",
    "decode_status_block_body",
    "decode_tlv",
    "decode_typed_float_body",
    "decode_unit",
    "encode_tlv",
    "is_status_block_body",
    "parse_frame",
    "parse_tlv_sequence",
    "subtype_family",
    "tlv_value_as_int",
    "unit_byte_to_unit",
]
