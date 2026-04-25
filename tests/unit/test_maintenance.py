"""``sartoriuslib.maintenance`` — port-level one-shot helpers.

These helpers wrap :meth:`Balance.configure_protocol`,
:meth:`Balance.set_baud_rate`, and :meth:`Balance.write_sbn_address`
behind a lifecycle that opens, runs the operation, returns the
post-change identity, and closes — for callers who don't want to hold
a live :class:`Balance` session.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusConfirmationRequiredError,
)
from sartoriuslib.maintenance import (
    set_baud_rate,
    switch_protocol,
    write_sbn_address,
)
from sartoriuslib.protocol.sbi import LINE_TERMINATOR, TOKEN_SERIAL, TOKEN_SOFTWARE, TOKEN_TYPE
from sartoriuslib.protocol.xbpi import build_command, encode_tlv
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
    canned_frames,
)


def _sbi_line(text: str) -> bytes:
    return text.encode("ascii") + LINE_TERMINATOR


def _set_baud_tx(wire_code: int) -> bytes:
    return build_command(0x5C, encode_tlv(0x21, wire_code))


def _write_sbn_tx(sbn: int) -> bytes:
    return build_command(0x72, encode_tlv(0x21, sbn))


class TestSwitchProtocol:
    @pytest.mark.anyio
    async def test_xbpi_to_sbi_returns_post_switch_identity(self) -> None:
        script: dict[bytes, bytes] = build_identify_script()
        script.update(
            {
                TOKEN_TYPE: _sbi_line("WZA8202-N"),
                TOKEN_SERIAL: _sbi_line("12345678"),
                TOKEN_SOFTWARE: _sbi_line("1.0"),
            },
        )
        transport = FakeTransport(script)

        info = await switch_protocol(
            transport,
            ProtocolKind.SBI,
            current_protocol=ProtocolKind.XBPI,
            new_baudrate=1200,
            timeout=0.1,
        )

        assert info.protocol is ProtocolKind.SBI
        assert info.model == "WZA8202-N"
        assert transport.last_reopen_baud == 1200
        # Helper closes the transport before returning.
        assert not transport.is_open

    @pytest.mark.anyio
    async def test_requires_confirm_true(self) -> None:
        transport = FakeTransport(build_identify_script())
        with pytest.raises(SartoriusConfirmationRequiredError, match="DANGEROUS"):
            await switch_protocol(
                transport,
                ProtocolKind.SBI,
                current_protocol=ProtocolKind.XBPI,
                new_baudrate=1200,
                timeout=0.05,
                confirm=False,
            )
        # Even on the gate failure, the helper closes the port behind itself.
        assert not transport.is_open


class TestSetBaudRate:
    @pytest.mark.anyio
    async def test_send_then_reopen_and_close(self) -> None:
        script: dict[bytes, bytes] = build_identify_script()
        script[_set_baud_tx(0x01)] = canned_frames.RX_ACK
        transport = FakeTransport(script)

        info = await set_baud_rate(
            transport,
            wire_code=0x01,
            baudrate=19200,
            timeout=0.1,
        )

        assert _set_baud_tx(0x01) in transport.writes
        assert transport.last_reopen_baud == 19200
        assert info.model == "MSE1203S-100-DR"
        assert not transport.is_open


class TestWriteSbnAddress:
    @pytest.mark.anyio
    async def test_writes_then_reads_back(self) -> None:
        new_sbn = 0x05
        script: dict[bytes, bytes] = build_identify_script(sbn=new_sbn)
        script[_write_sbn_tx(new_sbn)] = canned_frames.RX_ACK
        transport = FakeTransport(script)

        readback = await write_sbn_address(
            transport,
            new_sbn,
            timeout=0.1,
        )

        assert readback == new_sbn
        assert _write_sbn_tx(new_sbn) in transport.writes
        assert not transport.is_open
