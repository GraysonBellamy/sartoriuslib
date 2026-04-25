"""``stream(mode="autoprint")`` consume-only contract.

Validates the consume-only autoprint streaming path:

- ``mode="autoprint"`` on an SBI session with autoprint already running
  yields a :class:`Sample` and never writes to the balance.
- ``mode="autoprint"`` without a live line fails loudly (timeout) on
  context entry — no silent block, no surprise enable.
- ``mode="autoprint"`` on a non-SBI session refuses pre-I/O.
- The streaming session keeps reading lines after the first one without
  writing, even when interleaved with mid-line numeric fragments that
  must be skipped.
"""

from __future__ import annotations

import pytest

from sartoriuslib import (
    ProtocolKind,
    SartoriusProtocolUnsupportedError,
    open_device,
)
from sartoriuslib.errors import SartoriusTimeoutError
from sartoriuslib.testing import FakeTransport, build_identify_script


class TestAutoprintConsume:
    @pytest.mark.anyio
    async def test_consumes_existing_line_without_writing(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g  \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(mode="autoprint", timeout=0.1) as stream:
            sample = await anext(stream)
        assert sample.reading is not None
        assert sample.reading.value == 0.0
        assert sample.metadata["mode"] == "autoprint"
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_without_live_line_times_out_loudly(self) -> None:
        """No silent block — entry fails fast when nothing is on the line."""
        transport = FakeTransport()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.01,
        )
        with pytest.raises(SartoriusTimeoutError):
            async with bal.stream(mode="autoprint", timeout=0.01):
                pass
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_refuses_on_xbpi_session(self) -> None:
        transport = FakeTransport(build_identify_script())
        bal = await open_device(
            transport,
            protocol=ProtocolKind.XBPI,
            timeout=0.1,
        )
        with pytest.raises(SartoriusProtocolUnsupportedError, match="SBI"):
            async with bal.stream(mode="autoprint", timeout=0.1):
                pass
        await bal.aclose()

    @pytest.mark.anyio
    async def test_consumes_multiple_lines_without_writing(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(
            b"+     0.00 g  \r\n+     1.23 g  \r\n-   12.345 mg \r\n",
        )
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(mode="autoprint", timeout=0.1) as stream:
            first = await anext(stream)
            second = await anext(stream)
            third = await anext(stream)
        assert first.reading is not None
        assert first.reading.value == 0.0
        assert second.reading is not None
        assert second.reading.value == 1.23
        assert third.reading is not None
        assert third.reading.value == -12.345
        assert transport.writes == ()
        await bal.aclose()

    @pytest.mark.anyio
    async def test_skips_midline_numeric_fragment(self) -> None:
        """Mid-stream attach can land on a bare-number tail; skip until
        a fully formatted weight line arrives."""
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"   079\r\n0.090\r\nN     +    0.031    \r\n")
        await transport.close()
        bal = await open_device(
            transport,
            protocol=ProtocolKind.SBI,
            identify=False,
            timeout=0.1,
        )
        async with bal.stream(mode="autoprint", timeout=0.1) as stream:
            sample = await anext(stream)
        assert sample.reading is not None
        assert sample.reading.value == 0.031
        assert sample.reading.stable is False
        assert transport.writes == ()
        await bal.aclose()
