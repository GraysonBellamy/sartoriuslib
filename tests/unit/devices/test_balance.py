"""End-to-end tests for :class:`sartoriuslib.devices.balance.Balance` and
:func:`sartoriuslib.devices.factory.open_device`.

Every test drives a full stack (FakeTransport → xBPI client → session →
balance), covering the public open/poll/tare/identify surface:

    async with await open_device(port, protocol=ProtocolKind.XBPI) as bal:
        r = await bal.poll()
        await bal.tare()
        info = await bal.identify()
"""

from __future__ import annotations

import math

import pytest

from sartoriuslib import (
    Balance,
    BalanceFamily,
    BalanceState,
    Capability,
    DeviceInfo,
    ProtocolKind,
    Reading,
    SartoriusAutoprintActiveError,
    SartoriusConfirmationRequiredError,
    SartoriusError,
    SartoriusUnsupportedCommandError,
    Unit,
    open_balance,
    open_device,
)
from sartoriuslib.commands.raw import SAFE_READ_ONLY_OPCODES
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    build_sbi_identify_script,
    canned_frames,
)


def _script_with_identify(extra: dict[bytes, bytes] | None = None) -> dict[bytes, bytes]:
    """Base script — identify sequence plus any caller additions."""
    script = build_identify_script()
    if extra:
        script.update(extra)
    return script


async def _open_balance(
    script: dict[bytes, bytes] | None = None,
    *,
    identify: bool = True,
) -> tuple[Balance, FakeTransport]:
    transport = FakeTransport(script or _script_with_identify())
    balance = await open_device(
        transport,
        protocol=ProtocolKind.XBPI,
        identify=identify,
        timeout=0.1,
    )
    return balance, transport


# ---------------------------------------------------------------------------
# open_device — protocol gating + transport lifecycle.
# ---------------------------------------------------------------------------


