"""Shared first-batch schema-lock for tabular sinks.

Every tabular sink in the tree (SQLite, and eventually Parquet /
Postgres behind extras) shares the same schema-evolution policy:

1. **First batch wins.** The column set and order are locked from the
   first :meth:`write_many` call. For schema-less sinks this is just
   bookkeeping; for schema-ful sinks (SQLite ``CREATE TABLE``) the
   locked spec drives the backing schema.
2. **Unknown columns are dropped with a one-shot WARN.** Later batches
   carrying a new key don't reshape the file/table silently — each
   new key logs once, then gets dropped on subsequent batches without
   re-logging.
3. **Missing columns are filled with ``None``.** Row projection
   guarantees every locked column appears in the output dict.

This module is sink-facing only. It has no public re-export.

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

__all__ = ["ColumnSpec", "SchemaLock"]


_SCALAR_TYPE = type[float] | type[int] | type[str]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column in a locked tabular schema.

    Attributes:
        name: Column name, verbatim from the source row dict.
        python_type: Concrete Python scalar type backing the column —
            one of :class:`float`, :class:`int`, :class:`str`. Sinks
            translate this into their native type system.
        nullable: ``True`` if the first batch contained at least one
            ``None`` for this column, or if the column is entirely
            absent from some rows.
    """

    name: str
    python_type: _SCALAR_TYPE
    nullable: bool


class SchemaLock:
    """Lock a row-dict schema on first batch; drop unknowns on later batches.

    Not thread-safe. Each sink instance owns one :class:`SchemaLock`
    and guards it with whatever lock protects its write path.

    Typical sink flow::

        self._schema = SchemaLock(sink_name="sqlite", logger=_logger)
        # on first write_many:
        specs = self._schema.lock([sample_to_row(s) for s in samples])
        # for every batch (including the first):
        rows = [self._schema.project(sample_to_row(s)) for s in samples]
    """

    def __init__(self, *, sink_name: str, logger: logging.Logger) -> None:
        self._sink_name = sink_name
        self._logger = logger
        self._columns: tuple[ColumnSpec, ...] | None = None
        self._names: frozenset[str] = frozenset()
        self._unknown_warned: set[str] = set()

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in declaration order, or ``None`` before lock."""
        return self._columns

    @property
    def is_locked(self) -> bool:
        """``True`` once :meth:`lock` or :meth:`lock_to` has been called."""
        return self._columns is not None

    def lock(
        self,
        rows: Sequence[Mapping[str, object]],
    ) -> tuple[ColumnSpec, ...]:
        """Infer column specs from ``rows`` and lock the schema.

        Column order is determined by first-encounter across the batch.
        Per-column type is inferred from the first non-``None`` value;
        when the batch mixes ``int`` and ``float`` for one column the
        column widens to ``float``; any other mix widens to ``str``.

        Columns entirely ``None`` in the first batch default to
        ``str`` / ``nullable=True``.
        """
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock called twice")
        if not rows:
            raise ValueError("SchemaLock.lock requires a non-empty first batch")

        ordered_keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    ordered_keys.append(key)
                    seen.add(key)

        specs = [self._infer_column(key, rows) for key in ordered_keys]
        self._columns = tuple(specs)
        self._names = frozenset(ordered_keys)
        return self._columns

    @staticmethod
    def _infer_column(
        key: str,
        rows: Sequence[Mapping[str, object]],
    ) -> ColumnSpec:
        """Infer one column's spec from the first batch."""
        inferred: type | None = None
        nullable = False
        for row in rows:
            if key not in row:
                nullable = True
                continue
            value = row[key]
            if value is None:
                nullable = True
                continue
            value_type = type(value)
            if inferred is None:
                inferred = value_type
            elif inferred is not value_type:
                inferred = float if {inferred, value_type} <= {int, float} else str
        if inferred is None:
            inferred = str
            nullable = True
        elif inferred not in (float, int, str):
            inferred = str
        return ColumnSpec(name=key, python_type=inferred, nullable=nullable)

    def lock_to(self, specs: Sequence[ColumnSpec]) -> tuple[ColumnSpec, ...]:
        """Lock the schema from an externally-supplied spec list.

        Used by sinks that validate against an already-existing
        backing schema rather than inferring from the first batch.
        """
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock_to called twice")
        if not specs:
            raise ValueError("SchemaLock.lock_to requires at least one column")
        self._columns = tuple(specs)
        self._names = frozenset(spec.name for spec in self._columns)
        return self._columns

    def project(self, row: Mapping[str, object]) -> dict[str, object]:
        """Return a new dict containing only keys from the locked schema.

        Every locked column appears in the output dict — missing keys
        are filled with ``None``. Any key in ``row`` that is not part
        of the locked schema is dropped, with the first occurrence of
        each such key logged at WARN.
        """
        if self._columns is None:
            raise RuntimeError("SchemaLock.project called before lock()")

        result: dict[str, object] = {spec.name: None for spec in self._columns}
        for key, value in row.items():
            if key in self._names:
                result[key] = value
                continue
            if key not in self._unknown_warned:
                self._unknown_warned.add(key)
                self._logger.warning(
                    "sink.unknown_column_dropped",
                    extra={"sink": self._sink_name, "column": key},
                )
        return result
