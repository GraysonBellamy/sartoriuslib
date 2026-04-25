"""Tests for the remaining Phase-4 command specs:
tare / zero, status-block, identity primitives, and raw safe-list.
"""

from __future__ import annotations

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.identity import (
    READ_FACTORY_NUMBER,
    READ_MANUFACTURER,
    READ_MODEL,
    READ_OEM_TEXT,
    READ_SBN,
    READ_SW_VERSION,
    IdentityRequest,
)
from sartoriuslib.commands.raw import SAFE_READ_ONLY_OPCODES
from sartoriuslib.commands.status import STATUS_BLOCK, StatusRequest
from sartoriuslib.commands.tare import TARE, ZERO, TareRequest
from sartoriuslib.devices.capability import SafetyTier
from sartoriuslib.devices.models import BalanceState, BalanceStatus
from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi import parse_reply
from sartoriuslib.protocol.xbpi import build_command, checksum, parse_frame


def _rx(subtype: int, body: bytes) -> bytes:
    """Build a well-formed RX frame for tests.

    ``length`` = marker + subtype + body + chk (everything past the len
    byte). See ``docs/protocol.md`` §3.1.
    """
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


# ---------------------------------------------------------------------------
# Tare / zero — STATEFUL, return None on ACK.
# ---------------------------------------------------------------------------


class TestTareZero:
    def test_tare_opcode_0x14(self, ctx: CommandContext) -> None:
        assert TARE.xbpi is not None
        assert TARE.xbpi.encode(ctx, TareRequest()) == build_command(0x14)
        assert TARE.safety is SafetyTier.STATEFUL

    def test_zero_opcode_0x18(self, ctx: CommandContext) -> None:
        assert ZERO.xbpi is not None
        assert ZERO.xbpi.encode(ctx, TareRequest()) == build_command(0x18)
        assert ZERO.safety is SafetyTier.STATEFUL

    def test_decode_ack_returns_none(self, ctx: CommandContext) -> None:
        ack = bytes.fromhex("03410044")
        frame = parse_frame(ack)
        assert TARE.xbpi is not None
        assert TARE.xbpi.decode(frame, ctx) is None

    def test_sbi_tare_and_zero_have_no_reply(self) -> None:
        sbi_ctx = CommandContext(protocol=ProtocolKind.SBI)
        assert TARE.sbi is not None
        assert ZERO.sbi is not None
        assert TARE.sbi.encode(sbi_ctx, TareRequest()) == b"\x1bT"
        assert ZERO.sbi.encode(sbi_ctx, TareRequest()) == b"\x1bV"
        assert TARE.sbi.expect_lines == 0
        assert ZERO.sbi.expect_lines == 0


# ---------------------------------------------------------------------------
# Status block (0x30 → BalanceStatus).
# ---------------------------------------------------------------------------


class TestStatusBlock:
    def test_encode_opcode_0x30(self, ctx: CommandContext) -> None:
        assert STATUS_BLOCK.xbpi is not None
        assert STATUS_BLOCK.xbpi.encode(ctx, StatusRequest()) == build_command(0x30)

    def test_decode_cubis_stable(self, ctx: CommandContext) -> None:
        """MSE stable: state=0x88, status=0x18. See docs/protocol.md §8.2."""
        rx = _rx(0x48, bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42]))
        frame = parse_frame(rx)
        assert STATUS_BLOCK.xbpi is not None
        s = STATUS_BLOCK.xbpi.decode(frame, ctx)
        assert isinstance(s, BalanceStatus)
        assert s.state is BalanceState.STABLE
        assert s.stable is True
        assert s.adc_trusted is True
        assert s.isocal_due is True
        assert s.sequence == 0x42

    def test_decode_overload(self, ctx: CommandContext) -> None:
        rx = _rx(0x48, bytes([0x00, 0x00, 0x81, 0x82, 0x00, 0x10, 0x00, 0x01]))
        frame = parse_frame(rx)
        assert STATUS_BLOCK.xbpi is not None
        s = STATUS_BLOCK.xbpi.decode(frame, ctx)
        assert s.state is BalanceState.OVERLOAD

    def test_sbi_status_from_weight_line(self) -> None:
        assert STATUS_BLOCK.sbi is not None
        status = STATUS_BLOCK.sbi.decode(
            parse_reply(b"+     0.00 g  \r\n"),
            CommandContext(protocol=ProtocolKind.SBI),
        )
        assert status.state is BalanceState.STABLE
        assert status.isocal_due is None


