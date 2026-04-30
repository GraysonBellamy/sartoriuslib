"""End-to-end flow tests for the metrology / parameters / cal surface.

These complement the per-command unit tests under
:mod:`tests.unit.commands` and the per-feature tests under
:mod:`tests.unit.devices` by walking the user through realistic
scenarios — multi-step interactions where ordering, cache behaviour,
and capability gating all have to cooperate.

Three family flows (MSE1203S / WZA8202-N / BCE3202-1S) mirror
``docs/protocol.md`` §14.2's cross-family presence matrix.
"""

from __future__ import annotations

import math
import struct

import pytest

from sartoriuslib import (
    BalanceFamily,
    Capability,
    ProtocolKind,
    SartoriusConfirmationRequiredError,
    Unit,
    open_device,
)
from sartoriuslib.protocol.xbpi import build_command, checksum
from sartoriuslib.registry.aliases import normalise
from sartoriuslib.registry.modes import FilterMode
from sartoriuslib.registry.parameters import PARAMETER_TABLE
from sartoriuslib.registry.units import DISPLAY_UNIT_CODE_TO_UNIT
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    build_metrology_script,
    build_parameter_read_script,
    build_parameter_write_script,
    build_temperature_script,
)


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


# ---------------------------------------------------------------------------
# Per-family flow fixtures.
# ---------------------------------------------------------------------------


def _mse_full_script() -> dict[bytes, bytes]:
    """MSE1203S: identity + metrology + p01 read + p01 write + save_menu."""
    script = build_identify_script(model="MSE1203S-100-DR")
    script.update(build_metrology_script(capacity_g=1200.0, increment_g=0.001))
    script.update(build_parameter_read_script(1, current=2, max_value=4))
    script.update(build_parameter_write_script(1, value=3))
    # save_menu ACK
    script[build_command(0x47)] = bytes.fromhex("03410044")
    return script


def _wza_full_script() -> dict[bytes, bytes]:
    """WZA8202-N: identity + metrology (no counter — OEM lacks CONFIG_COUNTER).

    ``config_counter=None`` means the script genuinely omits the
    ``0xBA`` reply — :meth:`Balance._probe_dispatch_capabilities` will
    observe the absence and leave :attr:`Capability.CONFIG_COUNTER`
    unset.
    """
    script = build_identify_script(model="WZA8202-N")
    script.update(
        build_metrology_script(
            capacity_g=8200.0,
            increment_g=0.01,
            config_counter=None,
        )
    )
    return script


def _bce_full_script() -> dict[bytes, bytes]:
    """BCE3202-1S: identity + metrology + counter (BCE has PARAMETER_TABLE+CONFIG_COUNTER)."""
    script = build_identify_script(model="BCE3202-1S")
    script.update(build_metrology_script(capacity_g=3200.0, increment_g=0.01))
    return script


