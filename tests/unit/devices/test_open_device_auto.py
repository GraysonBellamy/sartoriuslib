"""``open_device(protocol=AUTO)`` — end-to-end resolution paths.

Covers all three exit paths of :func:`sartoriuslib.protocol.detect_protocol`
when reached through the public :func:`open_device` factory:

- Passive sniff observes SBI autoprint → SBI session in consume-only
  mode, sniffed line preserved for the first :meth:`Balance.poll`.
- xBPI ``READ_MODEL`` probe answers → xBPI session, identity refreshed.
- Sniff silent + xBPI silent + SBI ``ESC x1_`` answers → SBI session
  in command/reply mode.

Failure-clean path is covered by
:mod:`tests.unit.devices.test_balance::TestOpenDevice::
test_auto_protocol_fails_cleanly_when_silent`.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusAutoprintActiveError,
    open_device,
)
from sartoriuslib.protocol.sbi import LINE_TERMINATOR, TOKEN_SERIAL, TOKEN_SOFTWARE, TOKEN_TYPE
from sartoriuslib.testing import (
    FakeTransport,
    build_identify_script,
)


def _sbi_line(text: str) -> bytes:
    return text.encode("ascii") + LINE_TERMINATOR


def _sbi_full_identify_script(
    *,
    model: str = "WZA8202-N",
    serial: str = "12345678",
    software: str = "1.0",
) -> dict[bytes, bytes]:
    return {
        TOKEN_TYPE: _sbi_line(model),
        TOKEN_SERIAL: _sbi_line(serial),
        TOKEN_SOFTWARE: _sbi_line(software),
    }


class TestAutoXbpi:
    @pytest.mark.anyio
    async def test_resolves_to_xbpi_when_xbpi_responds(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.AUTO, timeout=0.1)
        try:
            assert bal.session.active_protocol is ProtocolKind.XBPI
            assert bal.session.sbi_autoprint_active is False
            # identify=True ran on the new session and populated DeviceInfo.
            assert bal.info is not None
            assert bal.info.model == "MSE1203S-100-DR"
        finally:
            await bal.aclose()


class TestAutoSbiAutoprint:
    @pytest.mark.anyio
    async def test_resolves_to_sbi_consume_only_when_autoprint_observed(self) -> None:
        """A balance left in autoprint resolves to SBI; ``identify=True``
        is rejected per the consume-only contract — open with
        ``identify=False`` and the sniffed line is preserved for the
        first :meth:`Balance.poll`."""
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     1.23 g  \r\n")
        await transport.close()

        bal = await open_device(
            transport,
            protocol=ProtocolKind.AUTO,
            timeout=0.1,
            identify=False,
        )
        try:
            assert bal.session.active_protocol is ProtocolKind.SBI
            assert bal.session.sbi_autoprint_active is True
            # First poll consumes the sniffed line — no write to the device.
            reading = await bal.poll()
            assert reading.value == 1.23
            assert transport.writes == ()
        finally:
            await bal.aclose()

    @pytest.mark.anyio
    async def test_identify_true_with_autoprint_raises(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        await transport.close()

        with pytest.raises(SartoriusAutoprintActiveError):
            await open_device(
                transport,
                protocol=ProtocolKind.AUTO,
                timeout=0.1,
                identify=True,
            )


class TestAutoSbiIdentityProbe:
    @pytest.mark.anyio
    async def test_resolves_to_sbi_via_identity_probe_when_silent(self) -> None:
        """Silent sniff + silent xBPI + responsive ``ESC x1_`` → SBI in
        plain command/reply mode (autoprint inactive)."""
        transport = FakeTransport(_sbi_full_identify_script())
        bal = await open_device(transport, protocol=ProtocolKind.AUTO, timeout=0.1)
        try:
            assert bal.session.active_protocol is ProtocolKind.SBI
            assert bal.session.sbi_autoprint_active is False
            assert bal.info is not None
            assert bal.info.model == "WZA8202-N"
        finally:
            await bal.aclose()
