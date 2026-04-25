"""Metrology commands — capacity, increment, temperature.

xBPI opcodes per ``docs/protocol.md`` §7.2 and §9:

- ``0x0C`` read_max          → typed_float (``Quantity``), TLV-21 area arg
- ``0x0D`` read_increment    → typed_float (``Quantity``), TLV-21 area arg
- ``0x76`` read_temperature  → typed_float (``TemperatureReading``), TLV-21 sensor arg

Capacity and increment are reported in the balance's *current display
unit*; the opcode reply does not carry a self-describing unit byte
(contrast the 8-byte measurement body's byte [6]). The typed results
here therefore use :attr:`Unit.UNKNOWN` — callers who need a concrete
unit read the display-unit parameter (``p07``) separately, or inspect
``Reading.unit`` from :meth:`Balance.poll`. This is more honest than
guessing grams.

Temperature replies use the sentinel ``7f ff ff ff`` for "sensor not
installed" (``docs/protocol.md`` §9). The :class:`TemperatureReading`
decode maps that to ``celsius=None`` instead of ``NaN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import Command, CommandContext, XbpiVariant
from sartoriuslib.devices.capability import Capability, SafetyTier
from sartoriuslib.devices.models import Quantity, TemperatureReading
from sartoriuslib.protocol.xbpi import (
    build_command,
    decode_typed_float_body,
    encode_tlv,
)
from sartoriuslib.registry.units import Unit

if TYPE_CHECKING:
    from sartoriuslib.protocol.xbpi.types import XbpiFrame

__all__ = [
    "READ_CAPACITY",
    "READ_INCREMENT",
    "READ_TEMPERATURE",
    "TEMPERATURE_SENTINEL",
    "MetrologyRequest",
    "TemperatureRequest",
]


#: Bytes [0..3] of the typed-float body when a sensor is not installed.
#: See ``docs/protocol.md`` §9.
TEMPERATURE_SENTINEL: bytes = b"\x7f\xff\xff\xff"


@dataclass(frozen=True, slots=True)
class MetrologyRequest:
    """Capacity / increment request.

    ``area`` selects the weighing range on multi-range balances;
    single-range units (all currently captured) report the same value
    regardless. Defaults to ``0`` because that is the only area every
    balance is known to accept.
    """

    area: int = 0


@dataclass(frozen=True, slots=True)
class TemperatureRequest:
    """Per-sensor temperature request (TLV-21 sensor index)."""

    sensor: int = 0


# ---------------------------------------------------------------------------
# Capacity / increment — shared typed_float decode.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _MetrologyVariant(XbpiVariant[MetrologyRequest, Quantity]):
    """Shared variant for capacity (``0x0C``) and increment (``0x0D``).

    Both ship the same wire shape: ``[opcode][TLV-21 area]`` in,
    5-byte ``typed_float`` body out. Unit is :attr:`Unit.UNKNOWN`
    because the opcode reply carries no unit byte.
    """

    opcode: int

    def encode(self, ctx: CommandContext, request: MetrologyRequest) -> bytes:
        return build_command(
            self.opcode,
            encode_tlv(0x21, request.area),
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> Quantity:
        body = decode_typed_float_body(reply.body)
        return Quantity(value=body.value, unit=Unit.UNKNOWN)


READ_CAPACITY = Command[MetrologyRequest, Quantity](
    name="read_capacity",
    xbpi=_MetrologyVariant(opcode=0x0C),
    safety=SafetyTier.READ_ONLY,
    parameterized=True,
)

READ_INCREMENT = Command[MetrologyRequest, Quantity](
    name="read_increment",
    xbpi=_MetrologyVariant(opcode=0x0D),
    safety=SafetyTier.READ_ONLY,
    parameterized=True,
)


# ---------------------------------------------------------------------------
# Temperature — typed_float with sentinel check.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _TemperatureVariant(XbpiVariant[TemperatureRequest, TemperatureReading]):
    opcode: int = 0x76

    def encode(self, ctx: CommandContext, request: TemperatureRequest) -> bytes:
        return build_command(
            self.opcode,
            encode_tlv(0x21, request.sensor),
            src_sbn=ctx.src_sbn,
            dst_sbn=ctx.dst_sbn,
        )

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> TemperatureReading:
        # Keep the caller's sensor index in the response — the reply
        # body doesn't echo it, so we pull it from the request via
        # the context's command history. For now, we don't have that
        # channel, so the caller sets it on the returned object.
        # Practical path: the Balance facade stashes the requested
        # sensor and the variant decodes bytes only.
        body = bytes(reply.body)
        if body[0:4] == TEMPERATURE_SENTINEL:
            celsius: float | None = None
        else:
            celsius = decode_typed_float_body(body).value
        return TemperatureReading(sensor=-1, celsius=celsius, raw=body)


READ_TEMPERATURE = Command[TemperatureRequest, TemperatureReading](
    name="read_temperature",
    xbpi=_TemperatureVariant(),
    capability_hints=Capability.TEMPERATURE_SENSORS,
    safety=SafetyTier.READ_ONLY,
    parameterized=True,
)
