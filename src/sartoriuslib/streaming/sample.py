"""Timed sample — one balance reading with send/receive provenance.

A :class:`Sample` is what the recorder emits into its memory-object
stream. It pairs a :class:`Reading` with enough timing to reconstruct
the acquisition timeline: ``monotonic_ns`` for drift analysis,
``requested_at`` / ``received_at`` / ``midpoint_at`` for wall-clock
provenance, and ``elapsed_s`` for per-sample latency checks.

The midpoint is the best point-estimate of the acquisition instant on
the device: halfway between when the poll bytes left the host and
when the full reply arrived. Downstream plots and correlations should
use this field when aligning balance data against other sensor
streams.

``reading`` is ``None`` when ``error`` is populated — the two fields
are mutually exclusive. Samples with ``error`` still carry the
timing fields so sinks can log the failed attempts with proper
wall-clock provenance.

Design reference: ``docs/design.md`` §10.
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
        requested_at: Wall-clock ``datetime`` (UTC) captured just
            before the poll bytes leave the host.
        received_at: Wall-clock ``datetime`` (UTC) captured just after
            the reply is read.
        midpoint_at: ``(requested_at + received_at) / 2`` — the
            design-preferred point estimate of the sample instant.
        monotonic_ns: :func:`time.monotonic_ns` at the read site. Used
            for scheduling / drift analysis only — never displayed,
            since the absolute value has no calendar meaning.
        elapsed_s: ``(received_at - requested_at).total_seconds()`` —
            precomputed for convenience.
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
    requested_at: datetime
    received_at: datetime
    midpoint_at: datetime
    monotonic_ns: int
    elapsed_s: float
    protocol: ProtocolKind | None
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)
    error: SartoriusError | None = None
