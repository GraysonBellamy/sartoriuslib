"""Tests for :class:`sartoriuslib.sinks.PostgresSink` and :class:`PostgresConfig`.

Focus (design §10):

- :class:`PostgresConfig` validation: DSN-vs-kwargs exclusivity, identifier
  regex, pool bounds, timeouts, credential scrubbing via
  :meth:`PostgresConfig.target`.
- Lazy asyncpg import; :meth:`PostgresSink.open` raises
  :class:`SartoriusSinkDependencyError` when asyncpg is absent.
- COPY path passes explicit ``columns=`` (validated) and record
  tuples — never string-formats values.
- Executemany fallback builds parameterised ``$N`` SQL — an
  injection-shaped value must pass through as a parameter, not as
  inline SQL.
- Introspection path reads ``information_schema.columns``, locks the
  schema, and raises :class:`SartoriusSinkSchemaError` on missing table.
- ``create_table=True`` emits ``CREATE TABLE IF NOT EXISTS``.
- Plain passwords never appear in log records or in
  :meth:`PostgresConfig.target`.

All tests use an in-process asyncpg substitute — no real DB required.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from sartoriuslib.errors import (
    SartoriusConfigurationError,
    SartoriusSinkDependencyError,
    SartoriusSinkSchemaError,
)
from sartoriuslib.sinks import PostgresConfig, PostgresSink
from tests.unit._sink_fixtures import make_sample


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fake asyncpg — records calls and plays back scripted fetch responses.
# ---------------------------------------------------------------------------


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.copied: list[dict[str, Any]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.fetch_script: list[tuple[str, list[dict[str, object]]]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def executemany(
        self,
        sql: str,
        records: list[tuple[object, ...]],
    ) -> None:
        self.executemany_calls.append((sql, list(records)))

    async def copy_records_to_table(
        self,
        table: str,
        *,
        records: list[tuple[object, ...]],
        columns: list[str],
        schema_name: str,
        timeout: float,
    ) -> None:
        self.copied.append(
            {
                "table": table,
                "records": list(records),
                "columns": list(columns),
                "schema_name": schema_name,
                "timeout": timeout,
            },
        )

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        del args
        for needle, rows in self.fetch_script:
            if needle in sql:
                return rows
        return []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn
        self.closed = False

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)

    async def close(self) -> None:
        self.closed = True


class _Acquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeAsyncpg:
    def __init__(self) -> None:
        self.conn = _FakeConnection()
        self.pool: _FakePool | None = None
        self.last_create_pool_kwargs: dict[str, object] | None = None

    async def create_pool(self, **kwargs: object) -> _FakePool:
        self.last_create_pool_kwargs = kwargs
        self.pool = _FakePool(self.conn)
        return self.pool


@pytest.fixture
def fake_asyncpg(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncpg:
    """Install a fake ``asyncpg`` module; return the handle for assertions."""
    fake = _FakeAsyncpg()
    monkeypatch.setitem(sys.modules, "asyncpg", fake)
    return fake


# ---------------------------------------------------------------------------
# PostgresConfig validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_requires_dsn_or_host(self) -> None:
        with pytest.raises(ValueError, match=r"dsn.*host"):
            PostgresConfig()

    def test_rejects_both_dsn_and_host(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            PostgresConfig(dsn="postgres://x/db", host="localhost")

    @pytest.mark.parametrize(
        "bad",
        [";DROP TABLE x", "1bad", "with space", 'sch"ema', "a" * 80],
    )
    def test_rejects_bad_schema_identifier(self, bad: str) -> None:
        with pytest.raises(ValueError, match="schema name"):
            PostgresConfig(host="h", database="d", schema=bad)

    @pytest.mark.parametrize(
        "bad",
        [";DROP TABLE x", "1bad", "with space", 'ta"ble', "a" * 80],
    )
    def test_rejects_bad_table_identifier(self, bad: str) -> None:
        with pytest.raises(ValueError, match="table name"):
            PostgresConfig(host="h", database="d", table=bad)

    def test_rejects_bad_pool_bounds(self) -> None:
        with pytest.raises(ValueError, match="pool bounds"):
            PostgresConfig(host="h", database="d", pool_min_size=0)
        with pytest.raises(ValueError, match="pool bounds"):
            PostgresConfig(host="h", database="d", pool_min_size=5, pool_max_size=2)

    def test_rejects_negative_statement_timeout(self) -> None:
        with pytest.raises(ValueError, match="statement_timeout_ms"):
            PostgresConfig(host="h", database="d", statement_timeout_ms=-1)

    def test_rejects_non_positive_command_timeout(self) -> None:
        with pytest.raises(ValueError, match="command_timeout_s"):
            PostgresConfig(host="h", database="d", command_timeout_s=0)


# ---------------------------------------------------------------------------
# Credential scrubbing
# ---------------------------------------------------------------------------


class TestCredentialScrubbing:
    def test_target_does_not_leak_password_dsn_form(self) -> None:
        cfg = PostgresConfig(
            dsn="postgres://alice:top-secret@db:5432/prod",
            table="samples",
        )
        target = cfg.target()
        assert "top-secret" not in target
        assert target == "db:5432/prod.public.samples"

    def test_target_does_not_leak_password_kwargs_form(self) -> None:
        cfg = PostgresConfig(
            host="db",
            port=5433,
            user="u",
            password="top-secret",
            database="prod",
        )
        target = cfg.target()
        assert "top-secret" not in target
        assert target == "db:5433/prod.public.samples"


# ---------------------------------------------------------------------------
# Lazy import / missing extra
# ---------------------------------------------------------------------------


class TestMissingExtra:
    async def test_open_raises_when_asyncpg_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(sys.modules, "asyncpg", None)
        sink = PostgresSink(PostgresConfig(host="h", database="d"))
        with pytest.raises(SartoriusSinkDependencyError) as excinfo:
            await sink.open()
        assert "sartoriuslib[postgres]" in str(excinfo.value)
        assert isinstance(excinfo.value, SartoriusConfigurationError)


# ---------------------------------------------------------------------------
# Create-table path
# ---------------------------------------------------------------------------


class TestCreateTable:
    async def test_create_table_true_emits_create_ddl(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        cfg = PostgresConfig(
            host="h",
            database="d",
            create_table=True,
            use_copy=False,
        )
        async with PostgresSink(cfg) as sink:
            await sink.write_many([make_sample(value=1.0)])

        ddl = [
            stmt
            for stmt, _ in fake_asyncpg.conn.executed
            if stmt.startswith("CREATE TABLE IF NOT EXISTS")
        ]
        assert ddl, "no CREATE TABLE IF NOT EXISTS found in executed SQL"
        assert '"public"."samples"' in ddl[0]
        assert '"value" double precision' in ddl[0]
        assert '"device" text' in ddl[0]

    async def test_create_table_false_introspects_existing(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        fake_asyncpg.conn.fetch_script.append(
            (
                "information_schema.columns",
                [
                    {"column_name": "device", "data_type": "text"},
                    {"column_name": "value", "data_type": "double precision"},
                    {"column_name": "sequence", "data_type": "bigint"},
                ],
            ),
        )
        cfg = PostgresConfig(host="h", database="d", create_table=False)
        async with PostgresSink(cfg) as sink:
            assert sink.columns is not None
            names = [c.name for c in sink.columns]
            assert names == ["device", "value", "sequence"]
            types = [c.python_type for c in sink.columns]
            assert types == [str, float, int]

    async def test_create_table_false_missing_table_raises(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        # No fetch_script entry -> fetch returns [], sink raises.
        cfg = PostgresConfig(host="h", database="d", create_table=False)
        sink = PostgresSink(cfg)
        with pytest.raises(SartoriusSinkSchemaError, match="does not exist"):
            await sink.open()
        assert fake_asyncpg.last_create_pool_kwargs is not None


# ---------------------------------------------------------------------------
# COPY path
# ---------------------------------------------------------------------------


class TestCopyPath:
    async def test_copy_called_with_validated_identifiers(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        cfg = PostgresConfig(
            host="h",
            database="d",
            schema="analytics",
            table="balances",
            create_table=True,
            use_copy=True,
        )
        async with PostgresSink(cfg) as sink:
            await sink.write_many(
                [make_sample(value=1.0), make_sample(value=2.0)],
            )

        assert len(fake_asyncpg.conn.copied) == 1
        call = fake_asyncpg.conn.copied[0]
        assert call["table"] == "balances"
        assert call["schema_name"] == "analytics"
        # columns passed explicitly — never interpolated into SQL
        assert "device" in call["columns"]
        assert "value" in call["columns"]
        # records arrived as tuples (positional, matching columns order)
        assert len(call["records"]) == 2
        assert all(isinstance(r, tuple) for r in call["records"])

    async def test_injection_value_passes_through_as_data(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        """A SQL-shaped value must reach COPY as a tuple field, not SQL text."""
        attack = "'; DROP TABLE samples; --"
        cfg = PostgresConfig(host="h", database="d", create_table=True)
        async with PostgresSink(cfg) as sink:
            await sink.write_many([make_sample(device=attack, value=1.0)])

        call = fake_asyncpg.conn.copied[0]
        flat = [v for record in call["records"] for v in record]
        assert attack in flat


# ---------------------------------------------------------------------------
# Executemany fallback
# ---------------------------------------------------------------------------


class TestExecuteManyFallback:
    async def test_uses_dollar_n_placeholders(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        cfg = PostgresConfig(
            host="h",
            database="d",
            create_table=True,
            use_copy=False,
        )
        async with PostgresSink(cfg) as sink:
            await sink.write_many([make_sample(value=1.0)])

        assert len(fake_asyncpg.conn.executemany_calls) == 1
        sql, records = fake_asyncpg.conn.executemany_calls[0]
        assert 'INSERT INTO "public"."samples"' in sql
        # $N placeholders present; no f-string leak of values
        assert "$1" in sql
        assert "1.0" not in sql  # value must NOT be inlined
        assert len(records) == 1

    async def test_injection_value_passes_through_as_parameter(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        attack = "'; DROP TABLE samples; --"
        cfg = PostgresConfig(
            host="h",
            database="d",
            create_table=True,
            use_copy=False,
        )
        async with PostgresSink(cfg) as sink:
            await sink.write_many([make_sample(device=attack, value=1.0)])

        sql, records = fake_asyncpg.conn.executemany_calls[0]
        assert attack not in sql  # never gets inlined
        flat = [v for record in records for v in record]
        assert attack in flat


# ---------------------------------------------------------------------------
# Lifecycle / pool
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_write_before_open_raises(self) -> None:
        sink = PostgresSink(PostgresConfig(host="h", database="d"))
        with pytest.raises(RuntimeError, match="write_many called before open"):
            await sink.write_many([make_sample()])

    async def test_close_without_open_is_noop(self) -> None:
        sink = PostgresSink(PostgresConfig(host="h", database="d"))
        await sink.close()

    async def test_open_is_idempotent(self, fake_asyncpg: _FakeAsyncpg) -> None:
        async with PostgresSink(
            PostgresConfig(host="h", database="d", create_table=True),
        ) as sink:
            await sink.open()
        assert fake_asyncpg.pool is not None
        assert fake_asyncpg.pool.closed is True

    async def test_empty_batch_is_noop(self, fake_asyncpg: _FakeAsyncpg) -> None:
        async with PostgresSink(
            PostgresConfig(host="h", database="d", create_table=True),
        ) as sink:
            await sink.write_many([])
        assert fake_asyncpg.conn.copied == []
        assert fake_asyncpg.conn.executemany_calls == []

    async def test_server_settings_include_statement_timeout(
        self,
        fake_asyncpg: _FakeAsyncpg,
    ) -> None:
        cfg = PostgresConfig(
            host="h",
            database="d",
            statement_timeout_ms=5_000,
            create_table=True,
        )
        async with PostgresSink(cfg):
            pass
        kwargs = fake_asyncpg.last_create_pool_kwargs
        assert kwargs is not None
        settings = kwargs["server_settings"]
        assert isinstance(settings, dict)
        assert settings["statement_timeout"] == "5000"
        assert settings["application_name"] == "sartoriuslib"


# ---------------------------------------------------------------------------
# Logging safety
# ---------------------------------------------------------------------------


class TestLoggingSafety:
    async def test_password_never_in_log_records_dsn_form(
        self,
        fake_asyncpg: _FakeAsyncpg,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = PostgresConfig(
            dsn="postgres://alice:top-secret-pw@db:5432/prod",
            create_table=True,
        )
        with caplog.at_level(logging.DEBUG, logger="sartoriuslib.sinks.postgres"):
            async with PostgresSink(cfg) as sink:
                await sink.write_many([make_sample(value=1.0)])
        serialised = "\n".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
        assert "top-secret-pw" not in serialised

    async def test_password_never_in_log_records_kwargs_form(
        self,
        fake_asyncpg: _FakeAsyncpg,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = PostgresConfig(
            host="db",
            user="alice",
            password="top-secret-pw",
            database="prod",
            create_table=True,
        )
        with caplog.at_level(logging.DEBUG, logger="sartoriuslib.sinks.postgres"):
            async with PostgresSink(cfg) as sink:
                await sink.write_many([make_sample(value=1.0)])
        serialised = "\n".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
        assert "top-secret-pw" not in serialised