class TestOpenDevice:
    @pytest.mark.anyio
    async def test_auto_protocol_fails_cleanly_when_silent(self) -> None:
        """``ProtocolKind.AUTO`` runs the conservative detector and surfaces
        the same "no responsive device" error as
        :func:`sartoriuslib.protocol.detect_protocol` when nothing answers."""
        transport = FakeTransport()
        with pytest.raises(SartoriusError, match="auto-detect"):
            await open_device(transport, protocol=ProtocolKind.AUTO, timeout=0.05)

    @pytest.mark.anyio
    async def test_forced_sbi_open_identifies_and_polls(self) -> None:
        script = build_sbi_identify_script(model="WZA8202-N", serial="SN123", software="2.0")
        script[b"\x1bP"] = b"+     0.00 g  \r\n"
        transport = FakeTransport(script)
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            timeout=0.1,
        )
        assert bal.info is not None
        assert bal.info.protocol is ProtocolKind.SBI
        assert bal.info.model == "WZA8202-N"
        assert bal.info.serial == "SN123"
        reading = await bal.poll()
        assert reading.protocol is ProtocolKind.SBI
        assert reading.value == 0.0
        assert reading.unit is Unit.G
        await bal.close()

    @pytest.mark.anyio
    async def test_sbi_tare_zero_and_raw(self) -> None:
        script = build_sbi_identify_script()
        script[b"\x1bP"] = b"+     1.23 g  \r\n"
        transport = FakeTransport(script)
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            timeout=0.1,
        )
        await bal.tare()
        await bal.zero()
        reply = await bal.raw_sbi("ESC P")
        assert reply.lines[0].reading is not None
        assert transport.writes[-3:] == (b"\x1bT", b"\x1bV", b"\x1bP")
        await bal.close()

    @pytest.mark.anyio
    async def test_forced_sbi_open_detects_autoprint_and_poll_consumes(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        assert bal.session.sbi_autoprint_active is True
        reading = await bal.poll()
        assert reading.value == 0.031
        assert reading.stable is False
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_forced_sbi_identify_refuses_when_autoprint_active(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        with pytest.raises(SartoriusAutoprintActiveError, match="identify"):
            await open_device(
                transport,
                protocol=ProtocolKind.SBI,
                identify=True,
                timeout=0.1,
            )

    @pytest.mark.anyio
    async def test_sbi_command_reply_refuses_when_autoprint_active(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        with pytest.raises(SartoriusAutoprintActiveError, match="command replies"):
            await bal.raw_sbi("ESC P")
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_sbi_command_reply_detects_autoprint_enabled_mid_session(self) -> None:
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        assert bool(bal.session.sbi_autoprint_active) is False
        transport.feed(b"N     +    0.031    \r\n")
        with pytest.raises(SartoriusAutoprintActiveError, match="command replies"):
            await bal.identify()
        assert bool(bal.session.sbi_autoprint_active) is True
        reading = await bal.poll()
        assert reading.value == 0.031
        assert transport.writes == (b"\x1bx1_",)
        await bal.close()

    @pytest.mark.anyio
    async def test_sbi_refresh_detects_autoprint_disabled_mid_session(self) -> None:
        transport = FakeTransport({b"\x1bP": b"+     1.23 g  \r\n"})
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        assert bool(bal.session.sbi_autoprint_active) is True
        assert await bal.refresh_sbi_autoprint_state(timeout=0.01) is False
        assert bool(bal.session.sbi_autoprint_active) is False
        reply = await bal.raw_sbi("ESC P")
        assert reply.lines[0].reading is not None
        assert reply.lines[0].reading.value == 1.23
        assert transport.writes == (b"\x1bP",)
        await bal.close()

    @pytest.mark.anyio
    async def test_sbi_refresh_keeps_autoprint_active_and_preserves_line(self) -> None:
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        transport.feed(b"N     +    0.031    \r\n")
        assert await bal.refresh_sbi_autoprint_state(timeout=0.1) is True
        assert bool(bal.session.sbi_autoprint_active) is True
        reading = await bal.poll()
        assert reading.value == 0.031
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_sbi_no_reply_command_allowed_when_autoprint_active(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"N     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        await bal.tare()
        assert transport.writes == (b"\x1bT",)
        await bal.close()

    @pytest.mark.anyio
    async def test_identify_false_skips_probe(self) -> None:
        """When identify=False, open_device doesn't touch the wire at all."""
        transport = FakeTransport()  # empty script
        bal = await open_device(
            transport,
            protocol=ProtocolKind.XBPI,
            identify=False,
            timeout=0.1,
        )
        assert bal.info is None
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_closes_transport_on_identify_failure(self) -> None:
        """If identify() times out the factory must close the transport."""
        from sartoriuslib.errors import SartoriusTimeoutError

        transport = FakeTransport()  # no replies scripted → timeout
        with pytest.raises(SartoriusTimeoutError):
            await open_device(
                transport,
                protocol=ProtocolKind.XBPI,
                identify=True,
                timeout=0.02,
            )
        assert transport.is_open is False

    @pytest.mark.anyio
    async def test_async_with_exits_close_transport(self) -> None:
        bal, transport = await _open_balance()
        async with bal:
            assert transport.is_open is True
        assert transport.is_open is False

    @pytest.mark.anyio
    async def test_aclose_is_idempotent(self) -> None:
        bal, transport = await _open_balance()
        await bal.close()
        await bal.close()
        assert transport.is_open is False

    @pytest.mark.anyio
    async def test_open_balance_alias(self) -> None:
        """``open_balance`` is a friendly alias for ``open_device``."""
        transport = FakeTransport(_script_with_identify())
        bal = await open_balance(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        assert isinstance(bal, Balance)
        await bal.close()


# ---------------------------------------------------------------------------
# Identify — composes DeviceInfo and propagates family to the session.
# ---------------------------------------------------------------------------


class TestIdentify:
    @pytest.mark.anyio
    async def test_mse_identify_classifies_cubis(self) -> None:
        bal, _ = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_READ_MODEL: canned_frames.RX_MODEL_MSE},
            )
        )
        info = bal.info
        assert info is not None
        assert info.model == "MSE1203S-100-DR"
        assert info.family is BalanceFamily.CUBIS
        assert info.manufacturer == "Sartorius"
        assert info.sbn == 0x00
        assert info.protocol is ProtocolKind.XBPI
        # Family-default capabilities seeded on the session.
        assert info.capabilities & Capability.XBPI_SUPPORT
        assert bal.session.family is BalanceFamily.CUBIS
        await bal.close()

    @pytest.mark.anyio
    async def test_wza_identify_classifies_oem(self) -> None:
        bal, _ = await _open_balance(
            build_identify_script(
                model="WZA8202-N",
            )
        )
        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.OEM_WEIGH_CELL
        await bal.close()

    @pytest.mark.anyio
    async def test_bce_identify_classifies_basic_lab(self) -> None:
        bal, _ = await _open_balance(
            build_identify_script(
                model="BCE3202-1S",
            )
        )
        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.BASIC_LAB
        await bal.close()

    @pytest.mark.anyio
    async def test_unknown_model_classifies_unknown(self) -> None:
        bal, _ = await _open_balance(
            build_identify_script(
                model="QZQ-9999",
            )
        )
        info = bal.info
        assert info is not None
        assert info.family is BalanceFamily.UNKNOWN
        await bal.close()

    @pytest.mark.anyio
    async def test_identify_reruns_overwrites_cache(self) -> None:
        """A second identify() call re-issues every identity opcode.

        Counted by TX occurrences rather than total writes so the
        assertion survives the metrology / counter probes that
        ``identify()`` runs alongside the textual primitives.
        """
        bal, transport = await _open_balance()
        info1 = bal.info
        assert info1 is not None
        info2 = await bal.identify()
        assert info2.model == info1.model
        identity_txs = (
            canned_frames.TX_READ_MODEL,
            canned_frames.TX_READ_MANUFACTURER,
            canned_frames.TX_READ_SW_VERSION,
            canned_frames.TX_READ_FACTORY_NUMBER,
            canned_frames.TX_READ_SBN,
        )
        for tx in identity_txs:
            assert transport.writes.count(tx) == 2, f"{tx.hex()} should appear twice"
        await bal.close()


# ---------------------------------------------------------------------------
# Polling / reads.
# ---------------------------------------------------------------------------


class TestReads:
    @pytest.mark.anyio
    async def test_poll_returns_reading(self) -> None:
        bal, _ = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_READ_NET: canned_frames.RX_NET_WEIGHT_EMPTY_PAN},
            )
        )
        reading: Reading = await bal.poll()
        assert reading.value is not None
        assert math.isclose(reading.value, -0.005, abs_tol=1e-6)
        assert reading.unit is Unit.G
        assert reading.stable is True
        assert reading.protocol is ProtocolKind.XBPI
        await bal.close()

    @pytest.mark.anyio
    async def test_read_net_hires_passes_resolution_arg(self) -> None:
        from sartoriuslib.protocol.xbpi import build_command

        tx_hires = build_command(0x1F, b"\x21\x01")
        bal, transport = await _open_balance(
            _script_with_identify(
                {tx_hires: canned_frames.RX_NET_WEIGHT_EMPTY_PAN},
            )
        )
        # Force HIRES capability so the prior gate doesn't trip.
        bal.session.update_identity(capabilities=Capability.HIRES_WEIGHT)
        await bal.read_net(hires=1)
        assert tx_hires in transport.writes
        await bal.close()


