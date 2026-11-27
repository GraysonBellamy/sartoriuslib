"""Hardware: internal adjustment wait / abort / re-attach on a real Cubis.

Runs real internal adjustments (~25 s each, three in total). Opt in with::

    SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1 \
    SARTORIUSLIB_TEST_PORT=COM4 SARTORIUSLIB_TEST_BAUD=57600 \
    uv run pytest tests/integration/test_internal_adjust_hw.py -m hardware_destructive

The pan can carry a tared load; the bench captures behind the
implementation were taken with ~344 g on the pan.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import anyio
import pytest

from sartoriuslib import (
    BalanceState,
    SartoriusAdjustmentAbortedError,
    open_device,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.transport.base import SerialSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sartoriuslib import Balance

pytestmark = [
    pytest.mark.hardware_destructive,
    pytest.mark.skipif(
        not (
            os.environ.get("SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS")
            and os.environ.get("SARTORIUSLIB_TEST_PORT")
        ),
        reason="set SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1 and SARTORIUSLIB_TEST_PORT",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"  # one real run is enough; don't triple the cal cycles


@pytest.fixture
async def bal() -> AsyncIterator[Balance]:
    port = os.environ["SARTORIUSLIB_TEST_PORT"]
    baud = int(os.environ.get("SARTORIUSLIB_TEST_BAUD", "19200"))
    balance = await open_device(
        port,
        protocol=ProtocolKind.XBPI,
        serial_settings=SerialSettings(port=port, baudrate=baud),
        timeout=1.0,
    )
    try:
        yield balance
    finally:
        await balance.close()


@pytest.mark.anyio
async def test_wait_true_returns_a_fresh_record(bal: Balance) -> None:
    before = await bal.last_cal_record()
    result = await bal.internal_adjust(confirm=True, wait=True)
    assert result.previous_cal_record.raw == before.raw
    assert result.cal_record.raw != before.raw
    assert result.cal_record.timestamp is not None
    assert 15.0 < result.duration_s < 90.0
    assert (await bal.last_cal_record()).raw == result.cal_record.raw
    assert (await bal.status()).state is not BalanceState.ADJUSTING


@pytest.mark.anyio
async def test_abort_from_another_task(bal: Balance) -> None:
    before = await bal.last_cal_record()
    outcome: list[BaseException] = []

    async def waiter() -> None:
        try:
            await bal.internal_adjust(confirm=True, wait=True)
        except SartoriusAdjustmentAbortedError as exc:
            outcome.append(exc)

    async with anyio.create_task_group() as tg:
        tg.start_soon(waiter)
        await anyio.sleep(8.0)
        assert await bal.abort_adjustment() is True

    assert len(outcome) == 1
    assert (await bal.last_cal_record()).raw == before.raw
    assert await bal.abort_adjustment() is False


@pytest.mark.anyio
async def test_cancel_then_reattach(bal: Balance) -> None:
    with anyio.move_on_after(8.0) as scope:
        await bal.internal_adjust(confirm=True, wait=True)
    assert scope.cancelled_caught
    status = await bal.status()  # port still in sync after the cancel
    assert status.state is BalanceState.ADJUSTING
    result = await bal.wait_for_adjustment()
    assert result.cal_record.raw != result.previous_cal_record.raw
