"""Tests for Session's ``0xBA``-keyed result cache (design §6.3)."""

from __future__ import annotations

import math

import pytest

from sartoriuslib import Capability, ProtocolKind, open_device
from sartoriuslib.protocol.xbpi import build_command, checksum
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


def _short_data_u8(value: int) -> bytes:
    return _rx(0x21, bytes([value]))


def _parameter_reply(current: int, max_value: int) -> bytes:
    """``0x55`` reply mirroring ``testing._parameter_rx``."""
    return _rx(0x21, bytes([current, 0x21, max_value]))


TX_CAPACITY = build_command(0x0C, bytes([0x21, 0x00]))
TX_INCREMENT = build_command(0x0D, bytes([0x21, 0x00]))
TX_COUNTER = build_command(0xBA)
TX_SAVE_MENU = build_command(0x47)


def _mse_script(
    *,
    counter: int = 1,
    capacity_g: float = 1200.0,
    increment_g: float = 0.001,
) -> dict[bytes, bytes]:
    """Identify + metrology script for a CUBIS balance (has CONFIG_COUNTER)."""
    script = build_identify_script()
    script.update(
        build_metrology_script(
            capacity_g=capacity_g,
            increment_g=increment_g,
            config_counter=counter,
        )
    )
    return script


class TestCacheHit:
    @pytest.mark.anyio
    async def test_capacity_cached_when_counter_stable(self) -> None:
        """Two capacity() calls with the same 0xBA return the cached value."""
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        # First capacity call lives inside identify() — counts as one probe.
        q1 = await bal.capacity()
        q2 = await bal.capacity()
        assert q1 == q2
        # Capacity fires once on the wire even though we called it three times
        # (once via identify, twice explicitly).
        assert transport.writes.count(TX_CAPACITY) == 1
        await bal.aclose()

    @pytest.mark.anyio
    async def test_increment_cached_separately_from_capacity(self) -> None:
        """Capacity and increment use distinct cache keys."""
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        await bal.increment()
        await bal.increment()
        assert transport.writes.count(TX_INCREMENT) == 1
        await bal.aclose()

    @pytest.mark.anyio
    async def test_cache_snapshot_includes_metrology_keys(self) -> None:
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        snap = bal.session.cache_snapshot()
        assert "capacity:0" in snap
        assert "increment:0" in snap
        # Every value pinned to the same counter that identify observed.
        assert all(counter == 1 for counter in snap.values())
        await bal.aclose()


class TestMetrologyCompositeUnit:
    """Capacity / increment fold the display-unit into a complete
    :class:`Quantity` because the typed-float wire body has no unit byte
    (``docs/protocol.md`` §7.2). ``get_display_unit()`` is itself cached
    on ``0xBA`` so the fold doesn't add wire chatter to repeat calls."""

    @pytest.mark.anyio
    async def test_capacity_unit_folded_from_p07(self) -> None:
        script = _mse_script()
        # p07 = 2 → Unit.G (DISPLAY_UNIT_CODE_TO_UNIT)
        script.update(build_parameter_read_script(7, current=2, max_value=24))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        from sartoriuslib import Unit

        q = await bal.capacity()
        assert math.isclose(q.value, 1200.0, rel_tol=1e-6, abs_tol=1e-12)
        assert q.unit is Unit.G
        await bal.aclose()

    @pytest.mark.anyio
    async def test_capacity_falls_open_when_display_unit_unreadable(
        self,
    ) -> None:
        """If ``get_display_unit()`` raises (no p07 in the script,
        FakeTransport replies with a timeout-equivalent absence), the
        capacity result preserves :attr:`Unit.UNKNOWN` rather than
        raising — losing a value because we can't decorate it would
        be the wrong trade-off."""
        # No p07 entry in the script → read_parameter(7) won't have a
        # canned reply.
        script = _mse_script()
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)

        from sartoriuslib import Unit

        q = await bal.capacity()
        # Numeric value still returns; unit fails open to UNKNOWN.
        assert math.isclose(q.value, 1200.0, rel_tol=1e-6, abs_tol=1e-12)
        assert q.unit is Unit.UNKNOWN
        await bal.aclose()

    @pytest.mark.anyio
    async def test_repeated_capacity_does_not_refetch_p07(self) -> None:
        """``get_display_unit()`` caches via the parameter-table
        ``0xBA`` cache, so two ``capacity()`` calls only fire the
        ``0x55 21 07`` read once even after the wire fold landed."""
        script = _mse_script()
        script.update(build_parameter_read_script(7, current=2, max_value=24))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)

        await bal.capacity()
        await bal.capacity()

        tx_p07 = build_command(0x55, bytes([0x21, 7]))
        # Exactly one p07 read across both capacity() calls.
        assert transport.writes.count(tx_p07) == 1
        await bal.aclose()


class TestCacheInvalidateOnCounterBump:
    @pytest.mark.anyio
    async def test_counter_change_refetches(self) -> None:
        """A ``0xBA`` bump between reads flushes the cache for that entry."""
        transport = FakeTransport(_mse_script(counter=1))
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        # First capacity() lived in identify(); already cached.
        before = transport.writes.count(TX_CAPACITY)
        # Flip the 0xBA reply so the next counter read observes a change.
        transport.add_script(TX_COUNTER, _short_data_u8(2))
        await bal.capacity()
        assert transport.writes.count(TX_CAPACITY) == before + 1
        # Cache is re-populated with the new counter.
        assert bal.session.cache_snapshot()["capacity:0"] == 2
        await bal.aclose()


