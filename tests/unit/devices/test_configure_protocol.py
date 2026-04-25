"""``Balance.configure_protocol`` — the host-side protocol flip.

Per ``docs/protocol.md`` §2.1, switching a Sartorius balance between
xBPI and SBI is a front-panel-menu operation on the device side; the
library's job is to reconcile the host (close old client, reopen the
transport at new framing, build new client, verify, refresh identity).

These tests exercise the host-side state machine end-to-end through
:class:`FakeTransport`:

- Confirm-required gate (DANGEROUS tier).
- AUTO target rejected as a value error.
- Same-protocol same-framing → no-op success (no transport activity).
- Cross-protocol switch with new framing → transport reopens, verify
  via identity probe, post-switch identify refreshes ``DeviceInfo``.
- Verification failure rolls the transport back to the original
  framing and surfaces the original error.
- Rollback failure marks the session :attr:`SessionState.BROKEN`.
- BROKEN session refuses every subsequent dispatch.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusConfirmationRequiredError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusValidationError,
    open_device,
)
from sartoriuslib.devices.session import SessionState
from sartoriuslib.protocol.sbi import LINE_TERMINATOR, TOKEN_SERIAL, TOKEN_SOFTWARE, TOKEN_TYPE
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
)


def _sbi_line(text: str) -> bytes:
    return text.encode("ascii") + LINE_TERMINATOR


def _xbpi_full_script() -> dict[bytes, bytes]:
    """Scripted xBPI identify replies for an MSE under ``open_device``."""
    return build_identify_script()


def _sbi_full_identify_script() -> dict[bytes, bytes]:
    """Scripted SBI identify replies — used as the post-switch verification +
    identify pair when ``configure_protocol(SBI)`` runs."""
    return {
        TOKEN_TYPE: _sbi_line("WZA8202-N"),
        TOKEN_SERIAL: _sbi_line("12345678"),
        TOKEN_SOFTWARE: _sbi_line("1.0"),
    }


class TestGate:
    @pytest.mark.anyio
    async def test_requires_confirm_true(self) -> None:
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            with pytest.raises(SartoriusConfirmationRequiredError, match="DANGEROUS"):
                await bal.configure_protocol(ProtocolKind.SBI)
        finally:
            await bal.aclose()

    @pytest.mark.anyio
    async def test_auto_target_is_rejected(self) -> None:
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            with pytest.raises(SartoriusValidationError, match="AUTO"):
                await bal.configure_protocol(ProtocolKind.AUTO, confirm=True)
        finally:
            await bal.aclose()


class TestNoOp:
    @pytest.mark.anyio
    async def test_same_protocol_same_framing_is_a_noop(self) -> None:
        """Calling configure_protocol(current) with no overrides returns
        cached info and never touches the transport."""
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        try:
            writes_before = transport.writes
            reopens_before = transport.reopen_count
            info = await bal.configure_protocol(ProtocolKind.XBPI, confirm=True)
            assert info is bal.info
            # No new bytes, no reopens.
            assert transport.writes == writes_before
            assert transport.reopen_count == reopens_before
        finally:
            await bal.aclose()


class TestCrossProtocolSwitch:
    @pytest.mark.anyio
    async def test_xbpi_to_sbi_reopens_and_refreshes_identity(self) -> None:
        script: dict[bytes, bytes] = _xbpi_full_script()
        script.update(_sbi_full_identify_script())
        transport = FakeTransport(script)
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.1)
        old_client = bal.session.xbpi_client
        try:
            new_info = await bal.configure_protocol(
                ProtocolKind.SBI,
                baudrate=1200,
                confirm=True,
            )
        finally:
            await bal.aclose()

        assert old_client is not None
        assert old_client.disposed is True

        # Transport reopened at the new baud (the only override we passed).
        assert transport.reopen_count >= 1
        assert transport.last_reopen_baud == 1200

        # Session now speaks SBI; DeviceInfo reflects SBI identity decode.
        assert new_info.protocol is ProtocolKind.SBI
        assert new_info.model == "WZA8202-N"

        # Both verification + identify hit the SBI identity tokens.
        assert TOKEN_TYPE in transport.writes


class TestRollback:
    @pytest.mark.anyio
    async def test_failed_verify_rolls_transport_back_and_keeps_old_protocol(self) -> None:
        """No SBI script is registered → the verify probe times out →
        the rollback path reopens at the original baud and re-raises."""
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        original_baud = bal.session.serial_settings.baudrate  # type: ignore[union-attr]
        try:
            with pytest.raises(SartoriusError):
                await bal.configure_protocol(
                    ProtocolKind.SBI,
                    baudrate=1200,
                    timeout=0.05,
                    confirm=True,
                )
            # Rollback ran: reopen back to the original baud.
            assert transport.last_reopen_baud == original_baud
            # Session still operational.
            assert bal.session.state is SessionState.OPERATIONAL
        finally:
            await bal.aclose()

    @pytest.mark.anyio
    async def test_failed_rollback_marks_session_broken(self) -> None:
        """When the rollback ``reopen`` itself fails, the session must
        transition to BROKEN and a clear connection error is raised.

        Both failures are surfaced: the original switch error becomes
        ``__cause__`` (the real reason the user reached this state); the
        rollback failure is captured in the error context so support
        teams can see both.
        """
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            # No SBI script + force the next reopen (rollback) to error.
            transport.force_reopen_error(True)
            with pytest.raises(SartoriusConnectionError, match="BROKEN") as exc_info:
                await bal.configure_protocol(
                    ProtocolKind.SBI,
                    baudrate=1200,
                    timeout=0.05,
                    confirm=True,
                )
            assert bal.session.state is SessionState.BROKEN
            # The original switch failure must be preserved as ``__cause__``
            # — not the rollback failure, which masks the real reason.
            assert exc_info.value.__cause__ is not None
            assert "rollback_error" in (exc_info.value.context.extra or {})
        finally:
            await bal.aclose()


class TestBrokenSessionRefusesDispatch:
    @pytest.mark.anyio
    async def test_broken_session_refuses_subsequent_calls(self) -> None:
        """A BROKEN session must refuse every subsequent dispatch with
        :class:`SartoriusConnectionError`, not hang on a dead transport."""
        transport = FakeTransport(_xbpi_full_script())
        bal = await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)
        try:
            transport.force_reopen_error(True)
            with pytest.raises(SartoriusConnectionError):
                await bal.configure_protocol(
                    ProtocolKind.SBI,
                    baudrate=1200,
                    timeout=0.05,
                    confirm=True,
                )
            assert bal.session.state is SessionState.BROKEN
            with pytest.raises(SartoriusConnectionError, match="BROKEN"):
                await bal.poll()
            with pytest.raises(SartoriusConnectionError, match="BROKEN"):
                await bal.identify()
        finally:
            await bal.aclose()
