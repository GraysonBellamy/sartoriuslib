"""``temporary_autoprint=True`` rejection contract.

Verified ``p36`` SBI parameter writes are not yet implemented, so
``stream(mode="autoprint", temporary_autoprint=True)`` is intentionally
unimplemented:

- Without ``confirm=True``: refused with
  :class:`SartoriusConfirmationRequiredError` because flipping ``p36``
  mutates a persistent device parameter (design §16.13).
- With ``confirm=True``: refused with :class:`NotImplementedError`
  pending a verified SBI ``p36`` write/read path. **This test should be
  promoted to enable/restore behaviour once the command lands.**
- Neither call writes to the balance — the gates run pre-I/O.

The plain consume-only mode (``temporary_autoprint=False``, the default)
is exercised in :mod:`tests.unit.protocol.sbi.test_sbi_autoprint_consume`.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    Balance,
    ProtocolKind,
    SartoriusConfirmationRequiredError,
    open_device,
)
from sartoriuslib.testing import FakeTransport


async def _open_quiet_sbi() -> tuple[Balance, FakeTransport]:
    transport = FakeTransport()
    bal = await open_device(
        transport,
        protocol=ProtocolKind.SBI,
        identify=False,
        timeout=0.05,
    )
    return bal, transport


class TestTemporaryAutoprint:
    @pytest.mark.anyio
    async def test_without_confirm_refused_pre_io(self) -> None:
        bal, transport = await _open_quiet_sbi()
        with pytest.raises(SartoriusConfirmationRequiredError, match="confirm=True"):
            async with bal.stream(
                mode="autoprint",
                temporary_autoprint=True,
                confirm=False,
                timeout=0.05,
            ):
                pass
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_with_confirm_explicitly_unimplemented(self) -> None:
        bal, transport = await _open_quiet_sbi()
        with pytest.raises(NotImplementedError, match="parameter-write"):
            async with bal.stream(
                mode="autoprint",
                temporary_autoprint=True,
                confirm=True,
                timeout=0.05,
            ):
                pass
        assert transport.writes == ()
        await bal.close()

    @pytest.mark.anyio
    async def test_refusal_path_does_not_change_session_state(self) -> None:
        """Pre-I/O refusal leaves :attr:`Session.sbi_autoprint_active` untouched
        so subsequent calls keep behaving as if no streaming was attempted."""
        bal, transport = await _open_quiet_sbi()
        before = bool(bal.session.sbi_autoprint_active)
        with pytest.raises(SartoriusConfirmationRequiredError):
            async with bal.stream(
                mode="autoprint",
                temporary_autoprint=True,
                timeout=0.05,
            ):
                pass
        assert bool(bal.session.sbi_autoprint_active) is before
        assert transport.writes == ()
        await bal.close()
