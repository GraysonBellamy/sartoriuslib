"""PostgreSQL sink — :mod:`asyncpg`, COPY by default, parameterised fallback.

:class:`PostgresSink` writes one row per :class:`Sample` into a
PostgreSQL table. ``asyncpg`` is an optional dependency behind
``sartoriuslib[postgres]``; the import is deferred to :meth:`open` so
instantiation works on bare-core installs and
:class:`~sartoriuslib.errors.SartoriusSinkDependencyError` is raised only
when the user actually tries to open a connection.

Best-practice defaults baked in:

- **Binary COPY** via :meth:`asyncpg.Connection.copy_records_to_table`.
  COPY is ~5–10× faster than parameterised INSERT for batches and is
  the recommended asyncpg bulk-ingest path. Callers that run on
  managed Postgres without COPY privileges can set
  :attr:`PostgresConfig.use_copy` to ``False`` to fall back to a
  prepared ``executemany``.
- **Connection pool** via :func:`asyncpg.create_pool`. The pool
  lifetime equals the sink lifetime; each batch acquires, writes,
  and releases, so the pool stays available for concurrent work.
- **Identifier validation** on ``schema`` and ``table`` (strict regex).
  Every value passes through ``$N`` placeholders — never
  string-formatted into SQL.
- **Credential scrubbing** — log lines that reference the connection
  use :meth:`PostgresConfig.target`, which composes a
  ``host:port/db.schema.table`` string from the parsed DSN / discrete
  fields and never includes the password. Tests assert the plain
  password never appears in :meth:`target` output.
- **``statement_timeout``** applied as a server setting so a wedged
  query cannot block the acquisition loop forever.

Schema evolution mirrors the other tabular sinks (design §10). The
default ``create_table=False`` reads the target table's columns from
``information_schema.columns`` on open and locks the schema to that
set. Passing ``create_table=True`` switches to first-batch inference
and runs ``CREATE TABLE IF NOT EXISTS`` — convenient for quick runs,
but the user gives up type control (everything text-like becomes
``TEXT`` rather than ``timestamptz`` etc.).

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlparse

from sartoriuslib._logging import get_logger
from sartoriuslib.errors import (
    SartoriusSinkDependencyError,
    SartoriusSinkSchemaError,
    SartoriusSinkWriteError,
)
from sartoriuslib.sinks._schema import ColumnSpec, SchemaLock
from sartoriuslib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from sartoriuslib.streaming.sample import Sample

__all__ = ["PostgresConfig", "PostgresSink"]


_logger = get_logger("sinks.postgres")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Map PostgreSQL ``information_schema.columns.data_type`` values back onto
# Python scalar types. Anything not in this set degrades to ``str`` — the
# existing column is treated as TEXT-equivalent and the sink forwards the
# ISO-formatted string that ``sample_to_row`` produces.
_PG_NUMERIC_FLOAT = frozenset(
    {
        "double precision",
        "real",
        "numeric",
        "decimal",
    },
)
_PG_NUMERIC_INT = frozenset(
    {
        "bigint",
        "integer",
        "smallint",
    },
)


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Schema/table names are interpolated into CREATE/INSERT "
            "statements, so they must be safe identifiers."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a PostgreSQL type literal."""
    if spec.python_type is float:
        return "double precision"
    if spec.python_type is int:
        return "bigint"
    return "text"


