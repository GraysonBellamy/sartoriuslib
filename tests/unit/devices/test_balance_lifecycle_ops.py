"""``Balance.set_baud_rate`` / ``Balance.write_sbn_address`` —
xBPI maintenance opcodes 0x5C and 0x72.

Per ``docs/protocol.md`` §7.10:

- ``0x5C`` set_baud_rate — DANGEROUS, changes serial comms mid-flight.
  Wire codes ``0x00=9600``, ``0x01=19200``, ``0x02=38400``, ``0x03=57600``.
- ``0x72`` write_sbn_address — DANGEROUS. Multidrop bus address change.

Both refuse without ``confirm=True`` (DANGEROUS tier) and refuse on an
SBI session (xBPI-only opcodes).
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusConfirmationRequiredError,
    SartoriusValidationError,
    open_device,
)
from sartoriuslib.protocol.xbpi import build_command, encode_tlv
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    canned_frames,
)


def _xbpi_script_with_sbn(sbn: int) -> dict[bytes, bytes]:
    """Identify script where READ_SBN reports ``sbn``."""
    script = build_identify_script(sbn=sbn)
    return script


def _set_baud_tx(wire_code: int) -> bytes:
    return build_command(0x5C, encode_tlv(0x21, wire_code))


def _write_sbn_tx(sbn: int) -> bytes:
    return build_command(0x72, encode_tlv(0x21, sbn))


class TestSetBaudRateGate:
    @pytest.mark.anyio
    async def test_requires_confirm_true(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            with pytest.raises(SartoriusConfirmationRequiredError, match="DANGEROUS"):
                await bal.set_baud_rate(0x01, baudrate=19200)
        finally:
            await bal.close()

    @pytest.mark.anyio
    async def test_rejects_out_of_range_wire_code(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            with pytest.raises(SartoriusValidationError, match="wire_code"):
                await bal.set_baud_rate(-1, baudrate=19200, confirm=True)
            with pytest.raises(SartoriusValidationError, match="wire_code"):
                await bal.set_baud_rate(0x100, baudrate=19200, confirm=True)
        finally:
            await bal.close()


class TestSetBaudRateSuccess:
    @pytest.mark.anyio
    async def test_writes_0x5c_then_reopens_and_verifies(self) -> None:
        """Successful sequence: send ``0x5C`` ACK at old baud, reopen at
        new baud, verify with READ_MODEL, refresh DeviceInfo."""
        script: dict[bytes, bytes] = build_identify_script()
        script[_set_baud_tx(0x01)] = canned_frames.RX_ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            new_info = await bal.set_baud_rate(0x01, baudrate=19200, confirm=True)
        finally:
            await bal.close()

        # The 0x5C frame went out at the old baud.
        assert _set_baud_tx(0x01) in transport.writes
        # Transport reopened to the new baud.
        assert transport.last_reopen_baud == 19200
        # Identity refreshed post-switch.
        assert new_info.model == "MSE1203S-100-DR"


class TestWriteSbnAddressGate:
    @pytest.mark.anyio
    async def test_requires_confirm_true(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            with pytest.raises(SartoriusConfirmationRequiredError, match="DANGEROUS"):
                await bal.write_sbn_address(0x05)
        finally:
            await bal.close()

    @pytest.mark.anyio
    async def test_rejects_out_of_range_sbn(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            with pytest.raises(SartoriusValidationError, match="sbn"):
                await bal.write_sbn_address(-1, confirm=True)
            with pytest.raises(SartoriusValidationError, match="sbn"):
                await bal.write_sbn_address(0x100, confirm=True)
        finally:
            await bal.close()


class TestWriteSbnAddressSuccess:
    @pytest.mark.anyio
    async def test_writes_0x72_and_reads_back_via_0x71(self) -> None:
        new_sbn = 0x05
        script: dict[bytes, bytes] = _xbpi_script_with_sbn(new_sbn)
        script[_write_sbn_tx(new_sbn)] = canned_frames.RX_ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            readback = await bal.write_sbn_address(new_sbn, confirm=True)
        finally:
            await bal.close()

        assert readback == new_sbn
        # Both the write and the readback (via READ_SBN, opcode 0x71) ran.
        assert _write_sbn_tx(new_sbn) in transport.writes
        assert canned_frames.TX_READ_SBN in transport.writes

    @pytest.mark.anyio
    async def test_update_session_dst_changes_destination_sbn(self) -> None:
        new_sbn = 0x05
        script: dict[bytes, bytes] = _xbpi_script_with_sbn(new_sbn)
        script[_write_sbn_tx(new_sbn)] = canned_frames.RX_ACK
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            assert bal.session.dst_sbn == 0x09  # factory default
            await bal.write_sbn_address(
                new_sbn,
                update_session_dst=True,
                confirm=True,
            )
            assert bal.session.dst_sbn == new_sbn
        finally:
            await bal.close()