class TestMSEFullFlow:
    @pytest.mark.anyio
    async def test_open_identify_read_write_save(self) -> None:
        """Walk a Cubis unit through identity + metrology + parameter R/W + persistence."""
        transport = FakeTransport(_mse_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        # Identity populated.
        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.CUBIS
        assert info.capacity is not None
        assert math.isclose(info.capacity.value, 1200.0, rel_tol=1e-6)
        assert info.increment is not None
        assert math.isclose(info.increment.value, 0.001, rel_tol=1e-6)
        assert Capability.CONFIG_COUNTER in info.capabilities
        assert Capability.PARAMETER_TABLE in info.capabilities
        # No baked-in temperature priors — runtime discovery is the
        # source of truth (see TestTemperatureDiscovery).
        assert info.temperature_sensor_indices is None

        # Typed accessor round trip.
        mode = await bal.get_filter_mode()
        assert mode is FilterMode.STABLE  # p01 current=2

        # Set + save sequence.
        await bal.set_filter_mode(FilterMode.UNSTABLE, confirm=True)
        tx_write_p01 = build_command(0x56, bytes([0x21, 1, 0x21, 3]))
        assert tx_write_p01 in transport.writes

        # save_menu clears the entire cache.
        pre_save_snapshot_size = len(bal.session.cache_snapshot())
        assert pre_save_snapshot_size >= 2  # at least capacity + increment
        await bal.save_menu(confirm=True)
        assert bal.session.cache_snapshot() == {}
        assert build_command(0x47) in transport.writes

        await bal.close()


class TestWZAFullFlow:
    @pytest.mark.anyio
    async def test_wza_skips_counter(self) -> None:
        """WZA has no CONFIG_COUNTER — no 0xBA reads should ever fire."""
        transport = FakeTransport(_wza_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.OEM_WEIGH_CELL
        assert Capability.CONFIG_COUNTER not in info.capabilities
        # No baked-in priors here either — even WZA, runtime
        # discovery is what fills temperature_sensor_indices.
        assert info.temperature_sensor_indices is None

        await bal.capacity()
        await bal.capacity()

        # ``0xBA`` fires exactly once — the dispatch-capability probe
        # in identify() saw no reply and left CONFIG_COUNTER unset, so
        # cached_execute bypasses the counter read on every subsequent
        # call.
        assert transport.writes.count(build_command(0xBA)) == 1
        # Each capacity call hits the wire.
        tx_capacity = build_command(0x0C, bytes([0x21, 0x00]))
        assert transport.writes.count(tx_capacity) == 3  # identify probe + 2 explicit
        await bal.close()


class TestBCEFullFlow:
    @pytest.mark.anyio
    async def test_bce_has_counter_and_parameter_table(self) -> None:
        """BCE3202-1S is classified BASIC_LAB; caches via CONFIG_COUNTER."""
        transport = FakeTransport(_bce_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.BASIC_LAB
        assert Capability.CONFIG_COUNTER in info.capabilities
        assert Capability.PARAMETER_TABLE in info.capabilities
        # BCE has no TEMPERATURE_SENSORS in the family default — capacity still present.
        assert info.capacity is not None
        await bal.close()


# ---------------------------------------------------------------------------
# Parameter-spec round-trip property tests.
# ---------------------------------------------------------------------------


class TestSpecRoundTrip:
    """Every wire value a :class:`ParameterSpec` accepts must round-trip
    back to the same enum member via encode→decode."""

    def test_all_enum_specs_roundtrip(self) -> None:
        for index, spec in PARAMETER_TABLE.items():
            if spec.unit_enum or spec.enum is None:
                continue
            for member in spec.enum:
                if member.value == 0:
                    # UNKNOWN: encode refuses, decode still maps raw 0 to it.
                    continue
                encoded = spec.encode(member)
                assert encoded == int(member.value), (
                    f"p{index} ({spec.name}): encode({member.name}) = {encoded}, "
                    f"expected {int(member.value)}"
                )
                decoded = spec.decode(encoded)
                assert decoded is member, (
                    f"p{index} ({spec.name}): round-trip {member.name} → {decoded}"
                )

    def test_display_unit_spec_roundtrip(self) -> None:
        """p07 ranges over the 24-entry display-unit table."""
        spec = PARAMETER_TABLE[7]
        for code, unit in DISPLAY_UNIT_CODE_TO_UNIT.items():
            assert spec.encode(unit) == code
            assert spec.decode(code) is unit


# ---------------------------------------------------------------------------
# Edge cases and invariants.
# ---------------------------------------------------------------------------


class TestNormaliseIdempotence:
    """``normalise`` must be idempotent: ``normalise(normalise(x)) == normalise(x)``."""

    @pytest.mark.parametrize(
        "raw",
        [
            "Very Stable",
            "AUTO_W",
            "  mixed  -case.punctuation  ",
            "µg",
            "g",
        ],
    )
    def test_idempotent(self, raw: str) -> None:
        once = normalise(raw)
        twice = normalise(once)
        assert once == twice


class TestTemperatureDiscovery:
    """Runtime sensor discovery — device-agnostic, no family priors."""

    @pytest.mark.anyio
    async def test_sparse_layout_full_walk(self) -> None:
        """Mirror the MSE1203S we tested: sensors at 0/1/3, sentinel at
        2, 0x04 at 4. The walk must visit every replying index (real or
        sentinel) and stop on the first 0x04 without poisoning the
        availability cache for the in-range indices."""
        script = build_identify_script(model="MSE1203S-100-DR")
        script.update(build_metrology_script())
        script.update(
            build_temperature_script(
                sensor_celsius={0: 25.5, 1: 25.6, 2: None, 3: 36.7},
                out_of_range_after=4,
            )
        )
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        discovered = await bal.discover_temperature_sensors()
        assert discovered == (0, 1, 2, 3)

        # The sparse-slot read still works post-discovery (cache wasn't poisoned).
        t2 = await bal.temperature(2)
        assert t2.celsius is None
        # And so does an in-range real sensor.
        t1 = await bal.temperature(1)
        assert t1.celsius is not None
        assert math.isclose(t1.celsius, 25.6, rel_tol=1e-6, abs_tol=1e-12)

        # Cached on DeviceInfo.
        assert bal.info is not None
        assert bal.info.temperature_sensor_indices == (0, 1, 2, 3)

        await bal.close()

    @pytest.mark.anyio
    async def test_no_sensors_returns_empty_tuple(self) -> None:
        """A device that immediately answers 0x04 to ``temperature(0)``
        produces an empty tuple — no sensors at all.

        WZA's family default doesn't seed
        :attr:`Capability.TEMPERATURE_SENSORS`, so the prior gate
        warns once before the call. That's the expected non-strict
        behaviour ("attempt anyway, learn from device") — we filter
        the warning to keep the test focussed on the discovery
        contract."""
        import warnings

        from sartoriuslib.errors import (
            SartoriusCapabilityWarning,
        )

        script = build_identify_script(model="WZA8202-N")
        script.update(build_metrology_script())
        script.update(
            build_temperature_script(
                sensor_celsius={},
                out_of_range_after=0,
            )
        )
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SartoriusCapabilityWarning)
            discovered = await bal.discover_temperature_sensors()
        assert discovered == ()
        assert bal.info is not None
        assert bal.info.temperature_sensor_indices == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_max_index_caps_walk(self) -> None:
        """``max_index`` is a safety cap — walk stops once it exceeds the
        cap even if the device would reply at higher indices."""
        # Script 6 contiguous sensors but cap discovery at 2.
        script = build_identify_script()
        script.update(build_metrology_script())
        script.update(
            build_temperature_script(
                sensor_celsius={i: 25.0 + i for i in range(6)},
                out_of_range_after=None,
            )
        )
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        discovered = await bal.discover_temperature_sensors(max_index=2)
        assert discovered == (0, 1, 2)
        await bal.close()

    @pytest.mark.anyio
    async def test_oor_index_does_not_poison_in_range_indices(self) -> None:
        """Hardware day reproducer: calling ``temperature(4)`` on the MSE
        used to mark the whole command UNSUPPORTED, blocking
        ``temperature(0..3)`` for the rest of the session. With
        ``parameterized=True`` the cache stays clean."""
        from sartoriuslib.errors import (
            SartoriusIndexOutOfRangeError,
        )

        script = build_identify_script()
        script.update(build_metrology_script())
        script.update(
            build_temperature_script(
                sensor_celsius={0: 25.5, 1: 25.6},
                out_of_range_after=2,
            )
        )
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        # First, hit the out-of-range path directly.
        with pytest.raises(SartoriusIndexOutOfRangeError):
            await bal.temperature(2)

        # In-range reads must still work afterwards — the previous bug
        # was that the whole command got cached UNSUPPORTED.
        t0 = await bal.temperature(0)
        assert t0.celsius is not None
        assert math.isclose(t0.celsius, 25.5, rel_tol=1e-6, abs_tol=1e-12)
        t1 = await bal.temperature(1)
        assert t1.celsius is not None
        assert math.isclose(t1.celsius, 25.6, rel_tol=1e-6, abs_tol=1e-12)
        await bal.close()


class TestRawParameterWithoutSpec:
    """Reading a parameter index that isn't in the typed registry still
    works via the raw ``read_parameter`` escape hatch."""

    @pytest.mark.anyio
    async def test_read_unmapped_parameter_returns_entry(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        # p25 is [LIKELY], not in the typed table — still readable raw.
        script.update(build_parameter_read_script(25, current=1, max_value=5))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        entry = await bal.read_parameter(25)
        assert entry.index == 25
        assert entry.current == 1
        assert entry.max == 5
        await bal.close()


class TestCalibrationVariants:
    @pytest.mark.anyio
    async def test_internal_adjust_accepts_alternate_cal_type(self) -> None:
        """cal_type=0x70 drives an external-cal variant per §7.7."""
        script = build_identify_script()
        script.update(build_metrology_script())
        tx_adjust = build_command(0x28, bytes([0x21, 0x70]))
        script[tx_adjust] = bytes.fromhex("03410044")  # ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        await bal.internal_adjust(cal_type=0x70, confirm=True)
        assert tx_adjust in transport.writes
        await bal.close()

    @pytest.mark.anyio
    async def test_last_cal_record_full_record(self) -> None:
        """Populated record: temp + MSE signature + counters."""
        script = build_identify_script()
        script.update(build_metrology_script())
        signature = bytes.fromhex("010900040206000701")
        counters = bytes([0x05, 0x06, 0x07])
        body = struct.pack(">f", 26.5) + signature + counters + bytes([0x00])
        script[build_command(0xB9)] = _rx(0x51, body)
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        record = await bal.last_cal_record()
        assert record.has_metadata is True
        assert record.signature == signature
        assert record.counters == counters
        await bal.close()


class TestReloadMenuFlushesCache:
    """reload_menu overwrites runtime state from EEPROM — any cached
    read could now be stale. The facade clears the cache defensively."""

    @pytest.mark.anyio
    async def test_reload_clears_cache(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        script[build_command(0x46)] = bytes.fromhex("03410044")  # ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        assert len(bal.session.cache_snapshot()) >= 2
        await bal.reload_menu(confirm=True)
        assert bal.session.cache_snapshot() == {}
        await bal.close()


class TestSafetyTierSafetyProofs:
    """PERSISTENT / DANGEROUS commands never write bytes when
    ``confirm`` is omitted."""

    @pytest.mark.anyio
    async def test_write_parameter_no_confirm_writes_nothing(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.write_parameter(1, 2)
        assert len(transport.writes) == writes_before

        # No 0x56 on the wire, ever.
        tx_write = build_command(0x56, bytes([0x21, 1, 0x21, 2]))
        assert tx_write not in transport.writes
        await bal.close()

    @pytest.mark.anyio
    async def test_internal_adjust_no_confirm_writes_nothing(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.internal_adjust()
        assert len(transport.writes) == writes_before
        # No 0x28 with any cal_type ever on the wire.
        assert not any(w.startswith(bytes([0x04, 0x01, 0x09, 0x28])) for w in transport.writes)
        await bal.close()

    @pytest.mark.anyio
    async def test_save_menu_no_confirm_writes_nothing(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError):
            await bal.save_menu()
        assert len(transport.writes) == writes_before
        assert build_command(0x47) not in transport.writes
        await bal.close()


class TestCachePreservesObjectIdentity:
    """Cached returns must be the same object (or at least equal) as the
    first fetch — the cache must not decode the bytes twice."""

    @pytest.mark.anyio
    async def test_capacity_cache_returns_equal_quantity(self) -> None:
        script = build_identify_script()
        script.update(build_metrology_script())
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        q1 = await bal.capacity()
        q2 = await bal.capacity()
        q3 = await bal.capacity()
        assert q1 == q2 == q3
        # Unit / value identity preserved.
        assert q1.value == q2.value
        assert q1.unit is q2.unit is Unit.UNKNOWN
        await bal.close()


class TestIdentifyDoesNotPoisonCacheOnFailure:
    """If metrology probe in identify fails, later explicit capacity()
    calls should still work (not carry forward the failure)."""

    @pytest.mark.anyio
    async def test_later_capacity_works_after_probe_timeout(self) -> None:
        # Script: identify only — metrology probe in identify() times out.
        script = build_identify_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        assert bal.info is not None
        assert bal.info.capacity is None  # probe failed

        # Now script the metrology replies and call capacity() explicitly.
        for tx, rx in build_metrology_script(capacity_g=500.0).items():
            transport.add_script(tx, rx)
        q = await bal.capacity()
        assert math.isclose(q.value, 500.0, rel_tol=1e-6)
        await bal.close()