@dataclass(frozen=True, slots=True)
class PostgresConfig:
    """Connection + target settings for :class:`PostgresSink`.

    Either ``dsn`` or the discrete ``host``/``user``/``database`` set
    must be provided. Credentials are not logged.

    Attributes:
        dsn: Full libpq-style connection string (e.g.
            ``postgres://user:pass@host:5432/db``). Mutually exclusive
            with the discrete fields.
        host: Database host. Required if ``dsn`` is not set.
        port: Database port. Defaults to ``5432``.
        user: Database role.
        password: Role password. Never logged.
        database: Database name.
        schema: Target schema. Validated against
            ``[A-Za-z_][A-Za-z0-9_]{0,62}``.
        table: Target table. Validated against the same pattern.
        pool_min_size: Minimum pool size. Defaults to ``1``.
        pool_max_size: Maximum pool size. Defaults to ``4``.
        statement_timeout_ms: ``statement_timeout`` applied as a
            server setting. Defaults to 30 s.
        command_timeout_s: asyncpg's per-call command timeout.
            Defaults to 10 s.
        create_table: If ``True``, infer the schema from the first
            batch and run ``CREATE TABLE IF NOT EXISTS``. If
            ``False`` (the safer default), require the table to
            exist and lock the schema from
            ``information_schema.columns``.
        use_copy: If ``True`` (default), bulk-write via asyncpg's
            binary COPY path. Disable only if your environment does
            not grant the COPY privilege to the sink's role, in
            which case writes fall back to prepared ``executemany``.
    """

    dsn: str | None = None
    host: str | None = None
    port: int = 5432
    user: str | None = None
    password: str | None = None
    database: str | None = None
    schema: str = "public"
    table: str = "samples"
    pool_min_size: int = 1
    pool_max_size: int = 4
    statement_timeout_ms: int = 30_000
    command_timeout_s: float = 10.0
    create_table: bool = False
    use_copy: bool = True

    def __post_init__(self) -> None:
        if self.dsn is None and self.host is None:
            msg = (
                "PostgresConfig requires either `dsn` or `host` (and related "
                "discrete fields); both were None."
            )
            raise ValueError(msg)
        if self.dsn is not None and self.host is not None:
            msg = (
                "PostgresConfig: `dsn` and `host` are mutually exclusive — "
                "pick one connection style."
            )
            raise ValueError(msg)
        _validate_identifier(self.schema, label="schema name")
        _validate_identifier(self.table, label="table name")
        if self.pool_min_size < 1 or self.pool_max_size < self.pool_min_size:
            msg = (
                f"PostgresConfig: pool bounds invalid "
                f"(min={self.pool_min_size}, max={self.pool_max_size})."
            )
            raise ValueError(msg)
        if self.statement_timeout_ms < 0:
            raise ValueError(
                f"statement_timeout_ms must be >= 0, got {self.statement_timeout_ms!r}",
            )
        if self.command_timeout_s <= 0:
            raise ValueError(
                f"command_timeout_s must be > 0, got {self.command_timeout_s!r}",
            )

    def target(self) -> str:
        """Return a log-safe description of the target: ``host:port/db.schema.table``."""
        if self.dsn is not None:
            parsed = urlparse(self.dsn)
            host = parsed.hostname or "?"
            port = parsed.port or self.port
            db = (parsed.path or "/?").lstrip("/") or "?"
        else:
            host = self.host or "?"
            port = self.port
            db = self.database or "?"
        return f"{host}:{port}/{db}.{self.schema}.{self.table}"


def _load_asyncpg() -> Any:
    """Lazy-import asyncpg; raise :class:`SartoriusSinkDependencyError` on miss."""
    try:
        # PLC0415: intentional deferred import — see ParquetSink for rationale.
        import asyncpg  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
    except ImportError as exc:
        raise SartoriusSinkDependencyError(
            "PostgresSink requires the `postgres` extra. "
            "Install with: `pip install 'sartoriuslib[postgres]'` "
            "(or `uv add 'sartoriuslib[postgres]'`).",
        ) from exc
    return asyncpg


