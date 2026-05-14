"""``find_devices()`` — multi-port baudrate-sweep discovery.

The unit under test is the sweep / per-port short-circuit / ordering
logic in :func:`sartoriuslib.find_devices`. The wire-protocol detection
is :func:`discover_port`'s job and is covered elsewhere — these tests
stub ``discover_port`` at the module level so they exercise only the
sweep semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from sartoriuslib import (
    DEFAULT_DISCOVERY_BAUDRATES,
    FindResult,
    ProtocolKind,
    SartoriusConnectionError,
    SartoriusError,
    find_devices,
)
from sartoriuslib.devices import discovery as discovery_module
from sartoriuslib.devices.discovery import DiscoveryResult

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
        open_failures: dict[str, Exception] | None = None,
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
    ) -> DiscoveryResult:
        del timeout, sniff_window, src_sbn, dst_sbn
        assert isinstance(port, str), "find_devices passes string ports"
        assert serial_settings is not None, "find_devices supplies SerialSettings"
        baud = serial_settings.baudrate
        self.calls.append(_Probe(port=port, baudrate=baud))
        if port in self._open_failures:
            raise self._open_failures[port]
        if self._autoprint_hits.get(port) == baud:
            return DiscoveryResult(
                port=port,
                baudrate=baud,
                parity=serial_settings.parity.value,
                stopbits=int(serial_settings.stopbits.value),
                protocol=ProtocolKind.SBI,
                autoprint_active=True,
            )
        if self._hits.get(port) == baud:
            return DiscoveryResult(
                port=port,
                baudrate=baud,
                parity=serial_settings.parity.value,
                stopbits=int(serial_settings.stopbits.value),
                protocol=ProtocolKind.XBPI,
                autoprint_active=False,
            )
        return DiscoveryResult(
            port=port,
            baudrate=baud,
            parity=serial_settings.parity.value,
            stopbits=int(serial_settings.stopbits.value),
            protocol=None,
            error="no responsive device",
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

        assert results == [
            FindResult(
                port="COM1",
                baudrate=57600,
                protocol=ProtocolKind.XBPI,
                ok=True,
                autoprint_active=False,
                error=None,
            ),
        ]
        # Short-circuit on hit: 115200 should not be probed.
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
    async def test_silent_port_returns_single_miss_at_last_baud(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub = _StubDiscoverPort()
        _install_stub(monkeypatch, stub)

        results = await find_devices(ports=["COM1"], baudrates=(9600, 19200))

        assert len(results) == 1
        result = results[0]
        assert result.ok is False
        assert result.protocol is None
        assert result.baudrate == 19200
        # discover_port's per-baud error string is preserved as SartoriusError.
        assert isinstance(result.error, SartoriusError)
        # Every baud probed (no short-circuit on miss).
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

        assert len(results) == 2
        assert results[0].port == "COM1"
        assert results[0].ok is True
        assert results[0].baudrate == 19200
        assert results[1].port == "COM2"
        assert results[1].ok is False
        # COM1 stopped after the 19200 hit; COM2 swept every baud.
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

        # Order matters — the constant defines the sweep order.
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
        assert sartoriuslib.FindResult is FindResult
        assert sartoriuslib.find_devices is find_devices