# ---------------------------------------------------------------------------
# Tare / zero — STATEFUL, no confirm required.
# ---------------------------------------------------------------------------


class TestTareZero:
    @pytest.mark.anyio
    async def test_tare_no_confirm_required(self) -> None:
        bal, transport = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_TARE: canned_frames.RX_ACK},
            )
        )
        await bal.tare()
        assert canned_frames.TX_TARE in transport.writes
        await bal.close()

    @pytest.mark.anyio
    async def test_zero_no_confirm_required(self) -> None:
        bal, transport = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_ZERO: canned_frames.RX_ACK},
            )
        )
        await bal.zero()
        assert canned_frames.TX_ZERO in transport.writes
        await bal.close()


# ---------------------------------------------------------------------------
# Status block.
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.anyio
    async def test_status_returns_balance_status(self) -> None:
        bal, _ = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_READ_STATUS_BLOCK: canned_frames.RX_STATUS_STABLE_CUBIS},
            )
        )
        status = await bal.status()
        assert status.state is BalanceState.STABLE
        assert status.stable is True
        await bal.close()


# ---------------------------------------------------------------------------
# raw_xbpi — safe-list vs confirm gate.
# ---------------------------------------------------------------------------


class TestRawXbpi:
    @pytest.mark.anyio
    async def test_safe_listed_opcode_runs_without_confirm(self) -> None:
        bal, _ = await _open_balance(_script_with_identify())
        # 0x71 (read_sbn) is on SAFE_READ_ONLY_OPCODES. Pre-scripted via
        # identify; running it again is just another read_sbn TX.
        assert 0x71 in SAFE_READ_ONLY_OPCODES
        frame = await bal.raw_xbpi(0x71)
        assert frame.subtype == 0x21
        await bal.close()

    @pytest.mark.anyio
    async def test_unsafe_opcode_requires_confirm(self) -> None:
        bal, transport = await _open_balance(_script_with_identify())
        # 0x14 (tare) is STATEFUL and not on the read-only safe-list.
        assert 0x14 not in SAFE_READ_ONLY_OPCODES
        writes_before = len(transport.writes)
        with pytest.raises(SartoriusConfirmationRequiredError, match="safe-list"):
            await bal.raw_xbpi(0x14)
        # Pre-I/O refusal: no write went out.
        assert len(transport.writes) == writes_before
        await bal.close()

    @pytest.mark.anyio
    async def test_unsafe_opcode_proceeds_with_confirm(self) -> None:
        bal, transport = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_TARE: canned_frames.RX_ACK},
            )
        )
        writes_before = len(transport.writes)
        frame = await bal.raw_xbpi(0x14, confirm=True)
        assert frame.subtype == 0x00  # ACK
        assert len(transport.writes) == writes_before + 1
        await bal.close()


