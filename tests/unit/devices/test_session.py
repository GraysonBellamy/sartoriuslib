"""Tests for :class:`sartoriuslib.devices.session.Session`.

Covers the gate stack (design doc §6.1) and availability-cache
behaviour (§6.1.1). Session dispatches through a real
:class:`XbpiProtocolClient` wired to a :class:`FakeTransport`, so these
are end-to-end over the codec + client, not mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anyio
import pytest

from sartoriuslib.commands.base import NO_CAPABILITY, Command, CommandContext, XbpiVariant
from sartoriuslib.devices.capability import Availability, Capability, SafetyTier
from sartoriuslib.devices.kind import BalanceFamily
from sartoriuslib.devices.session import Session
from sartoriuslib.errors import (
    SartoriusCapabilityError,
    SartoriusCapabilityWarning,
    SartoriusConfirmationRequiredError,
    SartoriusError,
    SartoriusOperationNotApplicableError,
    SartoriusProtocolUnsupportedError,
    SartoriusTimeoutError,
    SartoriusUnsupportedCommandError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.xbpi import (
    XbpiFrame,
    XbpiProtocolClient,
    build_command,
    checksum,
)
from sartoriuslib.transport import FakeTransport

# ---------------------------------------------------------------------------
# Test helpers: a tiny set of fake command variants.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NoReq:
    """Trivial request type for variants that take no args."""


@dataclass(frozen=True, slots=True)
class _Sbn(XbpiVariant[_NoReq, int]):
    """xBPI opcode 0x71 read_sbn — decodes to the SBN integer."""

    opcode: int = 0x71

    def encode(self, ctx: CommandContext, request: _NoReq) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> int:
        return reply.body[0]


@dataclass(frozen=True, slots=True)
class _TareOp(XbpiVariant[_NoReq, None]):
    """xBPI opcode 0x14 tare — no reply payload."""

    opcode: int = 0x14

    def encode(self, ctx: CommandContext, request: _NoReq) -> bytes:
        return build_command(self.opcode, src_sbn=ctx.src_sbn, dst_sbn=ctx.dst_sbn)

    def decode(self, reply: XbpiFrame, ctx: CommandContext) -> None:
        return None


def _rx(subtype: int, body: bytes) -> bytes:
    """Synthesize a balance→host RX frame (length + marker + subtype + body + chk)."""
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


READ_SBN = Command[_NoReq, int](
    name="read_sbn",
    xbpi=_Sbn(),
    safety=SafetyTier.READ_ONLY,
)

TARE = Command[_NoReq, None](
    name="tare",
    xbpi=_TareOp(),
    safety=SafetyTier.STATEFUL,
)

WRITE_PARAM = Command[_NoReq, None](
    name="write_parameter",
    xbpi=_TareOp(),  # reuse encode/decode shape; opcode value doesn't matter for gate tests
    safety=SafetyTier.PERSISTENT,
)

BAUD_CHANGE = Command[_NoReq, None](
    name="set_baud_rate",
    xbpi=_TareOp(),
    safety=SafetyTier.DANGEROUS,
)

SBI_ONLY = Command[_NoReq, None](
    name="sbi_only_cmd",
    xbpi=None,
    sbi=None,  # structurally: SBI variant not filled in yet
    safety=SafetyTier.READ_ONLY,
)

CUBIS_ONLY = Command[_NoReq, int](
    name="cubis_only_cmd",
    xbpi=_Sbn(),
    family_hints=frozenset({BalanceFamily.CUBIS}),
    safety=SafetyTier.READ_ONLY,
)

NEEDS_HIRES = Command[_NoReq, int](
    name="hires_cmd",
    xbpi=_Sbn(),
    capability_hints=Capability.HIRES_WEIGHT,
    safety=SafetyTier.READ_ONLY,
)


async def _xbpi_session(
    script: dict[bytes, bytes],
    *,
    strict: bool = False,
    family: BalanceFamily = BalanceFamily.UNKNOWN,
    capabilities: Capability = NO_CAPABILITY,
) -> tuple[Session, FakeTransport]:
    transport = FakeTransport(script)
    await transport.open()
    client = XbpiProtocolClient(transport, default_timeout=0.1)
    session = Session(
        xbpi_client=client,
        active_protocol=ProtocolKind.XBPI,
        family=family,
        capabilities=capabilities,
        strict=strict,
        default_timeout=0.1,
    )
    return session, transport


# ---------------------------------------------------------------------------
# Construction.
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_auto_rejected(self) -> None:
        with pytest.raises(SartoriusError, match="AUTO"):
            Session(active_protocol=ProtocolKind.AUTO)


# ---------------------------------------------------------------------------
# Happy path — encode → wire → decode.
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.anyio
    async def test_read_sbn_round_trip(self) -> None:
        tx = build_command(0x71)
        session, _ = await _xbpi_session({tx: _rx(0x21, b"\x00")})
        result = await session.execute(READ_SBN, _NoReq())
        assert result == 0x00
        assert session.availability_of("read_sbn") is Availability.SUPPORTED

    @pytest.mark.anyio
    async def test_tare_ack_returns_none(self) -> None:
        tx = build_command(0x14)
        session, _ = await _xbpi_session({tx: _rx(0x00, b"")})
        await session.execute(TARE, _NoReq())
        assert session.availability_of("tare") is Availability.SUPPORTED


# ---------------------------------------------------------------------------
# Safety gate (§6.1.1).
# ---------------------------------------------------------------------------


class TestSafetyGate:
    @pytest.mark.anyio
    async def test_persistent_without_confirm_raises_pre_io(self) -> None:
        session, transport = await _xbpi_session({})
        with pytest.raises(SartoriusConfirmationRequiredError):
            await session.execute(WRITE_PARAM, _NoReq())
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_dangerous_without_confirm_raises_pre_io(self) -> None:
        session, transport = await _xbpi_session({})
        with pytest.raises(SartoriusConfirmationRequiredError):
            await session.execute(BAUD_CHANGE, _NoReq())
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_persistent_with_confirm_proceeds(self) -> None:
        tx = build_command(0x14)
        session, _ = await _xbpi_session({tx: _rx(0x00, b"")})
        # Should not raise; command proceeds and completes.
        await session.execute(WRITE_PARAM, _NoReq(), confirm=True)
        assert session.availability_of("write_parameter") is Availability.SUPPORTED

    @pytest.mark.anyio
    async def test_stateful_runs_without_confirm(self) -> None:
        """STATEFUL (tare, zero) are normal interactive ops — no confirm gate."""
        tx = build_command(0x14)
        session, _ = await _xbpi_session({tx: _rx(0x00, b"")})
        await session.execute(TARE, _NoReq())  # no confirm kwarg

    @pytest.mark.anyio
    async def test_readonly_runs_without_confirm(self) -> None:
        tx = build_command(0x71)
        session, _ = await _xbpi_session({tx: _rx(0x21, b"\x00")})
        await session.execute(READ_SBN, _NoReq())


# ---------------------------------------------------------------------------
# Protocol gate — active protocol's variant must be set.
# ---------------------------------------------------------------------------


class TestProtocolGate:
    @pytest.mark.anyio
    async def test_xbpi_session_refuses_sbi_only_command(self) -> None:
        session, transport = await _xbpi_session({})
        with pytest.raises(SartoriusProtocolUnsupportedError, match="no xBPI variant"):
            await session.execute(SBI_ONLY, _NoReq())
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_sbi_session_refuses_xbpi_only_command(self) -> None:
        """SBI session asked for an xBPI-only command → pre-I/O refusal.

        The variant-None gate catches the call before any byte is written.
        """
        from sartoriuslib.protocol.sbi.client import SbiProtocolClient

        transport = FakeTransport({})
        await transport.open()
        sbi_client = SbiProtocolClient(transport, default_timeout=0.1)
        session = Session(
            sbi_client=sbi_client,
            active_protocol=ProtocolKind.SBI,
            default_timeout=0.1,
        )
        with pytest.raises(SartoriusProtocolUnsupportedError, match="no SBI variant"):
            await session.execute(READ_SBN, _NoReq())


# ---------------------------------------------------------------------------
# Availability cache (§6.1.1).
# ---------------------------------------------------------------------------


class TestAvailabilityCache:
    @pytest.mark.anyio
    async def test_success_marks_supported(self) -> None:
        tx = build_command(0x71)
        session, _ = await _xbpi_session({tx: _rx(0x21, b"\x00")})
        await session.execute(READ_SBN, _NoReq())
        assert session.availability_of("read_sbn") is Availability.SUPPORTED

    @pytest.mark.anyio
    async def test_0x04_marks_unsupported_sticky(self) -> None:
        """xBPI 0x04 → UNSUPPORTED; next call refuses pre-I/O (no write)."""
        tx = build_command(0x71)
        session, transport = await _xbpi_session({tx: _rx(0x01, b"\x04")})
        with pytest.raises(SartoriusUnsupportedCommandError):
            await session.execute(READ_SBN, _NoReq())
        assert session.availability_of("read_sbn") is Availability.UNSUPPORTED
        first_write_count = len(transport.writes)

        # Second call must NOT touch the wire.
        with pytest.raises(SartoriusUnsupportedCommandError, match="previously responded"):
            await session.execute(READ_SBN, _NoReq())
        assert len(transport.writes) == first_write_count

    @pytest.mark.anyio
    async def test_0x06_marks_inapplicable_retryable(self) -> None:
        """xBPI 0x06 INAPPLICABLE is *not* sticky — next call tries again."""
        tx = build_command(0x71)
        # First reply: error 0x06. Second: success. Use a list-reply so a
        # single scripted entry delivers both on consecutive writes... but
        # FakeTransport maps each write to one reply. Instead, script the
        # same tx to 0x06 first, then replace the script for the retry.
        transport = FakeTransport({tx: _rx(0x01, b"\x06")})
        await transport.open()
        client = XbpiProtocolClient(transport, default_timeout=0.1)
        session = Session(
            xbpi_client=client,
            active_protocol=ProtocolKind.XBPI,
            default_timeout=0.1,
        )
        with pytest.raises(SartoriusOperationNotApplicableError):
            await session.execute(READ_SBN, _NoReq())
        assert session.availability_of("read_sbn") is Availability.INAPPLICABLE

        # Retry: swap the scripted reply and try again. The session does
        # NOT short-circuit on INAPPLICABLE — it re-issues the call.
        transport.add_script(tx, _rx(0x21, b"\x00"))
        result = await session.execute(READ_SBN, _NoReq())
        assert result == 0x00
        assert session.availability_of("read_sbn") is Availability.SUPPORTED

    @pytest.mark.anyio
    async def test_timeout_leaves_availability_unchanged(self) -> None:
        """No reply → timeout. Availability stays UNKNOWN — don't infer
        absence from a non-response."""
        session, _ = await _xbpi_session({})  # empty script → write works, read times out
        with pytest.raises(SartoriusTimeoutError):
            await session.execute(READ_SBN, _NoReq(), timeout=0.02)
        assert session.availability_of("read_sbn") is Availability.UNKNOWN


# ---------------------------------------------------------------------------
# Prior-based gate — soft by default, hard under strict.
# ---------------------------------------------------------------------------


class TestPriorGate:
    @pytest.mark.anyio
    async def test_strict_mode_refuses_family_mismatch(self) -> None:
        tx = build_command(0x71)
        session, transport = await _xbpi_session(
            {tx: _rx(0x21, b"\x00")},
            strict=True,
            family=BalanceFamily.OEM_WEIGH_CELL,
        )
        with pytest.raises(SartoriusCapabilityError, match="priors mismatch"):
            await session.execute(CUBIS_ONLY, _NoReq())
        # Pre-I/O: no bytes went out.
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_strict_mode_refuses_capability_mismatch(self) -> None:
        tx = build_command(0x71)
        session, transport = await _xbpi_session(
            {tx: _rx(0x21, b"\x00")},
            strict=True,
            capabilities=Capability(0),  # no HIRES
        )
        with pytest.raises(SartoriusCapabilityError):
            await session.execute(NEEDS_HIRES, _NoReq())
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_nonstrict_warns_and_attempts(self) -> None:
        tx = build_command(0x71)
        session, transport = await _xbpi_session(
            {tx: _rx(0x21, b"\x00")},
            family=BalanceFamily.OEM_WEIGH_CELL,
        )
        with pytest.warns(SartoriusCapabilityWarning, match="priors mismatch"):
            result = await session.execute(CUBIS_ONLY, _NoReq())
        assert result == 0x00
        assert transport.writes == (tx,)

    @pytest.mark.anyio
    async def test_warning_is_one_shot_per_command(self) -> None:
        """The warning fires once per (session, command); subsequent calls
        don't spam."""
        import warnings as _warnings

        tx = build_command(0x71)
        session, _ = await _xbpi_session(
            {tx: _rx(0x21, b"\x00")},
            family=BalanceFamily.OEM_WEIGH_CELL,
        )
        # First call: warning expected.
        with _warnings.catch_warnings(record=True) as captured:
            _warnings.simplefilter("always")
            await session.execute(CUBIS_ONLY, _NoReq())
            await session.execute(CUBIS_ONLY, _NoReq())
        messages = [w for w in captured if issubclass(w.category, SartoriusCapabilityWarning)]
        assert len(messages) == 1

    @pytest.mark.anyio
    async def test_matching_family_no_warning(self) -> None:
        import warnings as _warnings

        tx = build_command(0x71)
        session, _ = await _xbpi_session(
            {tx: _rx(0x21, b"\x00")},
            family=BalanceFamily.CUBIS,
        )
        with _warnings.catch_warnings(record=True) as captured:
            _warnings.simplefilter("always")
            await session.execute(CUBIS_ONLY, _NoReq())
        assert not any(issubclass(w.category, SartoriusCapabilityWarning) for w in captured)


