"""Timed sample — one balance reading with send/receive provenance.

A :class:`Sample` is what the recorder emits into its memory-object
stream. It pairs a :class:`Reading` with enough timing to reconstruct
the acquisition timeline. The unified cross-library timestamp contract
(see ``UNIFIED_API_HANDOFF.md`` §C) requires three canonical fields:

- :attr:`t_mono_ns` — monotonic acquisition timestamp (join key).
- :attr:`t_utc` — wall-clock acquisition instant. For request/response
  polling this is the midpoint between :attr:`requested_at` and
  :attr:`received_at` — the best point estimate of when the device
  produced the reading. For SBI autoprint it is :attr:`received_at`.
- :attr:`t_midpoint_mono_ns` — integration-window midpoint (monotonic).
  ``None`` for single polled / autoprint samples; reserved for sensors
  that emit values integrated over a known window.

Per-protocol I/O provenance (``requested_at`` / ``received_at`` /
``latency_s``) is kept alongside so latency analysis and on-the-wire
debugging stay possible. :attr:`metadata` carries free-form annotations
(autoprint vs. poll mode) and is real data, not log spam.

``reading`` is ``None`` when ``error`` is populated — the two fields
are mutually exclusive. Samples with ``error`` still carry the
timing fields so sinks can log the failed attempts with proper
wall-clock provenance.

Design reference: ``docs/design.md`` §10; unified spec
``UNIFIED_API_HANDOFF.md`` §C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from sartoriuslib.devices.models import Reading
    from sartoriuslib.errors import SartoriusError
    from sartoriuslib.protocol.base import ProtocolKind


__all__ = ["Sample"]


def _empty_metadata() -> Mapping[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class Sample:
    """One balance poll with full timing provenance.

    Attributes:
        device: The manager-assigned name (from ``SartoriusManager.add``).
            Stable downstream identifier that follows the value into sinks.
        reading: The :class:`Reading` decoded from the balance's reply.
            ``None`` when the poll failed — inspect :attr:`error`.
        t_mono_ns: Canonical monotonic acquisition timestamp in
            nanoseconds since OS boot. The midpoint of the request /
            response monotonic timestamps for request/response polling;
            the receive-side monotonic for SBI autoprint frames. This is
            the join key downstream tooling correlates against
            sibling-library samples.
        t_utc: Wall-clock acquisition instant (UTC, tz-aware) for the
            same moment :attr:`t_mono_ns` records. For poll: midpoint of
            :attr:`requested_at` and :attr:`received_at`. For autoprint:
            :attr:`received_at`.
        t_midpoint_mono_ns: Optional integration-window midpoint in
            monotonic nanoseconds. ``None`` for single polled or
            autoprint samples (sartorius balances do not expose
            integration semantics); reserved for forward compatibility
            with sensors that do.
        requested_at: Wall-clock ``datetime`` (UTC) captured just
            before the poll bytes leave the host. ``None`` for autoprint
            samples where the host did not send a request.
        received_at: Wall-clock ``datetime`` (UTC) captured just after
            the reply is read.
        latency_s: ``(received_at - requested_at).total_seconds()`` —
            precomputed for convenience. ``0.0`` for autoprint samples.
        protocol: Which wire protocol produced this sample.
            Duplicates ``reading.protocol`` on successful polls and
            preserves the value for error rows where ``reading`` is
            ``None``. ``None`` only when an error-path sample arrives
            from a :class:`PollSource` that did not supply a protocol
            hint — in practice the manager always supplies one.
        metadata: Free-form per-sample annotations. Populated by
            ``stream(mode=...)`` to record which streaming mode produced
            the sample (``"poll"`` or ``"autoprint"``).
        error: The :class:`SartoriusError` captured on a failed poll,
            or ``None`` on success.
    """

    device: str
    reading: Reading | None
    t_mono_ns: int
    t_utc: datetime
    requested_at: datetime
    received_at: datetime
    latency_s: float
    protocol: ProtocolKind | None
    t_midpoint_mono_ns: int | None = None
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)
    error: SartoriusError | None = None
