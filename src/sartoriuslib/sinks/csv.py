"""CSV sink — stdlib :mod:`csv`, schema locked at first batch.

:class:`CsvSink` writes one row per :class:`Sample`. The column order
is fixed the first time :meth:`write_many` is called — inferred from
the first sample's :func:`sample_to_row` output — and stays stable
for the rest of the run. Unknown columns that appear in later
samples are dropped with a WARN log rather than silently reshaping
the file.

Stdlib-only — the core install pulls in no CSV dependencies.
Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Self

from sartoriuslib._logging import get_logger
from sartoriuslib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from io import TextIOWrapper
    from types import TracebackType

    from sartoriuslib.streaming.sample import Sample

__all__ = ["CsvSink"]


_logger = get_logger("sinks.csv")


class CsvSink:
    """Append-only CSV writer with first-batch schema lock.

    Attributes:
        path: Destination file. Created or overwritten on :meth:`open`.
        columns: Locked column order after the first :meth:`write_many`.
            ``None`` before the first flush.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file: TextIOWrapper | None = None
        self._writer: csv.DictWriter[str] | None = None
        self._columns: tuple[str, ...] | None = None
        self._unknown_columns_warned: set[str] = set()

    @property
    def path(self) -> Path:
        """Destination file path."""
        return self._path

    @property
    def columns(self) -> tuple[str, ...] | None:
        """Locked column order, or ``None`` if no batch has been flushed."""
        return self._columns

    async def open(self) -> None:
        """Open the CSV file for writing. Overwrites any existing file."""
        if self._file is not None:
            return
        self._file = self._path.open("w", encoding="utf-8", newline="")

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` as CSV rows."""
        if self._file is None:
            raise RuntimeError("CsvSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]
        if self._writer is None:
            self._columns = tuple(rows[0].keys())
            self._writer = csv.DictWriter(self._file, fieldnames=list(self._columns))
            self._writer.writeheader()

        columns = self._columns
        assert columns is not None  # noqa: S101

        for row in rows:
            unknown = row.keys() - set(columns)
            for key in unknown:
                if key not in self._unknown_columns_warned:
                    self._unknown_columns_warned.add(key)
                    _logger.warning(
                        "sinks.csv.unknown_column",
                        extra={
                            "path": str(self._path),
                            "column": key,
                            "action": "drop",
                        },
                    )
            filtered = {k: row.get(k) for k in columns}
            self._writer.writerow(filtered)
        self._file.flush()

    async def close(self) -> None:
        """Flush and close the CSV file. Idempotent."""
        if self._file is None:
            return
        try:
            self._file.flush()
        finally:
            self._file.close()
            self._file = None
            self._writer = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()
