"""Tests for the public frozen dataclasses in :mod:`sartoriuslib.devices.models`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sartoriuslib.devices.capability import Availability, Capability, ProbeSource
from sartoriuslib.devices.kind import BalanceFamily
from sartoriuslib.devices.models import (
    BalanceState,
    BalanceStatus,
    DeviceInfo,
    ProbeOutcome,
    Quantity,
    Reading,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.registry.units import Sign, Unit
from sartoriuslib.transport.base import SerialSettings


def _sample_reading(**overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "value": 199.995,
        "unit": Unit.G,
        "sign": Sign.POSITIVE,
        "stable": True,
        "overload": False,
        "underload": False,
        "decimals": 3,
        "sequence": None,
        "status_flags": {"stable": True},
        "protocol": ProtocolKind.XBPI,
        "received_at": datetime.now(UTC),
        "monotonic_ns": 0,
        "raw": b"\x00",
    }
    defaults.update(overrides)
    return Reading(**defaults)  # type: ignore[arg-type]


class TestReading:
    def test_frozen(self) -> None:
        r = _sample_reading()
        with pytest.raises(FrozenInstanceError):
            r.value = 1.0  # type: ignore[misc]

    def test_value_can_be_none_for_off_scale(self) -> None:
        r = _sample_reading(value=None)
        assert r.value is None

    def test_as_dict_shape(self) -> None:
        r = _sample_reading(raw=b"\x0b\x41\x48\x02")
        d = r.as_dict()
        assert d == {
            "value": 199.995,
            "unit": "g",
            "sign": "positive",
            "stable": 1,
            "overload": 0,
            "underload": 0,
            "decimals": 3,
            "sequence": None,
            "protocol": "xbpi",
            "raw": "0b414802",
        }

    def test_as_dict_preserves_none_value(self) -> None:
        r = _sample_reading(value=None, overload=True, stable=False)
        d = r.as_dict()
        assert d["value"] is None
        assert d["overload"] == 1
        assert d["stable"] == 0

    def test_as_dict_excludes_timing_and_status_flags(self) -> None:
        # Timing provenance lives on Sample; status_flags is opt-in via
        # Reading.status_flags rather than being flattened into rows.
        d = _sample_reading().as_dict()
        assert "received_at" not in d
        assert "monotonic_ns" not in d
        assert "status_flags" not in d

    def test_format_spec_delegates_to_value(self) -> None:
        """``f"{r:.4f}"`` should format the value, not crash. Default
        :func:`object.__format__` rejects non-empty specs with a
        :class:`TypeError` — without this test, the regression slips
        back in trivially. (Caught on hardware day.)"""
        r = _sample_reading(value=199.97499084472656)
        assert f"{r:.4f}" == "199.9750"
        assert f"{r:.2f}" == "199.97"
        assert f"{r:>10.3f}" == "   199.975"

    def test_format_empty_spec_falls_back_to_str(self) -> None:
        """``f"{r}"`` keeps the structured repr — only non-empty specs
        delegate to ``value``. Otherwise users lose the ability to see
        unit / stability / protocol in a casual print."""
        r = _sample_reading()
        assert f"{r}" == str(r)
        assert "Reading(" in f"{r}"

    def test_format_none_value_renders_safely(self) -> None:
        """A stream of mixed valid/None readings (e.g. during a tare
        settle) should not crash an f-string. ``None`` formats as
        ``"None"`` for any numeric spec instead of raising."""
        r = _sample_reading(value=None)
        assert f"{r:.4f}" == "None"
        assert f"{r:>10.4f}" == "None"


class TestBalanceStatus:
    def test_frozen(self) -> None:
        s = BalanceStatus(
            stable=True,
            state=BalanceState.STABLE,
            isocal_due=False,
            adc_trusted=True,
            sequence=0x42,
            raw_state=0x88,
            raw_status=0x18,
            raw=b"\x00" * 8,
        )
        with pytest.raises(FrozenInstanceError):
            s.stable = False  # type: ignore[misc]


class TestDeviceInfo:
    def test_defaults_include_empty_probe_report(self) -> None:
        info = DeviceInfo(
            manufacturer="Sartorius",
            model="MSE1203S",
            serial=None,
            factory_number=None,
            software=None,
            firmware=None,
            family=BalanceFamily.CUBIS,
            protocol=ProtocolKind.XBPI,
            capacity=None,
            increment=None,
            sbn=0,
            serial_settings=SerialSettings(port="/dev/null"),
            capabilities=Capability.XBPI_SUPPORT,
        )
        assert dict(info.probe_report) == {}

    def test_probe_report_can_be_populated(self) -> None:
        outcome = ProbeOutcome(
            availability=Availability.SUPPORTED,
            source=ProbeSource.LIVE_CALL,
            at=None,
            detail="seen on open",
        )
        info = DeviceInfo(
            manufacturer=None,
            model="M",
            serial=None,
            factory_number=None,
            software=None,
            firmware=None,
            family=BalanceFamily.UNKNOWN,
            protocol=ProtocolKind.XBPI,
            capacity=None,
            increment=None,
            sbn=None,
            serial_settings=SerialSettings(port="/dev/null"),
            capabilities=Capability(0),
            probe_report={Capability.XBPI_SUPPORT: outcome},
        )
        assert info.probe_report[Capability.XBPI_SUPPORT].availability is Availability.SUPPORTED

    def test_temperature_sensor_indices_default_none(self) -> None:
        """``None`` means "no prior" — the user should iterate and watch
        for sentinels / 0x04 errors."""
        info = DeviceInfo(
            manufacturer=None,
            model="?",
            serial=None,
            factory_number=None,
            software=None,
            firmware=None,
            family=BalanceFamily.UNKNOWN,
            protocol=ProtocolKind.XBPI,
            capacity=None,
            increment=None,
            sbn=None,
            serial_settings=SerialSettings(port="/dev/null"),
            capabilities=Capability(0),
        )
        assert info.temperature_sensor_indices is None

    def test_temperature_sensor_indices_sparse(self) -> None:
        """The MSE has sensors at (0, 1, 3) — index 2 is the reserved
        slot. Confirmed on hardware day; index 4+ raises 0x04."""
        info = DeviceInfo(
            manufacturer="Sartorius",
            model="MSE1203S",
            serial=None,
            factory_number=None,
            software=None,
            firmware=None,
            family=BalanceFamily.CUBIS,
            protocol=ProtocolKind.XBPI,
            capacity=None,
            increment=None,
            sbn=0,
            serial_settings=SerialSettings(port="/dev/null"),
            capabilities=Capability.TEMPERATURE_SENSORS,
            temperature_sensor_indices=(0, 1, 3),
        )
        assert info.temperature_sensor_indices == (0, 1, 3)
        # 2 is intentionally absent — it's the reserved sentinel slot.
        assert info.temperature_sensor_indices is not None
        assert 2 not in info.temperature_sensor_indices


class TestQuantity:
    def test_frozen(self) -> None:
        q = Quantity(value=1200.0, unit=Unit.G)
        with pytest.raises(FrozenInstanceError):
            q.value = 0.0  # type: ignore[misc]