class PostgresSink:
    """Append-only Postgres writer using pooled asyncpg connections.

    Attributes:
        config: Frozen :class:`PostgresConfig` instance.
        columns: Locked columns in order, or ``None`` before first
            :meth:`write_many`.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._schema = SchemaLock(sink_name="postgres", logger=_logger)
        self._asyncpg: Any = None
        self._pool: Any = None
        self._insert_sql: str | None = None
        self._rows_written = 0

    @property
    def config(self) -> PostgresConfig:
        """The frozen :class:`PostgresConfig` passed in at construction."""
        return self._config

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first :meth:`write_many`."""
        return self._schema.columns

    async def open(self) -> None:
        """Load asyncpg, open the pool, and (optionally) introspect the table.

        Idempotent. When ``create_table=False`` (the default), the
        target's columns are read on open and the schema is locked
        immediately. When ``create_table=True`` the lock happens
        lazily on the first :meth:`write_many`.
        """
        if self._pool is not None:
            return
        self._asyncpg = _load_asyncpg()
        cfg = self._config
        server_settings = {
            "application_name": "sartoriuslib",
            "statement_timeout": str(int(cfg.statement_timeout_ms)),
        }
        try:
            if cfg.dsn is not None:
                self._pool = await self._asyncpg.create_pool(
                    dsn=cfg.dsn,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
            else:
                self._pool = await self._asyncpg.create_pool(
                    host=cfg.host,
                    port=cfg.port,
                    user=cfg.user,
                    password=cfg.password,
                    database=cfg.database,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
        except Exception as exc:
            raise SartoriusSinkWriteError(
                f"PostgresSink: failed to open pool for {cfg.target()}: {exc}",
            ) from exc

        _logger.info(
            "sinks.postgres.open",
            extra={
                "target": cfg.target(),
                "pool_min": cfg.pool_min_size,
                "pool_max": cfg.pool_max_size,
                "use_copy": cfg.use_copy,
                "create_table": cfg.create_table,
            },
        )

        if not cfg.create_table:
            await self._introspect_existing_table()

    async def _introspect_existing_table(self) -> None:
        """Read ``information_schema.columns`` and lock the schema."""
        cfg = self._config
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                cfg.schema,
                cfg.table,
            )
        if not rows:
            msg = (
                f"PostgresSink: table {cfg.schema}.{cfg.table} does not exist "
                f"on {cfg.target()} and create_table=False. Create the table "
                "first, or pass create_table=True on PostgresConfig."
            )
            raise SartoriusSinkSchemaError(msg)
        specs: list[ColumnSpec] = []
        for row in rows:
            data_type = str(row["data_type"]).lower()
            if data_type in _PG_NUMERIC_FLOAT:
                py_type: type = float
            elif data_type in _PG_NUMERIC_INT:
                py_type = int
            else:
                py_type = str
            specs.append(
                ColumnSpec(
                    name=str(row["column_name"]),
                    python_type=py_type,
                    nullable=True,
                ),
            )
        self._schema.lock_to(specs)
        self._insert_sql = self._build_insert_sql()

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` — one COPY (or executemany) per call."""
        if self._pool is None:
            raise RuntimeError("PostgresSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]

        if not self._schema.is_locked:
            assert self._config.create_table  # noqa: S101
            self._schema.lock(rows)
            await self._create_table()
            self._insert_sql = self._build_insert_sql()

        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        assert self._insert_sql is not None  # noqa: S101

        projected_tuples: list[tuple[object, ...]] = []
        for row in rows:
            fields = self._schema.project(row)
            projected_tuples.append(tuple(fields[spec.name] for spec in columns))

        try:
            if self._config.use_copy:
                await self._write_copy(projected_tuples, columns)
            else:
                await self._write_executemany(projected_tuples)
        except SartoriusSinkWriteError:
            raise
        except Exception as exc:
            raise SartoriusSinkWriteError(
                f"PostgresSink: write failed for {self._config.target()}: {exc}",
            ) from exc
        self._rows_written += len(projected_tuples)

    async def _write_copy(
        self,
        records: Sequence[tuple[object, ...]],
        columns: Sequence[ColumnSpec],
    ) -> None:
        """Bulk-insert ``records`` using asyncpg's binary COPY path."""
        cfg = self._config
        async with self._pool.acquire() as conn:
            await conn.copy_records_to_table(
                cfg.table,
                records=list(records),
                columns=[spec.name for spec in columns],
                schema_name=cfg.schema,
                timeout=cfg.command_timeout_s,
            )

    async def _write_executemany(
        self,
        records: Sequence[tuple[object, ...]],
    ) -> None:
        """Insert ``records`` via prepared ``executemany`` (COPY-off fallback)."""
        assert self._insert_sql is not None  # noqa: S101
        async with (
            self._pool.acquire() as conn,
            conn.transaction(),
        ):
            await conn.executemany(self._insert_sql, records)

    def _build_insert_sql(self) -> str:
        """Compose the parameterised INSERT used by the executemany fallback.

        Identifiers (schema, table, column names) are validated or
        library-sourced — never user input. Values go through ``$N``
        placeholders.
        """
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_list = ", ".join(f'"{spec.name}"' for spec in columns)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        cfg = self._config
        # S608: identifiers validated at config construction; values are $N.
        return (
            f'INSERT INTO "{cfg.schema}"."{cfg.table}" '  # noqa: S608
            f"({col_list}) VALUES ({placeholders})"
        )

    async def _create_table(self) -> None:
        """Issue ``CREATE TABLE IF NOT EXISTS`` from the inferred schema."""
        cfg = self._config
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(f'"{spec.name}" {_column_type(spec)}' for spec in columns)
        # Identifiers validated in PostgresConfig.__post_init__.
        stmt = f'CREATE TABLE IF NOT EXISTS "{cfg.schema}"."{cfg.table}" ({col_defs})'
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(stmt)
        except Exception as exc:
            raise SartoriusSinkWriteError(
                f"PostgresSink: CREATE TABLE failed for {cfg.schema}.{cfg.table}: {exc}",
            ) from exc

    async def close(self) -> None:
        """Close the pool. Idempotent."""
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        try:
            await pool.close()
        finally:
            _logger.info(
                "sinks.postgres.close",
                extra={
                    "target": self._config.target(),
                    "rows_written": self._rows_written,
                },
            )
        self._asyncpg = None

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