# ---------------------------------------------------------------------------
# Identity primitives — ascii blobs + raw blobs + SBN.
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_read_model_opcode_0x02(self, ctx: CommandContext) -> None:
        assert READ_MODEL.xbpi is not None
        assert READ_MODEL.xbpi.encode(ctx, IdentityRequest()) == build_command(0x02)

    def test_sbi_identity_tokens(self) -> None:
        sbi_ctx = CommandContext(protocol=ProtocolKind.SBI)
        assert READ_MODEL.sbi is not None
        assert READ_SW_VERSION.sbi is not None
        assert READ_FACTORY_NUMBER.sbi is not None
        assert READ_MODEL.sbi.encode(sbi_ctx, IdentityRequest()) == b"\x1bx1_"
        assert READ_FACTORY_NUMBER.sbi.encode(sbi_ctx, IdentityRequest()) == b"\x1bx2_"
        assert READ_SW_VERSION.sbi.encode(sbi_ctx, IdentityRequest()) == b"\x1bx3_"

    def test_sbi_identity_decode(self) -> None:
        assert READ_MODEL.sbi is not None
        assert (
            READ_MODEL.sbi.decode(
                parse_reply(b"WZA8202-N\r\n"),
                CommandContext(protocol=ProtocolKind.SBI),
            )
            == "WZA8202-N"
        )

    def test_sbi_model_decode_rejects_autoprint_weight(self) -> None:
        assert READ_MODEL.sbi is not None
        with pytest.raises(SartoriusParseError, match="identity text"):
            READ_MODEL.sbi.decode(
                parse_reply(b"N     +    0.031    \r\n"),
                CommandContext(protocol=ProtocolKind.SBI),
            )

    def test_read_manufacturer_opcode_0x07(self, ctx: CommandContext) -> None:
        assert READ_MANUFACTURER.xbpi is not None
        assert READ_MANUFACTURER.xbpi.encode(ctx, IdentityRequest()) == build_command(0x07)

    def test_read_oem_text_opcode_0x05(self, ctx: CommandContext) -> None:
        assert READ_OEM_TEXT.xbpi is not None
        assert READ_OEM_TEXT.xbpi.encode(ctx, IdentityRequest()) == build_command(0x05)

    def test_decode_ascii_blob_strips_null_padding(self, ctx: CommandContext) -> None:
        # 20-byte ASCII model, null-padded.
        name = "MSE1203S-100-DR"
        body = name.encode("ascii").ljust(20, b"\x00")
        frame = parse_frame(_rx(0x54, body))
        assert READ_MODEL.xbpi is not None
        assert READ_MODEL.xbpi.decode(frame, ctx) == "MSE1203S-100-DR"

    def test_decode_software_version_is_raw_bytes(self, ctx: CommandContext) -> None:
        body = bytes([0x00, 0x39, 0x21, 0x00, 0x39, 0x01, 0x39, 0x01, 0x00, 0x01])
        frame = parse_frame(_rx(0x4A, body))
        assert READ_SW_VERSION.xbpi is not None
        assert READ_SW_VERSION.xbpi.decode(frame, ctx) == body

    def test_decode_factory_number_is_raw_bytes(self, ctx: CommandContext) -> None:
        body = bytes([0x00, 0x31, 0x80, 0x11, 0x65])
        frame = parse_frame(_rx(0x45, body))
        assert READ_FACTORY_NUMBER.xbpi is not None
        assert READ_FACTORY_NUMBER.xbpi.decode(frame, ctx) == body

    def test_read_sbn_decode(self, ctx: CommandContext) -> None:
        frame = parse_frame(_rx(0x21, b"\x05"))
        assert READ_SBN.xbpi is not None
        assert READ_SBN.xbpi.decode(frame, ctx) == 0x05


# ---------------------------------------------------------------------------
# Raw safe-list — every listed opcode should be ≤ 0xFF.
# ---------------------------------------------------------------------------


class TestRawSafeList:
    def test_nonempty(self) -> None:
        assert len(SAFE_READ_ONLY_OPCODES) > 0

    def test_all_are_single_bytes(self) -> None:
        for op in SAFE_READ_ONLY_OPCODES:
            assert 0 <= op <= 0xFF

    def test_includes_core_reads(self) -> None:
        for op in (0x02, 0x07, 0x1E, 0x20, 0x22, 0x30, 0x32, 0x55, 0x71, 0xBA):
            assert op in SAFE_READ_ONLY_OPCODES

    def test_excludes_known_destructive_opcodes(self) -> None:
        """Write / reset / calibration-init opcodes must NOT be safe-listed."""
        for op in (0x14, 0x18, 0x28, 0x46, 0x47, 0x56, 0x58, 0x5C, 0x72):
            assert op not in SAFE_READ_ONLY_OPCODES
