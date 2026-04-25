"""Tests for :class:`sartoriuslib.sinks.ParquetSink`.

Focus (design §10):

- Round-trip via :func:`pyarrow.parquet.read_table`.
- zstd is the default codec; caller-supplied alternatives are respected.
- Schema locked on first batch.
- One row group per ``write_many`` by default; ``row_group_size`` overrides.
- Missing ``pyarrow`` (the ``parquet`` extra) surfaces as
  :class:`SartoriusSinkDependencyError` on :meth:`open`, not at import time.

Skipped on bare-core installs via ``pytest.importorskip`` — the sink's
dependency-missing error path has its own test that monkey-patches
``sys.modules`` so it runs even when pyarrow is installed.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from sartoriuslib.errors import (
    SartoriusConfigurationError,
    SartoriusSinkDependencyError,
)
from sartoriuslib.sinks import ParquetSink
from tests.unit._sink_fixtures import make_sample

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from pathlib import Path

    from sartoriuslib.streaming.sample import Sample

pyarrow = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


pytestmark = pytest.mark.anyio


class TestConstruction:
    def test_rejects_invalid_row_group_size(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="row_group_size"):
            ParquetSink(tmp_path / "x.parquet", row_group_size=0)
        with pytest.raises(ValueError, match="row_group_size"):
            ParquetSink(tmp_path / "x.parquet", row_group_size=-5)

    def test_exposes_path_and_compression(self, tmp_path: Path) -> None:
        sink = ParquetSink(tmp_path / "x.parquet", compression="snappy")
        assert sink.path == tmp_path / "x.parquet"
        assert sink.compression == "snappy"
        assert sink.columns is None


class TestLifecycle:
    async def test_context_manager_open_close(self, tmp_path: Path) -> None:
        async with ParquetSink(tmp_path / "run.parquet"):
            pass

    async def test_close_without_open_is_noop(self, tmp_path: Path) -> None:
        sink = ParquetSink(tmp_path / "never.parquet")
        await sink.close()

    async def test_open_is_idempotent(self, tmp_path: Path) -> None:
        async with ParquetSink(tmp_path / "run.parquet") as sink:
            await sink.open()

    async def test_write_before_open_raises(self, tmp_path: Path) -> None:
        sink = ParquetSink(tmp_path / "run.parquet")
        with pytest.raises(RuntimeError, match="write_many called before open"):
            await sink.write_many([make_sample()])

    async def test_empty_batch_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([])
        # No file should have been created (writer never opened)
        assert not path.exists()


class TestRoundTrip:
    async def test_round_trip_via_read_table(self, tmp_path: Path) -> None:
        path = tmp_path / "run.parquet"
        samples = [make_sample("b1", value=v) for v in (1.0, 2.0, 3.0)]
        async with ParquetSink(path) as sink:
            await sink.write_many(samples[:2])
            await sink.write_many(samples[2:])

        table = pq.read_table(path)
        assert table.num_rows == 3
        assert table.column("value").to_pylist() == [1.0, 2.0, 3.0]
        assert table.column("device").to_pylist() == ["b1", "b1", "b1"]
        assert table.column("unit").to_pylist() == ["g", "g", "g"]
        assert table.column("protocol").to_pylist() == ["xbpi", "xbpi", "xbpi"]

    async def test_schema_has_correct_pyarrow_types(self, tmp_path: Path) -> None:
        path = tmp_path / "run.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample(value=1.0)])

        table = pq.read_table(path)
        by_name = {f.name: f.type for f in table.schema}
        assert str(by_name["value"]) == "double"
        assert str(by_name["elapsed_s"]) == "double"
        assert str(by_name["device"]) == "string"
        assert str(by_name["unit"]) == "string"
        assert str(by_name["protocol"]) == "string"

    async def test_all_fields_are_nullable(self, tmp_path: Path) -> None:
        path = tmp_path / "run.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample(value=1.0)])
        table = pq.read_table(path)
        assert all(field.nullable for field in table.schema)

    async def test_error_row_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "err.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample("b1", error=True)])
        table = pq.read_table(path)
        assert table.column("value").to_pylist() == [None]
        assert table.column("error_type").to_pylist() == [
            "sartoriuslib.errors.SartoriusTimeoutError",
        ]


class TestCompression:
    async def test_zstd_by_default(self, tmp_path: Path) -> None:
        path = tmp_path / "zstd.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample(value=1.0)])
        meta = pq.ParquetFile(path).metadata
        assert meta.row_group(0).column(0).compression == "ZSTD"

    async def test_snappy_respected(self, tmp_path: Path) -> None:
        path = tmp_path / "snappy.parquet"
        async with ParquetSink(path, compression="snappy") as sink:
            await sink.write_many([make_sample(value=1.0)])
        meta = pq.ParquetFile(path).metadata
        assert meta.row_group(0).column(0).compression == "SNAPPY"


class TestRowGroups:
    async def test_one_row_group_per_write_many(self, tmp_path: Path) -> None:
        path = tmp_path / "rg.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample(value=1.0), make_sample(value=2.0)])
            await sink.write_many([make_sample(value=3.0)])
            await sink.write_many([make_sample(value=4.0), make_sample(value=5.0)])
        assert pq.ParquetFile(path).num_row_groups == 3


class TestSchemaLock:
    async def test_columns_locked_after_first_batch(self, tmp_path: Path) -> None:
        path = tmp_path / "lock.parquet"
        async with ParquetSink(path) as sink:
            await sink.write_many([make_sample(value=1.0)])
            locked = sink.columns
            await sink.write_many([make_sample(value=2.0)])
        assert locked is not None
        assert {c.name for c in locked} >= {
            "device",
            "value",
            "unit",
            "elapsed_s",
            "protocol",
        }


class TestMissingExtra:
    async def test_raises_when_pyarrow_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify the lazy-import error surfaces on :meth:`open`, not import."""
        monkeypatch.setitem(sys.modules, "pyarrow", None)
        monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
        sink = ParquetSink(tmp_path / "x.parquet")  # construction still OK
        with pytest.raises(SartoriusSinkDependencyError) as excinfo:
            await sink.open()
        assert "sartoriuslib[parquet]" in str(excinfo.value)
        assert isinstance(excinfo.value, SartoriusConfigurationError)


class TestPipeIntegration:
    async def test_pipe_end_to_end(self, tmp_path: Path) -> None:
        from sartoriuslib.sinks import pipe

        samples = [make_sample("b1", value=float(i)) for i in range(4)]

        async def _stream() -> AsyncIterator[Mapping[str, Sample]]:
            for s in samples:
                yield {"b1": s}

        path = tmp_path / "piped.parquet"
        async with ParquetSink(path) as sink:
            summary = await pipe(_stream(), sink, batch_size=2, flush_interval=1.0)

        assert summary.samples_emitted == 4
        assert pq.read_table(path).num_rows == 4
