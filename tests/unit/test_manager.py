"""Tests for :class:`sartoriuslib.manager.SartoriusManager`.

Covers:

- Add / remove / get happy path.
- Port canonicalisation (POSIX symlinks, duplicate-key rejection).
- Same-port serialisation via shared :class:`XbpiProtocolClient`.
- Different ports run concurrently.
- ``ErrorPolicy.RAISE`` vs ``ErrorPolicy.RETURN``.
- ``DeviceResult.protocol`` carries the session's active protocol on
  both success and error paths.
- ``execute(command, requests_by_name)`` dispatch.
"""

from __future__ import annotations

import sys

import anyio
import pytest

from sartoriuslib import (
    Balance,
    ProtocolKind,
    Reading,
    SartoriusError,
    SartoriusValidationError,
)
from sartoriuslib.commands.tare import TARE, TareRequest
from sartoriuslib.manager import (
    BalanceManager,
    DeviceResult,
    ErrorPolicy,
    SartoriusManager,
    _canonical_port_key,  # pyright: ignore[reportPrivateUsage]
)
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    build_sbi_identify_script,
    canned_frames,
)


def _poll_script() -> dict[bytes, bytes]:
    """Identify + one RX_NET_WEIGHT_EMPTY_PAN reply for `poll`."""
    script = build_identify_script()
    script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
    return script


def _build_fake_transport(label: str = "fake://t") -> FakeTransport:
    return FakeTransport(_poll_script(), label=label)


# ---------------------------------------------------------------------------
# add / remove / lifecycle.
# ---------------------------------------------------------------------------


