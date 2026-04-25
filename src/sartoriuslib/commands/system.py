"""System commands — config counter, save/reload menu.

xBPI opcodes per ``docs/protocol.md`` §7.8 and §7.11:

- ``0xBA`` config_generation_counter: u8 register that increments on
  most runtime-config changes. The cache-invalidation signal that
  powers :class:`Session`'s result cache. ``READ_ONLY``.
- ``0x47`` save_menu_to_eeprom: persist current menu to EEPROM.
  ``PERSISTENT`` — requires ``confirm=True``.
- ``0x46`` read_menu_from_eeprom: reload saved menu from EEPROM.
  ``PERSISTENT`` — requires ``confirm=True``.

The ``0xBA`` caveat from ``docs/protocol.md`` §10.1 and design doc
§6.3: not every persistent write ticks the counter (``p13`` / ``p50``
are known exceptions). The cache treats those writes as
cache-invalidating regardless — see :class:`ParameterSpec` and the
session's invalidation hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.errors import ErrorContext, SartoriusParseError
from sartoriuslib.protocol.xbpi import build_command

if TYPE_CHECKING:
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "CONFIG_COUNTER",
    "RELOAD_MENU",
    "SAVE_MENU",
    "SystemRequest",
]


@dataclass(frozen=True, slots=True)
class SystemRequest:
    """No-arg request shared by ``0xBA`` / ``0x47`` / ``0x46``."""


@dataclass(frozen=True, slots=True)
class _ConfigCounterVariant(XbpiVariant[SystemRequest, int]):
    opcode: int = 0xBA

    def encode(self, ctx: CommandContext, request: SystemRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> int:
        if len(reply.body) != 1:
            raise SartoriusParseError(
                f"config_counter body must be 1 byte, got {len(reply.body)}",
                context=ErrorContext(
                    command_name="config_counter",
                    opcode=self.opcode,
                    raw_response=reply.raw,
                ),
            )
        return reply.body[0]


@dataclass(frozen=True, slots=True)
class _MenuAckVariant(XbpiVariant[SystemRequest, None]):
    """Shared variant for ``0x47`` save-menu and ``0x46`` reload-menu."""

    opcode: int

    def encode(self, ctx: CommandContext, request: SystemRequest) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> None:
        # ACK reply — protocol client validated subtype.
        return None


CONFIG_COUNTER = Command[SystemRequest, int](
    name="config_counter",
    xbpi=_ConfigCounterVariant(),
    capability_hints=Capability.CONFIG_COUNTER,
    safety=SafetyTier.READ_ONLY,
)

SAVE_MENU = Command[SystemRequest, None](
    name="save_menu",
    xbpi=_MenuAckVariant(opcode=0x47),
    safety=SafetyTier.PERSISTENT,
)

RELOAD_MENU = Command[SystemRequest, None](
    name="reload_menu",
    xbpi=_MenuAckVariant(opcode=0x46),
    safety=SafetyTier.PERSISTENT,
)
