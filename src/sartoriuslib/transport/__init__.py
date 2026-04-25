"""Transport layer — moves bytes only. No Sartorius command semantics.

See design doc §8.
"""

from __future__ import annotations

from sartoriuslib.transport.base import (
    ByteSize,
    Parity,
    SerialSettings,
    StopBits,
    Transport,
)
from sartoriuslib.transport.fake import FakeTransport, ScriptedReply
from sartoriuslib.transport.serial import SerialTransport

__all__ = [
    "ByteSize",
    "FakeTransport",
    "Parity",
    "ScriptedReply",
    "SerialSettings",
    "SerialTransport",
    "StopBits",
    "Transport",
]