class TestCacheCaveatRows:
    """§6.3 caveat: writes to ``p13`` / ``p50`` don't bump ``0xBA`` — the
    Balance facade's ``write_parameter`` must still invalidate the
    cache for that index."""

    @pytest.mark.anyio
    async def test_write_invalidates_cache_for_that_index(self) -> None:
        script = _mse_script()
        script.update(build_parameter_read_script(13, current=1, max_value=3))
        script.update(build_parameter_write_script(13, value=2))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        tx_read = build_command(0x55, bytes([0x21, 13]))

        # First read populates the cache.
        entry1 = await bal.read_parameter(13)
        assert entry1.current == 1
        writes_after_first_read = transport.writes.count(tx_read)

        # Second read: cache hit (counter stable) — no new write.
        entry2 = await bal.read_parameter(13)
        assert entry2.current == 1
        assert transport.writes.count(tx_read) == writes_after_first_read

        # Write to p13. Counter stays at 1 on the wire (the caveat)
        # but the facade must invalidate anyway.
        await bal.write_parameter(13, 2, confirm=True)

        # Next read must re-fetch despite counter being stable.
        transport.add_script(tx_read, _parameter_reply(2, 3))
        entry3 = await bal.read_parameter(13)
        assert entry3.current == 2, "post-write read must not serve stale cache"
        assert transport.writes.count(tx_read) == writes_after_first_read + 1
        await bal.aclose()


class TestNoCacheWithoutCapability:
    """WZA sessions lack CONFIG_COUNTER — cache is bypassed entirely."""

    @pytest.mark.anyio
    async def test_cubis_lacking_0xba_does_not_get_capability(self) -> None:
        """Probe-driven CONFIG_COUNTER detection: even a CUBIS-shaped
        device that doesn't reply to ``0xBA`` must not get the cap
        seeded. Hardware day surfaced the design risk — a stripped-down
        Cubis variant (or a future firmware that drops 0xBA) used to
        get the cap from the family table, then fail every cached
        ``capacity()`` call when ``cached_execute`` tried to read 0xBA.
        Now the cap is observed, not assumed."""
        # MSE1203S model string but no 0xBA reply.
        script = build_identify_script(model="MSE1203S-100-DR")
        script.update(build_metrology_script(capacity_g=1200.0, config_counter=None))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        # Family is still CUBIS (string match) — but the cap is unset.
        assert bal.session.family.name == "CUBIS"
        assert Capability.CONFIG_COUNTER not in bal.session.capabilities
        # Cached metrology calls work without raising — they fall
        # through to direct execution, exactly like WZA.
        q1 = await bal.capacity()
        q2 = await bal.capacity()
        assert q1.value == q2.value
        await bal.aclose()

    @pytest.mark.anyio
    async def test_wza_every_capacity_call_hits_wire(self) -> None:
        script = build_identify_script(model="WZA8202-N")
        # ``config_counter=None`` — the simulated WZA genuinely doesn't
        # reply to ``0xBA``, so :meth:`Balance._probe_dispatch_capabilities`
        # will leave CONFIG_COUNTER unset and the cache will be bypassed.
        script.update(build_metrology_script(capacity_g=8200.0, config_counter=None))
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        assert Capability.CONFIG_COUNTER not in bal.session.capabilities
        count_before = transport.writes.count(TX_CAPACITY)
        await bal.capacity()
        await bal.capacity()
        # Both calls hit the wire — no caching.
        assert transport.writes.count(TX_CAPACITY) == count_before + 2
        # Counter opcode fired exactly once: the dispatch-capability
        # probe in identify(). After that the absence is observed and
        # cached_execute bypasses the counter read.
        assert transport.writes.count(TX_COUNTER) == 1
        await bal.aclose()


class TestInvalidateCacheApi:
    @pytest.mark.anyio
    async def test_explicit_invalidate_forces_refetch(self) -> None:
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        before = transport.writes.count(TX_CAPACITY)
        bal.session.invalidate_cache("capacity:0")
        await bal.capacity()
        assert transport.writes.count(TX_CAPACITY) == before + 1
        await bal.aclose()

    @pytest.mark.anyio
    async def test_invalidate_unknown_key_is_noop(self) -> None:
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        snap_before = bal.session.cache_snapshot()
        bal.session.invalidate_cache("nonexistent_key")
        assert bal.session.cache_snapshot() == snap_before
        await bal.aclose()

    @pytest.mark.anyio
    async def test_invalidate_all_clears_everything(self) -> None:
        transport = FakeTransport(_mse_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        assert len(bal.session.cache_snapshot()) >= 2  # capacity + increment
        bal.session.invalidate_cache()
        assert bal.session.cache_snapshot() == {}
        await bal.aclose()

    @pytest.mark.anyio
    async def test_save_menu_flushes_entire_cache(self) -> None:
        script = _mse_script()
        script[TX_SAVE_MENU] = bytes.fromhex("03410044")  # ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        assert len(bal.session.cache_snapshot()) >= 2
        await bal.save_menu(confirm=True)
        assert bal.session.cache_snapshot() == {}
        await bal.aclose()
