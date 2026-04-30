"""SQLite sink — stdlib :mod:`sqlite3` + WAL, parameterised ``executemany``.

:class:`SqliteSink` writes one row per :class:`Sample` into a local
SQLite file. Core-sink (no extra required) because ``sqlite3`` ships
with the Python standard library.

The ``sqlite3`` driver is synchronous; the sink calls it through
:func:`anyio.to_thread.run_sync` so the event loop stays responsive.

Best-practice defaults baked in:

- ``journal_mode=WAL`` + ``synchronous=NORMAL``.
- ``busy_timeout=5000`` ms for brief lock contention retries.
- One ``BEGIN IMMEDIATE`` … ``COMMIT`` per ``write_many``.
- SQL identifiers validated against ``^[A-Za-z_][A-Za-z0-9_]{0,62}$``;
  values always parameterised.

Schema evolution mirrors the other tabular sinks: column set locked
on the first batch, unknown columns dropped with a one-shot WARN.

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from anyio.to_thread import run_sync

from sartoriuslib._logging import get_logger
from sartoriuslib.errors import SartoriusSinkSchemaError, SartoriusSinkWriteError
from sartoriuslib.sinks._schema import ColumnSpec, SchemaLock
from sartoriuslib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from sartoriuslib.streaming.sample import Sample

__all__ = ["SqliteSink"]


_logger = get_logger("sinks.sqlite")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_JournalMode = Literal["WAL", "DELETE", "MEMORY", "TRUNCATE", "PERSIST", "OFF"]
_Synchronous = Literal["FULL", "NORMAL", "OFF", "EXTRA"]


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Table names are interpolated into CREATE/INSERT statements, so "
            "they must be safe identifiers."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a SQLite type affinity."""
    if spec.python_type is float:
        return "REAL"
    if spec.python_type in (int, bool):
        return "INTEGER"
    return "TEXT"


class SqliteSink:
    """Append-only SQLite writer with WAL journaling and first-batch schema lock."""

    def __init__(
        self,
        path: str | Path,
        *,
        table: str = "samples",
        create_table: bool = True,
        journal_mode: _JournalMode = "WAL",
        synchronous: _Synchronous = "NORMAL",
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._path = Path(path)
        self._table = _validate_identifier(table, label="table name")
        self._create_table = create_table
        self._journal_mode: _JournalMode = journal_mode
        self._synchronous: _Synchronous = synchronous
        if busy_timeout_ms < 0:
            raise ValueError(f"busy_timeout_ms must be >= 0, got {busy_timeout_ms!r}")
        self._busy_timeout_ms = busy_timeout_ms
        self._conn: sqlite3.Connection | None = None
        self._schema = SchemaLock(sink_name="sqlite", logger=_logger)
        self._insert_sql: str | None = None

    @property
    def path(self) -> Path:
        """Destination SQLite file path."""
        return self._path

    @property
    def table(self) -> str:
        """Target table name (validated)."""
        return self._table

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first :meth:`write_many`."""
        return self._schema.columns

    async def open(self) -> None:
        """Open the SQLite connection, apply PRAGMAs, and introspect the target."""
        if self._conn is not None:
            return
        self._conn = await run_sync(self._connect_blocking)
        _logger.info(
            "sinks.sqlite.open",
            extra={
                "path": str(self._path),
                "table": self._table,
                "journal_mode": self._journal_mode,
                "synchronous": self._synchronous,
            },
        )
        if not self._create_table:
            try:
                await run_sync(self._introspect_existing_table_blocking)
            except BaseException:
                conn = self._conn
                self._conn = None
                await run_sync(conn.close)
                raise

    def _connect_blocking(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self._synchronous}")
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        return conn

    def _introspect_existing_table_blocking(self) -> None:
        assert self._conn is not None  # noqa: S101
        cursor = self._conn.execute(f'PRAGMA table_info("{self._table}")')
        rows = cursor.fetchall()
        if not rows:
            msg = (
                f"SqliteSink: table {self._table!r} does not exist in "
                f"{self._path} and create_table=False. Create the table first "
                "or pass create_table=True."
            )
            raise SartoriusSinkSchemaError(msg)
        specs: list[ColumnSpec] = []
        for _cid, name, decl_type, notnull, _default, _pk in rows:
            upper = (decl_type or "").upper()
            if "BOOL" in upper:
                py_type: type = bool
            elif "INT" in upper:
                py_type = int
            elif any(token in upper for token in ("REAL", "FLOA", "DOUB")):
                py_type = float
            else:
                py_type = str
            specs.append(
                ColumnSpec(name=name, python_type=py_type, nullable=not notnull),
            )
        self._schema.lock_to(specs)
        self._insert_sql = self._build_insert_sql()

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` as rows in a single transaction."""
        if self._conn is None:
            raise RuntimeError("SqliteSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]

        if not self._schema.is_locked and self._create_table:
            self._schema.lock(rows)
            await run_sync(self._create_table_blocking)
            self._insert_sql = self._build_insert_sql()

        assert self._insert_sql is not None  # noqa: S101
        columns = self._schema.columns
        assert columns is not None  # noqa: S101

        projected: list[tuple[object, ...]] = []
        for row in rows:
            fields = self._schema.project(row)
            projected.append(tuple(fields[spec.name] for spec in columns))

        await run_sync(self._executemany_blocking, projected)

    def _build_insert_sql(self) -> str:
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_list = ", ".join(f'"{spec.name}"' for spec in columns)
        placeholders = ", ".join("?" for _ in columns)
        # S608: identifiers validated in __init__; values parameterised.
        return f'INSERT INTO "{self._table}" ({col_list}) VALUES ({placeholders})'  # noqa: S608

    def _create_table_blocking(self) -> None:
        assert self._conn is not None  # noqa: S101
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(f'"{spec.name}" {_column_type(spec)}' for spec in columns)
        stmt = f'CREATE TABLE IF NOT EXISTS "{self._table}" ({col_defs})'
        try:
            self._conn.execute(stmt)
        except sqlite3.Error as exc:
            raise SartoriusSinkWriteError(
                f"SqliteSink: CREATE TABLE failed for {self._table!r}: {exc}",
            ) from exc

    def _executemany_blocking(self, rows: Sequence[tuple[object, ...]]) -> None:
        assert self._conn is not None  # noqa: S101
        assert self._insert_sql is not None  # noqa: S101
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(self._insert_sql, rows)
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                _logger.exception("sinks.sqlite.rollback_failed")
            raise SartoriusSinkWriteError(
                f"SqliteSink: INSERT into {self._table!r} failed: {exc}",
            ) from exc

    async def close(self) -> None:
        """Close the connection. Idempotent."""
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        try:
            await run_sync(conn.close)
        finally:
            _logger.info(
                "sinks.sqlite.close",
                extra={"path": str(self._path), "table": self._table},
            )

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
