"""Tests for :mod:`sartoriuslib.commands.system` (``0xBA``/``0x47``/``0x46``)."""

from __future__ import annotations

import pytest

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.system import (
    CONFIG_COUNTER,
    RELOAD_MENU,
    SAVE_MENU,
    SystemRequest,
)
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.errors import SartoriusParseError
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import build_command, checksum, parse_frame


def _rx(subtype: int, body: bytes) -> bytes:
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


@pytest.fixture
def ctx() -> CommandContext:
    return CommandContext(protocol=ProtocolKind.XBPI)


class TestConfigCounter:
    def test_encode_no_args(self, ctx: CommandContext) -> None:
        assert CONFIG_COUNTER.xbpi is not None
        assert CONFIG_COUNTER.xbpi.encode(ctx, SystemRequest()) == build_command(0xBA)

    def test_decode_counter_value(self, ctx: CommandContext) -> None:
        """Body is one u8 in ``short_data`` subtype ``0x21``."""
        rx = _rx(subtype=0x21, body=b"\x2a")
        frame = parse_frame(rx)
        assert CONFIG_COUNTER.xbpi is not None
        assert CONFIG_COUNTER.xbpi.decode(frame, ctx) == 0x2A

    def test_decode_cold_boot_value(self, ctx: CommandContext) -> None:
        """Cold boot resets ``0xBA`` to 1 (``docs/protocol.md`` §7.11)."""
        rx = _rx(subtype=0x21, body=b"\x01")
        frame = parse_frame(rx)
        assert CONFIG_COUNTER.xbpi is not None
        assert CONFIG_COUNTER.xbpi.decode(frame, ctx) == 1

    def test_decode_rejects_wrong_length(self, ctx: CommandContext) -> None:
        rx = _rx(subtype=0x21, body=b"\x01\x02")
        frame = parse_frame(rx)
        assert CONFIG_COUNTER.xbpi is not None
        with pytest.raises(SartoriusParseError, match="1 byte"):
            CONFIG_COUNTER.xbpi.decode(frame, ctx)


class TestSaveReloadMenu:
    def test_save_menu_opcode_0x47(self, ctx: CommandContext) -> None:
        assert SAVE_MENU.xbpi is not None
        assert SAVE_MENU.xbpi.encode(ctx, SystemRequest()) == build_command(0x47)

    def test_reload_menu_opcode_0x46(self, ctx: CommandContext) -> None:
        assert RELOAD_MENU.xbpi is not None
        assert RELOAD_MENU.xbpi.encode(ctx, SystemRequest()) == build_command(0x46)

    def test_decode_ack_returns_none(self, ctx: CommandContext) -> None:
        rx = _rx(subtype=0x00, body=b"")
        frame = parse_frame(rx)
        assert SAVE_MENU.xbpi is not None
        assert RELOAD_MENU.xbpi is not None
        assert SAVE_MENU.xbpi.decode(frame, ctx) is None
        assert RELOAD_MENU.xbpi.decode(frame, ctx) is None


class TestMetadata:
    def test_capability_hints(self) -> None:
        assert CONFIG_COUNTER.capability_hints == Capability.CONFIG_COUNTER
        # Save/reload menu are generic — no capability prior.
        assert SAVE_MENU.capability_hints == Capability(0)
        assert RELOAD_MENU.capability_hints == Capability(0)

    def test_safety_tiers(self) -> None:
        assert CONFIG_COUNTER.safety is SafetyTier.READ_ONLY
        assert SAVE_MENU.safety is SafetyTier.PERSISTENT
        assert RELOAD_MENU.safety is SafetyTier.PERSISTENT
