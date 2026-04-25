"""Sync wrappers for :mod:`sartoriuslib.sinks`.

Every in-tree sink has a one-to-one sync counterpart. All of them
share :class:`SyncSinkAdapter`: the per-sink subclass only constructs
the matching async sink with its own parameters and hands it to the
adapter, which owns the portal + open/write/close plumbing.

Sinks follow the same portal-ownership pattern as the rest of the sync
facade — each wrapper creates a throwaway :class:`SyncPortal` on
``__enter__`` unless the caller passes one in. Pass a shared portal
when the sink must share an event loop with a
:class:`SyncSartoriusManager` or :func:`record`, otherwise the sink's
writes run on a different loop than the data producer.

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Literal, Self

from sartoriuslib.sinks import (
    CsvSink,
    InMemorySink,
    JsonlSink,
    ParquetSink,
    PostgresConfig,
    PostgresSink,
    SqliteSink,
)
from sartoriuslib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from types import TracebackType
    from typing import Protocol

    from sartoriuslib.sinks.base import SampleSink
    from sartoriuslib.streaming.sample import Sample

__all__ = [
    "PostgresConfig",
    "SyncCsvSink",
    "SyncInMemorySink",
    "SyncJsonlSink",
    "SyncParquetSink",
    "SyncPostgresSink",
    "SyncSampleSink",
    "SyncSinkAdapter",
    "SyncSqliteSink",
]


if TYPE_CHECKING:

    class SyncSampleSink(Protocol):
        """Sync shape of an acquisition sink.

        Mirrors :class:`~sartoriuslib.sinks.base.SampleSink` — same method
        names, no ``await``. Every concrete wrapper in this module
        satisfies this Protocol.
        """

        def open(self) -> None:
            """Allocate the sink's backing resource."""
            ...

        def write_many(self, samples: Sequence[Sample]) -> None:
            """Append ``samples`` to the sink."""
            ...

        def close(self) -> None:
            """Flush and release the backing resource — idempotent."""
            ...

        def __enter__(self) -> Self:
            """Open the sink and return self."""
            ...

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            """Close the sink on exit."""
            ...


# Sqlite / Parquet literal aliases mirror the per-file definitions in the
# async sinks. Duplicated rather than imported because the async aliases
# are module-private.

_JournalMode = Literal["WAL", "DELETE", "MEMORY", "TRUNCATE", "PERSIST", "OFF"]
_Synchronous = Literal["FULL", "NORMAL", "OFF", "EXTRA"]
_Compression = Literal["zstd", "snappy", "gzip", "brotli", "lz4", "none"]


class SyncSinkAdapter:
    """Generic sync wrapper around any :class:`SampleSink`.

    Subclasses typically only override :meth:`__init__` to build the
    matching async sink with sink-specific parameters and hand it to
    this base class.
    """

    def __init__(
        self,
        async_sink: SampleSink,
        *,
        portal: SyncPortal | None = None,
    ) -> None:
        self._async_sink = async_sink
        self._portal_override = portal
        self._portal: SyncPortal | None = None
        self._stack: ExitStack | None = None
        self._entered = False

    @property
    def async_sink(self) -> SampleSink:
        """The wrapped async :class:`SampleSink` — escape hatch."""
        return self._async_sink

    @property
    def portal(self) -> SyncPortal:
        """Active :class:`SyncPortal` (raises if outside ``with`` block)."""
        portal = self._portal
        if portal is None:
            raise RuntimeError("SyncSinkAdapter is not open")
        return portal

    def open(self) -> None:
        """Blocking :meth:`SampleSink.open`."""
        self.portal.call(self._async_sink.open)

    def write_many(self, samples: Sequence[Sample]) -> None:
        """Blocking :meth:`SampleSink.write_many`."""
        self.portal.call(self._async_sink.write_many, samples)

    def close(self) -> None:
        """Blocking :meth:`SampleSink.close` — idempotent."""
        portal = self._portal
        if portal is None:
            return
        portal.call(self._async_sink.close)

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("SyncSinkAdapter is not reusable after exit")
        self._entered = True
        stack = ExitStack()
        try:
            self._portal = (
                self._portal_override
                if self._portal_override is not None
                else stack.enter_context(SyncPortal())
            )
            self.open()
            self._stack = stack
        except BaseException:
            stack.close()
            self._portal = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        try:
            self.close()
        finally:
            self._portal = None
            if stack is not None:
                stack.__exit__(exc_type, exc, tb)


class SyncInMemorySink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.memory.InMemorySink`."""

    def __init__(self, *, portal: SyncPortal | None = None) -> None:
        super().__init__(InMemorySink(), portal=portal)

    @property
    def samples(self) -> list[Sample]:
        """Captured samples — proxy for :attr:`InMemorySink.samples`."""
        inner: InMemorySink = self._async_sink  # type: ignore[assignment]
        return inner.samples


class SyncCsvSink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.csv.CsvSink`."""

    def __init__(self, path: str | Path, *, portal: SyncPortal | None = None) -> None:
        super().__init__(CsvSink(path), portal=portal)


class SyncJsonlSink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.jsonl.JsonlSink`."""

    def __init__(self, path: str | Path, *, portal: SyncPortal | None = None) -> None:
        super().__init__(JsonlSink(path), portal=portal)


class SyncSqliteSink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.sqlite.SqliteSink`."""

    def __init__(
        self,
        path: str | Path,
        *,
        table: str = "samples",
        create_table: bool = True,
        journal_mode: _JournalMode = "WAL",
        synchronous: _Synchronous = "NORMAL",
        busy_timeout_ms: int = 5000,
        portal: SyncPortal | None = None,
    ) -> None:
        super().__init__(
            SqliteSink(
                path,
                table=table,
                create_table=create_table,
                journal_mode=journal_mode,
                synchronous=synchronous,
                busy_timeout_ms=busy_timeout_ms,
            ),
            portal=portal,
        )


class SyncParquetSink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.parquet.ParquetSink`.

    Requires the ``sartoriuslib[parquet]`` extra — the dependency check
    runs on :meth:`open`, same as the async sink.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        compression: _Compression = "zstd",
        use_dictionary: bool = True,
        row_group_size: int | None = None,
        portal: SyncPortal | None = None,
    ) -> None:
        super().__init__(
            ParquetSink(
                path,
                compression=compression,
                use_dictionary=use_dictionary,
                row_group_size=row_group_size,
            ),
            portal=portal,
        )


class SyncPostgresSink(SyncSinkAdapter):
    """Sync wrapper over :class:`~sartoriuslib.sinks.postgres.PostgresSink`.

    Requires the ``sartoriuslib[postgres]`` extra — dependency check runs
    on :meth:`open`.
    """

    def __init__(
        self,
        config: PostgresConfig,
        *,
        portal: SyncPortal | None = None,
    ) -> None:
        super().__init__(PostgresSink(config), portal=portal)
