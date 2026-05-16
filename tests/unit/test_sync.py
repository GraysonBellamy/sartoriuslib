"""Tests for :mod:`sartoriuslib.sync`.

Covers:

- :class:`SyncPortal` lifecycle + single-member ExceptionGroup unwrap.
- :class:`Sartorius.open` wraps the async factory.
- :class:`SyncSartoriusManager` mirrors :class:`SartoriusManager`.
- :class:`SyncBalance` exposes every :class:`Balance` method with
  matching signatures (parity test).
- Sync sinks + sync ``record`` + sync ``pipe`` smoke test.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sartoriuslib import Balance, Reading
from sartoriuslib.sync import (
    Sartorius,
    SyncBalance,
    SyncCsvSink,
    SyncInMemorySink,
    SyncPortal,
    SyncSartoriusManager,
    pipe,
    record,
    run_sync,
)
from sartoriuslib.testing import FakeTransport, build_identify_script, canned_frames


def _poll_script() -> dict[bytes, bytes]:
    script = build_identify_script()
    script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
    return script


# ---------------------------------------------------------------------------
# SyncPortal primitives.
# ---------------------------------------------------------------------------


class TestSyncPortal:
    def test_call_runs_coroutine(self) -> None:
        async def _answer() -> int:
            return 42

        with SyncPortal() as portal:
            assert portal.call(_answer) == 42

    def test_call_outside_context_raises(self) -> None:
        portal = SyncPortal()
        with pytest.raises(RuntimeError, match="not running"):
            portal.call(lambda: None)  # type: ignore[arg-type, return-value]

    def test_not_reusable_after_exit(self) -> None:
        portal = SyncPortal()
        with portal:
            pass
        with pytest.raises(RuntimeError, match="not reusable"):
            portal.__enter__()

    def test_run_sync_helper(self) -> None:
        async def _answer() -> int:
            return 7

        assert run_sync(_answer) == 7

    def test_exception_group_unwrap(self) -> None:
        async def _raise() -> None:
            raise ExceptionGroup("x", [ValueError("single")])

        with (
            SyncPortal() as portal,
            pytest.raises(ValueError, match="single"),
        ):
            portal.call(_raise)


# ---------------------------------------------------------------------------
# Sartorius.open.
# ---------------------------------------------------------------------------


class TestSartoriusOpen:
    def test_open_and_poll(self) -> None:
        transport = FakeTransport(_poll_script())
        with Sartorius.open(transport, timeout=0.1) as bal:
            assert isinstance(bal, SyncBalance)
            reading = bal.poll()
            assert isinstance(reading, Reading)
            assert bal.info is not None

    def test_shared_portal(self) -> None:
        transport = FakeTransport(_poll_script())
        with (
            SyncPortal() as portal,
            Sartorius.open(transport, timeout=0.1, portal=portal) as bal,
        ):
            assert bal.portal is portal


# ---------------------------------------------------------------------------
# SyncSartoriusManager.
# ---------------------------------------------------------------------------


class TestSyncManager:
    def test_add_poll_remove(self) -> None:
        transport = FakeTransport(_poll_script())
        with SyncSartoriusManager() as mgr:
            wrapped = mgr.add("b1", transport, timeout=0.1)
            assert isinstance(wrapped, SyncBalance)
            assert mgr.names == ("b1",)
            results = mgr.poll()
            assert results["b1"].ok
            mgr.remove("b1")
            assert len(mgr.names) == 0

    def test_not_reusable_after_exit(self) -> None:
        mgr = SyncSartoriusManager()
        with mgr:
            pass
        with pytest.raises(RuntimeError, match="not reusable"):
            mgr.__enter__()


# ---------------------------------------------------------------------------
# Parity test — SyncBalance matches Balance method signatures.
# ---------------------------------------------------------------------------


_SKIP_ASYNC_ONLY = {
    # Async-only (CM / low-level) methods that don't get a sync equivalent
    # by design — lifecycle is handled via ``with Sartorius.open(...)``.
    "__aenter__",
    "__aexit__",
    "close",
    # Internal helpers — leading underscore, not public.
}


def _public_async_methods(cls: type) -> dict[str, inspect.Signature]:
    result: dict[str, inspect.Signature] = {}
    for name, member in inspect.getmembers(cls):
        if name.startswith("_") or name in _SKIP_ASYNC_ONLY:
            continue
        if not callable(member):
            continue
        if not inspect.iscoroutinefunction(member):
            continue
        result[name] = inspect.signature(member)
    return result


def _public_sync_methods(cls: type) -> dict[str, inspect.Signature]:
    result: dict[str, inspect.Signature] = {}
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if not callable(member):
            continue
        if inspect.iscoroutinefunction(member):
            continue
        # Exclude properties by checking for method-ness.
        if isinstance(member, property):  # pragma: no cover
            continue
        result[name] = inspect.signature(member)
    return result


class TestSyncBalanceParity:
    def test_every_async_method_has_sync_counterpart(self) -> None:
        async_methods = _public_async_methods(Balance)
        sync_methods = _public_sync_methods(SyncBalance)
        missing = [m for m in async_methods if m not in sync_methods]
        assert not missing, f"SyncBalance missing methods: {missing}"

    def test_signatures_match_modulo_await(self) -> None:
        async_methods = _public_async_methods(Balance)
        sync_methods = _public_sync_methods(SyncBalance)
        diffs: list[str] = []
        for name, async_sig in async_methods.items():
            sync_sig = sync_methods[name]
            async_params = tuple(async_sig.parameters.keys())
            sync_params = tuple(sync_sig.parameters.keys())
            if async_params != sync_params:
                diffs.append(
                    f"{name}: async={async_params!r} sync={sync_params!r}",
                )
        assert not diffs, "parameter-name drift:\n" + "\n".join(diffs)


# ---------------------------------------------------------------------------
# Sync sinks + record + pipe.
# ---------------------------------------------------------------------------


class TestSyncRecordAndSinks:
    def test_sync_record_collects_batches(self) -> None:
        with SyncSartoriusManager() as mgr:
            mgr.add("b1", FakeTransport(_poll_script()), timeout=0.1)
            with record(mgr, rate_hz=20.0, duration=0.2) as recording:
                batches = list(recording.stream)
        assert batches
        assert all("b1" in batch for batch in batches)

    def test_sync_pipe_into_memory_sink(self) -> None:
        with SyncSartoriusManager() as mgr:
            mgr.add("b1", FakeTransport(_poll_script()), timeout=0.1)
            with SyncInMemorySink(portal=mgr.portal) as sink:
                with record(mgr, rate_hz=20.0, duration=0.2) as recording:
                    summary = pipe(recording.stream, sink)
                assert summary.samples_emitted >= 1
                assert sink.samples
                assert sink.samples[0].device == "b1"

    def test_sync_pipe_into_csv_sink(self, tmp_path: Path) -> None:
        path = tmp_path / "sync.csv"
        with SyncSartoriusManager() as mgr:
            mgr.add("b1", FakeTransport(_poll_script()), timeout=0.1)
            with (
                SyncCsvSink(path, portal=mgr.portal) as sink,
                record(mgr, rate_hz=20.0, duration=0.15) as recording,
            ):
                pipe(recording.stream, sink)
        content = path.read_text(encoding="utf-8").splitlines()
        assert len(content) >= 2  # header + ≥1 row
        assert "device" in content[0]
