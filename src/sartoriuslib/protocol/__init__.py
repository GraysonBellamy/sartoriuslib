"""Protocol layer — framing, parsing, and protocol-client adapters.

xBPI and SBI each have a full subpackage under here; the shared
:class:`ProtocolClient` protocol and :class:`ProtocolKind` live at this
level. See design doc §2 layer map and §4.
"""

from __future__ import annotations

from sartoriuslib.protocol.base import ProtocolClient, ProtocolKind
from sartoriuslib.protocol.client import make_protocol_client
from sartoriuslib.protocol.detect import DetectionResult, detect_protocol

__all__ = [
    "DetectionResult",
    "ProtocolClient",
    "ProtocolKind",
    "detect_protocol",
    "make_protocol_client",
]