# ---------------------------------------------------------------------------
# Prior propagation — session family starts UNKNOWN, post-identify it matches.
# ---------------------------------------------------------------------------


class TestPriorPropagation:
    @pytest.mark.anyio
    async def test_session_family_matches_identified_device(self) -> None:
        bal, _ = await _open_balance(build_identify_script(model="MSE1203S-100-DR"))
        assert bal.session.family is BalanceFamily.CUBIS
        await bal.close()

    @pytest.mark.anyio
    async def test_availability_cache_populated_by_identify(self) -> None:
        """Identify's five round trips flip their availability to SUPPORTED."""
        from sartoriuslib.devices.capability import Availability

        bal, _ = await _open_balance()
        for name in (
            "read_model",
            "read_manufacturer",
            "read_software_version",
            "read_factory_number",
            "read_sbn",
        ):
            assert bal.session.availability_of(name) is Availability.SUPPORTED
        await bal.close()


# ---------------------------------------------------------------------------
# Device error → sticky UNSUPPORTED (surfaces at Balance level too).
# ---------------------------------------------------------------------------


class TestDeviceErrors:
    @pytest.mark.anyio
    async def test_poll_unsupported_error_sticky(self) -> None:
        """xBPI error 0x04 → second poll refuses pre-I/O, no write."""
        from sartoriuslib.protocol.xbpi import checksum

        pre = bytes([0x04, 0x41, 0x01, 0x04])  # error subtype, code 0x04
        rx_err = pre + bytes([checksum(pre)])
        bal, transport = await _open_balance(
            _script_with_identify(
                {canned_frames.TX_READ_NET: rx_err},
            )
        )

        with pytest.raises(SartoriusUnsupportedCommandError):
            await bal.poll()
        writes_after_first = len(transport.writes)

        with pytest.raises(SartoriusUnsupportedCommandError, match="previously responded"):
            await bal.poll()
        assert len(transport.writes) == writes_after_first
        await bal.close()


# ---------------------------------------------------------------------------
# DeviceInfo shape.
# ---------------------------------------------------------------------------


class TestDeviceInfoShape:
    @pytest.mark.anyio
    async def test_device_info_is_immutable(self) -> None:
        bal, _ = await _open_balance()
        info = bal.info
        assert isinstance(info, DeviceInfo)
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            info.model = "changed"  # type: ignore[misc]
        await bal.close()
