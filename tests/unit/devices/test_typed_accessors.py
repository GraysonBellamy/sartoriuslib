"""Tests for :class:`Balance`'s typed parameter accessors + metrology surface."""

from __future__ import annotations

import math
import struct

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusConfirmationRequiredError,
    Unit,
    open_device,
)
from sartoriuslib.errors import SartoriusValidationError, UnknownUnitError
from sartoriuslib.protocol.xbpi import build_command, checksum
from sartoriuslib.registry.modes import (
    AutoZeroMode,
    FilterMode,
    IsoCalMode,
    MenuAccessMode,
    TareBehavior,
)
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    build_metrology_script,
    build_parameter_read_script,
    build_parameter_write_script,
)


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


def _base_script() -> dict[bytes, bytes]:
    script = build_identify_script()
    script.update(build_metrology_script())
    return script


# ---------------------------------------------------------------------------
# Metrology facade.
# ---------------------------------------------------------------------------


class TestMetrologyFacade:
    @pytest.mark.anyio
    async def test_capacity_returns_quantity(self) -> None:
        transport = FakeTransport(_base_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        q = await bal.capacity()
        assert math.isclose(q.value, 1200.0, abs_tol=1e-6)
        # Opcode reply carries no unit byte — Unit.UNKNOWN is honest.
        assert q.unit is Unit.UNKNOWN
        await bal.aclose()

    @pytest.mark.anyio
    async def test_increment_returns_quantity(self) -> None:
        transport = FakeTransport(_base_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        q = await bal.increment()
        assert math.isclose(q.value, 0.001, abs_tol=1e-9)
        await bal.aclose()

    @pytest.mark.anyio
    async def test_temperature_round_trips_sensor_index(self) -> None:
        """Balance wraps decode to fill the ``sensor`` field."""
        script = _base_script()
        # Sensor 2 — typed_float 23.5 °C with aux byte 0.
        tx_temp = build_command(0x76, bytes([0x21, 2]))
        body = struct.pack(">f", 23.5) + bytes([0x00])
        script[tx_temp] = _rx(0x35, body)
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        reading = await bal.temperature(sensor=2)
        assert reading.sensor == 2
        assert reading.celsius is not None
        assert math.isclose(reading.celsius, 23.5, abs_tol=1e-3)
        await bal.aclose()

    @pytest.mark.anyio
    async def test_temperature_sensor_not_installed(self) -> None:
        script = _base_script()
        tx_temp = build_command(0x76, bytes([0x21, 4]))
        # Sentinel + aux byte.
        script[tx_temp] = _rx(0x35, b"\x7f\xff\xff\xff\x00")
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        reading = await bal.temperature(sensor=4)
        assert reading.sensor == 4
        assert reading.celsius is None
        await bal.aclose()


# ---------------------------------------------------------------------------
# Typed parameter accessors.
# ---------------------------------------------------------------------------


class TestTypedGetters:
    @pytest.mark.anyio
    async def test_get_filter_mode_decodes_enum(self) -> None:
        script = _base_script()
        # p01 = 2 (STABLE), max=4.
        script.update(build_parameter_read_script(1, current=2, max_value=4))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        mode = await bal.get_filter_mode()
        assert mode is FilterMode.STABLE
        await bal.aclose()

    @pytest.mark.anyio
    async def test_get_display_unit_decodes_unit(self) -> None:
        script = _base_script()
        # p07 = 2 (g), max=24.
        script.update(build_parameter_read_script(7, current=2, max_value=24))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        unit = await bal.get_display_unit()
        assert unit is Unit.G
        await bal.aclose()

    @pytest.mark.anyio
    async def test_get_auto_zero_decodes_enum(self) -> None:
        script = _base_script()
        script.update(build_parameter_read_script(6, current=1, max_value=2))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        mode = await bal.get_auto_zero()
        assert mode is AutoZeroMode.ON
        await bal.aclose()

    @pytest.mark.anyio
    async def test_get_isocal_mode_decodes_enum(self) -> None:
        script = _base_script()
        script.update(build_parameter_read_script(15, current=3, max_value=4))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        mode = await bal.get_isocal_mode()
        assert mode is IsoCalMode.ON
        await bal.aclose()

    @pytest.mark.anyio
    async def test_get_tare_behavior_decodes_enum(self) -> None:
        script = _base_script()
        script.update(build_parameter_read_script(5, current=2, max_value=3))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        mode = await bal.get_tare_behavior()
        assert mode is TareBehavior.WITH_STABILITY
        await bal.aclose()

    @pytest.mark.anyio
    async def test_get_menu_access_decodes_enum(self) -> None:
        script = _base_script()
        script.update(build_parameter_read_script(40, current=1, max_value=2))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        mode = await bal.get_menu_access()
        assert mode is MenuAccessMode.CAN_EDIT
        await bal.aclose()


class TestTypedSetters:
    @pytest.mark.anyio
    async def test_set_filter_mode_requires_confirm(self) -> None:
        script = _base_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.set_filter_mode(FilterMode.STABLE)
        # PERSISTENT + no confirm → no bytes on the wire.
        assert len(transport.writes) == writes_before
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_filter_mode_encodes_correct_tlvs(self) -> None:
        script = _base_script()
        script.update(build_parameter_write_script(1, value=2))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        await bal.set_filter_mode(FilterMode.STABLE, confirm=True)
        tx_write = build_command(0x56, bytes([0x21, 1, 0x21, 2]))
        assert tx_write in transport.writes
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_filter_mode_accepts_string_alias(self) -> None:
        script = _base_script()
        script.update(build_parameter_write_script(1, value=4))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        # "very unstable" resolves via aliases to FilterMode.VERY_UNSTABLE = 4.
        await bal.set_filter_mode("very unstable", confirm=True)
        tx_write = build_command(0x56, bytes([0x21, 1, 0x21, 4]))
        assert tx_write in transport.writes
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_display_unit_accepts_unit_enum(self) -> None:
        script = _base_script()
        script.update(build_parameter_write_script(7, value=3))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        await bal.set_display_unit(Unit.KG, confirm=True)
        tx_write = build_command(0x56, bytes([0x21, 7, 0x21, 3]))
        assert tx_write in transport.writes
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_display_unit_accepts_string(self) -> None:
        script = _base_script()
        script.update(build_parameter_write_script(7, value=13))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        await bal.set_display_unit("milligram", confirm=True)
        tx_write = build_command(0x56, bytes([0x21, 7, 0x21, 13]))
        assert tx_write in transport.writes
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_display_unit_rejects_unknown_string(self) -> None:
        script = _base_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(UnknownUnitError):
            await bal.set_display_unit("wibbles", confirm=True)
        assert len(transport.writes) == writes_before
        await bal.aclose()

    @pytest.mark.anyio
    async def test_set_filter_mode_rejects_bogus_string(self) -> None:
        script = _base_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusValidationError):
            await bal.set_filter_mode("extremely_calm", confirm=True)
        assert len(transport.writes) == writes_before
        await bal.aclose()


class TestRawParameterFacade:
    @pytest.mark.anyio
    async def test_write_parameter_without_confirm_rejects_pre_io(self) -> None:
        script = _base_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.write_parameter(1, 2)
        assert len(transport.writes) == writes_before
        await bal.aclose()

    @pytest.mark.anyio
    async def test_read_parameter_round_trips_index(self) -> None:
        script = _base_script()
        script.update(build_parameter_read_script(3, current=4, max_value=6))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        entry = await bal.read_parameter(3)
        assert entry.index == 3  # Balance fills the index that decode couldn't see.
        assert entry.current == 4
        assert entry.max == 6
        await bal.aclose()


class TestCalibrationFacade:
    @pytest.mark.anyio
    async def test_internal_adjust_requires_confirm(self) -> None:
        transport = FakeTransport(_base_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.internal_adjust()
        # DANGEROUS + no confirm → no wire writes.
        assert len(transport.writes) == writes_before
        await bal.aclose()

    @pytest.mark.anyio
    async def test_last_cal_record_decodes(self) -> None:
        script = _base_script()
        tx = build_command(0xB9)
        body = struct.pack(">f", 20.85) + bytes(13)  # temp only, no metadata
        script[tx] = _rx(0x51, body)
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        record = await bal.last_cal_record()
        assert record.temperature_celsius is not None
        assert math.isclose(record.temperature_celsius, 20.85, abs_tol=1e-3)
        assert record.has_metadata is False
        await bal.aclose()


class TestIdentifyFillsMetrology:
    @pytest.mark.anyio
    async def test_identify_populates_capacity_and_increment(self) -> None:
        transport = FakeTransport(_base_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        info = bal.info
        assert info is not None
        assert info.capacity is not None
        assert math.isclose(info.capacity.value, 1200.0, abs_tol=1e-6)
        assert info.increment is not None
        assert math.isclose(info.increment.value, 0.001, abs_tol=1e-9)
        await bal.aclose()

    @pytest.mark.anyio
    async def test_identify_survives_missing_metrology_replies(self) -> None:
        """Balances that refuse 0x0C/0x0D still identify cleanly."""
        transport = FakeTransport(build_identify_script())  # no metrology
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        info = bal.info
        assert info is not None
        assert info.capacity is None
        assert info.increment is None
        await bal.aclose()
