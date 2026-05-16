"""Streaming + recording primitives. See design doc §10."""

from __future__ import annotations

from sartoriuslib.streaming.poll_source import PollSourceAdapter
from sartoriuslib.streaming.recorder import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    Recording,
    record,
)
from sartoriuslib.streaming.sample import Sample
from sartoriuslib.streaming.stream_session import StreamingSession, StreamMode

__all__ = [
    "AcquisitionSummary",
    "OverflowPolicy",
    "PollSource",
    "PollSourceAdapter",
    "Recording",
    "Sample",
    "StreamMode",
    "StreamingSession",
    "record",
]