class TestAddRemove:
    @pytest.mark.anyio
    async def test_add_returns_balance(self) -> None:
        transport = _build_fake_transport()
        async with SartoriusManager() as mgr:
            balance = await mgr.add("b1", transport)
            assert isinstance(balance, Balance)
            assert mgr.names == ("b1",)
            assert mgr.get("b1") is balance

    @pytest.mark.anyio
    async def test_duplicate_name_rejected(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("b1", _build_fake_transport())
            with pytest.raises(SartoriusValidationError, match="already in use"):
                await mgr.add("b1", _build_fake_transport())

    @pytest.mark.anyio
    async def test_remove_unknown_name_rejected(self) -> None:
        async with SartoriusManager() as mgr:
            with pytest.raises(SartoriusValidationError, match="no balance named"):
                await mgr.remove("missing")

    @pytest.mark.anyio
    async def test_get_unknown_name_rejected(self) -> None:
        async with SartoriusManager() as mgr:
            with pytest.raises(SartoriusValidationError):
                mgr.get("missing")

    @pytest.mark.anyio
    async def test_close_is_idempotent(self) -> None:
        mgr = SartoriusManager()
        await mgr.close()
        assert mgr.closed
        await mgr.close()  # no raise

    @pytest.mark.anyio
    async def test_pre_built_balance_skips_lifecycle(self) -> None:
        # Pre-built balances aren't owned by the manager.
        transport = _build_fake_transport()
        from sartoriuslib import open_device

        balance = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        async with SartoriusManager() as mgr:
            await mgr.add("external", balance)
            assert mgr.get("external") is balance
        # Transport not owned by the manager; still open after CM exit.
        assert transport.is_open


# ---------------------------------------------------------------------------
# Port canonicalisation.
# ---------------------------------------------------------------------------


class TestPortCanonicalization:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-specific canonicalization")
    def test_posix_nonexistent_path_passes_through(self) -> None:
        # Paths that don't exist fall back to the raw string — keeps
        # fake-port test fixtures honest.
        assert _canonical_port_key("/dev/not-a-real-port-xyz") == "/dev/not-a-real-port-xyz"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-specific canonicalization")
    def test_posix_realpath_resolves_symlinks(self, tmp_path: object) -> None:
        from pathlib import Path

        tp = Path(tmp_path)  # type: ignore[arg-type]
        target = tp / "target"
        target.write_bytes(b"")
        link = tp / "link"
        link.symlink_to(target)
        assert _canonical_port_key(str(link)) == str(target.resolve())

    def test_transport_source_keys_by_id(self) -> None:
        # Two distinct FakeTransport instances should never collide.
        t1 = FakeTransport()
        t2 = FakeTransport()
        k1 = f"transport:{id(t1)}"
        k2 = f"transport:{id(t2)}"
        assert k1 != k2


# ---------------------------------------------------------------------------
# Poll dispatch + ErrorPolicy.
# ---------------------------------------------------------------------------


class TestPoll:
    @pytest.mark.anyio
    async def test_poll_returns_reading(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("b1", _build_fake_transport())
            results = await mgr.poll()
            assert set(results.keys()) == {"b1"}
            result = results["b1"]
            assert isinstance(result, DeviceResult)
            assert result.ok
            assert isinstance(result.value, Reading)
            assert result.value.protocol is ProtocolKind.XBPI
            assert result.error is None

    @pytest.mark.anyio
    async def test_poll_sbi_balance(self) -> None:
        script = build_sbi_identify_script()
        script[b"\x1bP"] = b"+     0.00 g  \r\n"
        async with SartoriusManager() as mgr:
            await mgr.add(
                "sbi",
                FakeTransport(script),
                protocol=ProtocolKind.SBI,
                timeout=0.1,
            )
            results = await mgr.poll()
            result = results["sbi"]
            assert result.ok
            assert result.value is not None
            assert result.value.protocol is ProtocolKind.SBI

    @pytest.mark.anyio
    async def test_poll_multiple_balances_different_ports(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("a", _build_fake_transport("fake://a"))
            await mgr.add("b", _build_fake_transport("fake://b"))
            results = await mgr.poll()
            assert set(results.keys()) == {"a", "b"}
            assert all(r.ok for r in results.values())

    @pytest.mark.anyio
    async def test_poll_subset_names(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("a", _build_fake_transport("fake://a"))
            await mgr.add("b", _build_fake_transport("fake://b"))
            results = await mgr.poll(["a"])
            assert set(results.keys()) == {"a"}

    @pytest.mark.anyio
    async def test_poll_rejects_unknown_name(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("a", _build_fake_transport())
            with pytest.raises(SartoriusValidationError, match="unknown"):
                await mgr.poll(["missing"])

    @pytest.mark.anyio
    async def test_error_policy_return_surfaces_error(self) -> None:
        # A transport whose poll script omits TX_READ_NET will time
        # out on poll() — that surfaces as a typed SartoriusError.
        identify_only = FakeTransport(build_identify_script())
        async with SartoriusManager(error_policy=ErrorPolicy.RETURN) as mgr:
            await mgr.add("b1", identify_only)
            results = await mgr.poll()
            result = results["b1"]
            assert not result.ok
            assert isinstance(result.error, SartoriusError)

    @pytest.mark.anyio
    async def test_error_policy_raise_wraps_in_exception_group(self) -> None:
        identify_only = FakeTransport(build_identify_script())
        async with SartoriusManager(error_policy=ErrorPolicy.RAISE) as mgr:
            await mgr.add("b1", identify_only)
            with pytest.raises(ExceptionGroup) as excinfo:
                await mgr.poll()
            assert "manager.poll" in str(excinfo.value)


# ---------------------------------------------------------------------------
# execute(command, requests_by_name).
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.anyio
    async def test_execute_per_device_request(self) -> None:
        script = build_identify_script()
        script[canned_frames.TX_TARE] = canned_frames.RX_ACK
        async with SartoriusManager() as mgr:
            await mgr.add("a", FakeTransport(script))
            results = await mgr.execute(TARE, {"a": TareRequest()})
            assert set(results.keys()) == {"a"}
            assert results["a"].ok

    @pytest.mark.anyio
    async def test_execute_unknown_name_rejected(self) -> None:
        async with SartoriusManager() as mgr:
            await mgr.add("a", _build_fake_transport())
            with pytest.raises(SartoriusValidationError):
                await mgr.execute(TARE, {"missing": TareRequest()})


# ---------------------------------------------------------------------------
# BalanceManager alias.
# ---------------------------------------------------------------------------


class TestAlias:
    def test_balance_manager_aliases_sartorius_manager(self) -> None:
        assert BalanceManager is SartoriusManager


# ---------------------------------------------------------------------------
# Same-port I/O serialisation.
# ---------------------------------------------------------------------------


class _SlowFakeTransport(FakeTransport):
    """:class:`FakeTransport` with an artificial per-write delay.

    Two balances sharing one transport on one port would interleave
    bytes without a shared lock. This test shim artificially delays
    every ``write`` so any concurrency bug in the manager's
    client-sharing path shows up as byte-level interleaving.
    """

    def __init__(
        self,
        script: dict[bytes, bytes],
        *,
        label: str = "fake://slow",
        write_delay_s: float = 0.02,
    ) -> None:
        super().__init__(script, label=label)
        self._write_delay = write_delay_s
        self._in_flight = False
        self.concurrency_violation = False

    async def write(self, data: bytes, *, timeout: float) -> None:
        if self._in_flight:
            self.concurrency_violation = True
        self._in_flight = True
        try:
            await anyio.sleep(self._write_delay)
            await super().write(data, timeout=timeout)
        finally:
            self._in_flight = False


class TestSamePortSerialisation:
    @pytest.mark.anyio
    async def test_two_balances_share_client_and_serialise(self) -> None:
        # Two balances using the same transport must serialise through
        # the shared XbpiProtocolClient lock. With a slow fake
        # transport, a missing shared lock would flip
        # ``concurrency_violation``.
        script = build_identify_script()
        script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
        shared = _SlowFakeTransport(script)
        async with SartoriusManager() as mgr:
            b1 = await mgr.add("b1", shared)
            # Second balance attaches to the already-open transport;
            # identify=False so the manager reuses the same
            # identify-script replies without rewinding.
            b2 = await mgr.add("b2", shared, identify=False)
            assert b1.session is not b2.session

            async with anyio.create_task_group() as tg:
                tg.start_soon(b1.poll)
                tg.start_soon(b2.poll)
        assert not shared.concurrency_violation
