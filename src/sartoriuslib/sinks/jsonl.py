"""JSONL sink — stdlib :mod:`json`, one object per line, no schema lock.

:class:`JsonlSink` writes one JSON object per :class:`Sample`. Unlike
:class:`~sartoriuslib.sinks.csv.CsvSink`, it doesn't lock a schema —
each row stands alone, so a device whose reading format carries an
extra field simply emits a wider object without affecting earlier or
later rows.

Stdlib-only. Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Self

from sartoriuslib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from io import TextIOWrapper
    from types import TracebackType

    from sartoriuslib.streaming.sample import Sample

__all__ = ["JsonlSink"]


class JsonlSink:
    r"""Append-only JSONL writer — one flattened sample per line.

    The on-disk format is ``<sample-row-as-json>\n`` per sample;
    reading back is just ``[json.loads(line) for line in f]``.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file: TextIOWrapper | None = None

    @property
    def path(self) -> Path:
        """Destination file path."""
        return self._path

    async def open(self) -> None:
        """Open the JSONL file for writing. Overwrites any existing file."""
        if self._file is not None:
            return
        self._file = self._path.open("w", encoding="utf-8", newline="")

    async def write_many(self, samples: Sequence[Sample]) -> None:
        r"""Serialise each sample as one JSON object per line."""
        if self._file is None:
            raise RuntimeError("JsonlSink: write_many called before open()")
        if not samples:
            return
        for sample in samples:
            row = sample_to_row(sample)
            self._file.write(json.dumps(row, ensure_ascii=False))
            self._file.write("\n")
        self._file.flush()

    async def close(self) -> None:
        """Flush and close the JSONL file. Idempotent."""
        if self._file is None:
            return
        try:
            self._file.flush()
        finally:
            self._file.close()
            self._file = None

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
