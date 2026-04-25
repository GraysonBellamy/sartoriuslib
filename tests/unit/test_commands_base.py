"""Tests for :mod:`sartoriuslib.commands.base` — the spec dataclasses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from typing import TYPE_CHECKING

import pytest

from sartoriuslib.commands.base import Command, CommandContext, SbiVariant, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.kind import BalanceFamily
from sartoriuslib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.types import XbpiFrame


class TestCommandContext:
    def test_defaults_match_protocol_conventions(self) -> None:
        ctx = CommandContext(protocol=ProtocolKind.XBPI)
        assert ctx.src_sbn == 0x01
        assert ctx.dst_sbn == 0x09
        assert ctx.firmware is None
        assert ctx.family is BalanceFamily.UNKNOWN

    def test_is_frozen(self) -> None:
        ctx = CommandContext(protocol=ProtocolKind.XBPI)
        with pytest.raises(FrozenInstanceError):
            ctx.src_sbn = 0x02  # type: ignore[misc]


class TestXbpiVariantContract:
    def test_cannot_instantiate_abstract_base(self) -> None:
        with pytest.raises(TypeError):
            XbpiVariant()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        @dataclass(frozen=True, slots=True)
        class Ping(XbpiVariant[bytes, int]):
            opcode: int = 0x71

            def encode(self, ctx: CommandContext, request: bytes) -> bytes:
                return bytes([self.opcode]) + request

            def decode(self, reply: XbpiFrame, ctx: CommandContext) -> int:
                return reply.body[0]

        p = Ping()
        assert p.opcode == 0x71
        assert p.encode(CommandContext(protocol=ProtocolKind.XBPI), b"\x00") == b"\x71\x00"


class TestSbiVariantContract:
    def test_cannot_instantiate_abstract_base(self) -> None:
        with pytest.raises(TypeError):
            SbiVariant()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        @dataclass(frozen=True, slots=True)
        class Print(SbiVariant[None, str]):
            token: bytes = b"\x1bP"

            def encode(self, ctx: CommandContext, request: None) -> bytes:
                return self.token

            def decode(self, reply: SbiReply, ctx: CommandContext) -> str:
                return reply.raw.decode("ascii", errors="replace")

        p = Print()
        assert p.token == b"\x1bP"
        assert p.encode(CommandContext(protocol=ProtocolKind.SBI), None) == b"\x1bP"


class TestCommandDataclass:
    def test_defaults(self) -> None:
        cmd = Command[bytes, int](name="x")
        assert cmd.xbpi is None
        assert cmd.sbi is None
        assert cmd.family_hints == frozenset()
        assert cmd.capability_hints == Capability(0)
        assert cmd.safety is SafetyTier.READ_ONLY
        assert cmd.min_firmware is None
        assert cmd.max_firmware is None

    def test_is_frozen(self) -> None:
        cmd = Command[bytes, int](name="x")
        with pytest.raises(FrozenInstanceError):
            cmd.name = "y"  # type: ignore[misc]

    def test_family_hints_frozen(self) -> None:
        cmd = Command[bytes, int](
            name="x",
            family_hints=frozenset({BalanceFamily.CUBIS}),
        )
        assert BalanceFamily.CUBIS in cmd.family_hints
