"""Sync facade — ``anyio.BlockingPortal`` wrapper over the async core.

Async is canonical; the sync facade wraps it through
:class:`SyncPortal` so scripts, notebooks, and REPL sessions can drive
balances without ``await``.

Surfaces:

* Balance / manager — :class:`Sartorius`, :class:`SyncBalance`,
  :class:`SyncSartoriusManager` (:class:`ErrorPolicy` / :class:`DeviceResult`
  re-exports).
* Recording — :func:`record`, :func:`pipe`, :class:`Recording`,
  :class:`AcquisitionSummary`, :class:`OverflowPolicy`.
* Sinks — :class:`SyncSinkAdapter` +
  :class:`SyncInMemorySink` / :class:`SyncCsvSink` /
  :class:`SyncJsonlSink` / :class:`SyncSqliteSink` /
  :class:`SyncParquetSink` / :class:`SyncPostgresSink`
  (+ :class:`PostgresConfig`).
* Portal primitives — :class:`SyncPortal`, :func:`run_sync`.

See ``docs/design.md`` §9 for the design.
"""

from __future__ import annotations

from sartoriuslib.sync.balance import Sartorius, SyncBalance
from sartoriuslib.sync.manager import (
    DeviceResult,
    ErrorPolicy,
    SyncSartoriusManager,
)
from sartoriuslib.sync.portal import SyncPortal, run_sync
from sartoriuslib.sync.recording import (
    AcquisitionSummary,
    OverflowPolicy,
    Recording,
    pipe,
    record,
)
from sartoriuslib.sync.sinks import (
    PostgresConfig,
    SyncCsvSink,
    SyncInMemorySink,
    SyncJsonlSink,
    SyncParquetSink,
    SyncPostgresSink,
    SyncSinkAdapter,
    SyncSqliteSink,
)

__all__ = [
    "AcquisitionSummary",
    "DeviceResult",
    "ErrorPolicy",
    "OverflowPolicy",
    "PostgresConfig",
    "Recording",
    "Sartorius",
    "SyncBalance",
    "SyncCsvSink",
    "SyncInMemorySink",
    "SyncJsonlSink",
    "SyncParquetSink",
    "SyncPortal",
    "SyncPostgresSink",
    "SyncSartoriusManager",
    "SyncSinkAdapter",
    "SyncSqliteSink",
    "pipe",
    "record",
    "run_sync",
]
