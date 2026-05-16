"""``find_devices()`` — multi-port baudrate-sweep discovery.

The unit under test is the sweep / per-port short-circuit / ordering
logic in :func:`sartoriuslib.find_devices`. The wire-protocol detection
is :func:`discover_port`'s job and is covered elsewhere — these tests
stub ``discover_port`` at the module level so they exercise only the
sweep semantics.

After the unified-API migration ``find_devices`` returns one
:class:`SartoriusDiscoveryResult` per probe attempt (port × baud),
not one per port; the per-port fold lives behind
:func:`summarize_discovery`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from sartoriuslib import (
    DEFAULT_DISCOVERY_BAUDRATES,
    DiscoveryResult,
    DiscoverySummary,
    ProtocolKind,
    SartoriusConnectionError,
    SartoriusDiscoveryResult,
    SartoriusError,
    find_devices,
    summarize_discovery,
)
from sartoriuslib.devices import discovery as discovery_module

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sartoriuslib.transport.base import SerialSettings, Transport


@dataclass(frozen=True)
class _Probe:
    port: str
    baudrate: int


class _StubDiscoverPort:
    """Records every call and replies according to a scripted rule set."""

    def __init__(
        self,
        *,
        hits: dict[str, int] | None = None,
        autoprint_hits: dict[str, int] | None = None,
        open_failures: dict[str, SartoriusError] | None = None,
    ) -> None:
        self.calls: list[_Probe] = []
        self._hits = hits or {}
        self._autoprint_hits = autoprint_hits or {}
        self._open_failures = open_failures or {}

    async def __call__(
        self,
        port: str | Transport,
        *,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
        sniff_window: float = 0.25,
        src_sbn: int = 0x01,
        dst_sbn: int = 0x09,
    ) -> SartoriusDiscoveryResult:
        del timeout, sniff_window, src_sbn
        assert isinstance(port, str), "find_devices passes string ports"
        assert serial_settings is not None, "find_devices supplies SerialSettings"
        baud = serial_settings.baudrate
        self.calls.append(_Probe(port=port, baudrate=baud))
        if port in self._open_failures:
            return SartoriusDiscoveryResult(
                ok=False,
                port=port,
                address=None,
                baudrate=baud,
                protocol=None,
                device_info=None,
                error=self._open_failures[port],
                elapsed_s=0.001,
                parity=serial_settings.parity.value,
                stopbits=int(serial_settings.stopbits.value),
            )
        if self._autoprint_hits.get(port) == baud:
            return SartoriusDiscoveryResult(
                ok=True,
                port=port,
                address=None,
                baudrate=baud,
                protocol=ProtocolKind.SBI,
                device_info=None,
                error=None,
                elapsed_s=0.002,
                parity=serial_settings.parity.value,
                stopbits=int(serial_settings.stopbits.value),
                autoprint_active=True,
            )
        if self._hits.get(port) == baud:
            return SartoriusDiscoveryResult(
                ok=True,
                port=port,
                address=dst_sbn,
                baudrate=baud,
                protocol=ProtocolKind.XBPI,
                device_info=None,
                error=None,
                elapsed_s=0.002,
                parity=serial_settings.parity.value,
                stopbits=int(serial_settings.stopbits.value),
            )
        return SartoriusDiscoveryResult(
            ok=False,
            port=port,
            address=None,
            baudrate=baud,
            protocol=None,
            device_info=None,
            error=SartoriusError("no responsive device"),
            elapsed_s=0.001,
            parity=serial_settings.parity.value,
            stopbits=int(serial_settings.stopbits.value),
        )


def _install_stub(
    monkeypatch: pytest.MonkeyPatch,
    stub: _StubDiscoverPort,
) -> None:
    monkeypatch.setattr(discovery_module, "discover_port", stub)


def _bauds(calls: Iterable[_Probe], port: str) -> list[int]:
    return [c.baudrate for c in calls if c.port == port]


class TestFindDevicesHit:
    @pytest.mark.anyio
    async def test_xbpi_hit_at_specific_baud(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort(hits={"COM1": 57600})
        _install_stub(monkeypatch, stub)

        results = await find_devices(
            ports=["COM1"],
            baudrates=(9600, 19200, 38400, 57600, 115200),
        )

        # First hit wins: four probes (3 misses + 1 hit).
        assert len(results) == 4
        assert results[-1].ok is True
        assert results[-1].protocol is ProtocolKind.XBPI
        assert results[-1].baudrate == 57600
        # 115200 not probed.
        assert _bauds(stub.calls, "COM1") == [9600, 19200, 38400, 57600]

    @pytest.mark.anyio
    async def test_sbi_autoprint_hit_sets_autoprint_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort(autoprint_hits={"COM3": 9600})
        _install_stub(monkeypatch, stub)

        results = await find_devices(ports=["COM3"], baudrates=(9600,))

        assert len(results) == 1
        result = results[0]
        assert result.ok is True
        assert result.protocol is ProtocolKind.SBI
        assert result.autoprint_active is True


class TestFindDevicesMiss:
    @pytest.mark.anyio
    async def test_silent_port_returns_one_row_per_baud(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)

        results = await find_devices(ports=["COM1"], baudrates=(9600, 19200))

        assert len(results) == 2
        assert all(r.ok is False for r in results)
        assert [r.baudrate for r in results] == [9600, 19200]
        # Every baud carries a typed SartoriusError.
        assert all(isinstance(r.error, SartoriusError) for r in results)
        assert _bauds(stub.calls, "COM1") == [9600, 19200]


class TestFindDevicesPortOpenFailure:
    @pytest.mark.anyio
    async def test_port_open_failure_short_circuits_remaining_bauds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        boom = SartoriusConnectionError("port busy")
        stub = _StubDiscoverPort(open_failures={"COM1": boom})
        _install_stub(monkeypatch, stub)

        results = await find_devices(
            ports=["COM1"],
            baudrates=(9600, 19200, 38400),
        )

        assert len(results) == 1
        result = results[0]
        assert result.ok is False
        assert result.error is boom
        assert result.baudrate == 9600
        # Only one probe — remaining bauds skipped.
        assert _bauds(stub.calls, "COM1") == [9600]


class TestFindDevicesMultiPort:
    @pytest.mark.anyio
    async def test_each_port_short_circuits_independently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort(hits={"COM1": 19200})
        _install_stub(monkeypatch, stub)

        results = await find_devices(
            ports=["COM1", "COM2"],
            baudrates=(9600, 19200, 38400),
        )

        com1 = [r for r in results if r.port == "COM1"]
        com2 = [r for r in results if r.port == "COM2"]
        # COM1: 9600 miss + 19200 hit (first hit wins).
        assert len(com1) == 2
        assert com1[-1].ok is True
        assert com1[-1].baudrate == 19200
        # COM2: every baud probed, every one a miss.
        assert len(com2) == 3
        assert all(r.ok is False for r in com2)
        assert _bauds(stub.calls, "COM1") == [9600, 19200]
        assert _bauds(stub.calls, "COM2") == [9600, 19200, 38400]

    @pytest.mark.anyio
    async def test_input_port_order_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)

        results = await find_devices(
            ports=["COM5", "COM1", "COM3"],
            baudrates=(9600,),
        )

        assert [r.port for r in results] == ["COM5", "COM1", "COM3"]


class TestFindDevicesDefaults:
    @pytest.mark.anyio
    async def test_default_baudrates_match_module_constant(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)

        await find_devices(ports=["COM1"])

        assert _bauds(stub.calls, "COM1") == list(DEFAULT_DISCOVERY_BAUDRATES)

    @pytest.mark.anyio
    async def test_ports_none_enumerates_via_anyserial(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import anyserial

        async def _fake_list_serial_ports(
            *,
            backend: str = "native",
        ) -> list[anyserial.PortInfo]:
            del backend
            return [
                anyserial.PortInfo(device="COMX"),
                anyserial.PortInfo(device="COMY"),
            ]

        monkeypatch.setattr(anyserial, "list_serial_ports", _fake_list_serial_ports)
        stub = _StubDiscoverPort(hits={"COMY": 9600})
        _install_stub(monkeypatch, stub)

        results = await find_devices(baudrates=(9600,))

        assert [r.port for r in results] == ["COMX", "COMY"]
        assert results[0].ok is False
        assert results[1].ok is True


class TestFindDevicesEdgeCases:
    @pytest.mark.anyio
    async def test_empty_ports_returns_empty_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)

        results = await find_devices(ports=[])

        assert results == []
        assert stub.calls == []


class TestFindDevicesPackageExports:
    def test_constants_and_types_reachable_from_top_level(self) -> None:
        import sartoriuslib

        assert sartoriuslib.DEFAULT_DISCOVERY_BAUDRATES == (
            9600,
            19200,
            38400,
            57600,
            115200,
        )
        assert sartoriuslib.find_devices is find_devices
        assert sartoriuslib.summarize_discovery is summarize_discovery
        assert sartoriuslib.DiscoveryResult is DiscoveryResult
        assert sartoriuslib.SartoriusDiscoveryResult is SartoriusDiscoveryResult
        assert sartoriuslib.DiscoverySummary is DiscoverySummary


class TestSummarizeDiscovery:
    @pytest.mark.anyio
    async def test_hit_summary_uses_winning_baud(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort(hits={"COM1": 19200})
        _install_stub(monkeypatch, stub)
        results = await find_devices(ports=["COM1"], baudrates=(9600, 19200, 38400))
        summaries = summarize_discovery(results)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.ok is True
        assert s.baudrate == 19200
        assert s.protocol is ProtocolKind.XBPI

    @pytest.mark.anyio
    async def test_miss_summary_carries_last_baud_and_first_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)
        results = await find_devices(ports=["COM1"], baudrates=(9600, 19200))
        summaries = summarize_discovery(results)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.ok is False
        assert s.baudrate == 19200  # last attempted
        assert isinstance(s.error, SartoriusError)