# ---------------------------------------------------------------------------
# Gate order — safety runs before protocol, before priors.
# ---------------------------------------------------------------------------


class TestGateOrder:
    @pytest.mark.anyio
    async def test_safety_fires_before_protocol_gate(self) -> None:
        """A DANGEROUS SBI-only command under an XBPI session: without
        confirm, the confirmation-required error must fire first — the
        protocol-unsupported error should not leak out."""
        dangerous_sbi_only = Command[_NoReq, None](
            name="dangerous_sbi_only",
            xbpi=None,
            sbi=None,
            safety=SafetyTier.DANGEROUS,
        )
        session, transport = await _xbpi_session({})
        with pytest.raises(SartoriusConfirmationRequiredError):
            await session.execute(dangerous_sbi_only, _NoReq())
        assert transport.writes == ()

    @pytest.mark.anyio
    async def test_protocol_fires_before_priors(self) -> None:
        """Prior-mismatched SBI-only command under an XBPI session: the
        protocol gate fires first, no warning is emitted."""
        import warnings as _warnings

        sbi_only_cubis = Command[_NoReq, int](
            name="sbi_only_cubis",
            xbpi=None,
            sbi=None,
            family_hints=frozenset({BalanceFamily.CUBIS}),
            safety=SafetyTier.READ_ONLY,
        )
        session, _ = await _xbpi_session({}, family=BalanceFamily.OEM_WEIGH_CELL)
        with _warnings.catch_warnings(record=True) as captured:
            _warnings.simplefilter("always")
            with pytest.raises(SartoriusProtocolUnsupportedError):
                await session.execute(sbi_only_cubis, _NoReq())
        # No capability warning got as far as being emitted.
        assert not any(issubclass(w.category, SartoriusCapabilityWarning) for w in captured)


# ---------------------------------------------------------------------------
# Concurrency — two execute() calls on one session serialize.
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.anyio
    async def test_two_concurrent_calls_serialize(self) -> None:
        """Two execute() calls concurrently → both land on the transport,
        one at a time. FakeTransport isn't re-entrant: if they overlapped
        the read buffer would be corrupted."""
        tx_sbn = build_command(0x71)
        tx_tare = build_command(0x14)
        session, transport = await _xbpi_session(
            {
                tx_sbn: _rx(0x21, b"\x00"),
                tx_tare: _rx(0x00, b""),
            },
        )
        results: list[object] = []

        async def _exec(command: Command[_NoReq, Any]) -> None:
            results.append(await session.execute(command, _NoReq()))

        async with anyio.create_task_group() as tg:
            tg.start_soon(_exec, READ_SBN)
            tg.start_soon(_exec, TARE)
        assert set(transport.writes) == {tx_sbn, tx_tare}
        # Both calls completed.
        assert len(results) == 2
