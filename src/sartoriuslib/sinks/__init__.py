"""Sample sinks — stdlib-backed (core) plus optional Parquet & Postgres.

Public surface:

- :class:`SampleSink` — the Protocol every sink satisfies.
- :func:`pipe` — drains a recorder stream into a sink with buffered flushes.
- :class:`InMemorySink` — test-only; collects samples in a list.
- :class:`CsvSink` — stdlib-backed CSV; schema locked at first batch.
- :class:`JsonlSink` — stdlib-backed JSONL; one object per line.
- :class:`SqliteSink` — stdlib-backed SQLite (WAL, parameterised inserts).
- :class:`ParquetSink` — pyarrow-backed; requires ``sartoriuslib[parquet]``.
- :class:`PostgresSink` + :class:`PostgresConfig` — asyncpg-backed; requires
  ``sartoriuslib[postgres]``.

The optional sinks (:class:`ParquetSink`, :class:`PostgresSink`) import
their backing drivers lazily inside :meth:`open`. That means
instantiation succeeds without the extra installed — calling
:meth:`open` on an un-provisioned install raises
:class:`~sartoriuslib.errors.SartoriusSinkDependencyError` with a
copy-paste install hint.

See ``docs/design.md`` §10.
"""

from __future__ import annotations

from sartoriuslib.sinks.base import SampleSink, pipe, sample_to_row
from sartoriuslib.sinks.csv import CsvSink
from sartoriuslib.sinks.jsonl import JsonlSink
from sartoriuslib.sinks.memory import InMemorySink
from sartoriuslib.sinks.parquet import ParquetSink
from sartoriuslib.sinks.postgres import PostgresConfig, PostgresSink
from sartoriuslib.sinks.sqlite import SqliteSink

__all__ = [
    "CsvSink",
    "InMemorySink",
    "JsonlSink",
    "ParquetSink",
    "PostgresConfig",
    "PostgresSink",
    "SampleSink",
    "SqliteSink",
    "pipe",
    "sample_to_row",
]
