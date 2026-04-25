"""Shared pytest configuration.

AnyIO's own pytest plugin drives async tests. The ``anyio_backend`` fixture
parametrizes tests across asyncio, asyncio+uvloop when uvloop is available,
and trio. Matches the backend matrix used by the sibling ``alicatlib`` and
``anyserial`` packages and catches regressions that only surface under one
scheduler.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

_UVLOOP_UNAVAILABLE = sys.platform == "win32" or importlib.util.find_spec("uvloop") is None

_PARAMS: list[ParameterSet] = [
    pytest.param(("asyncio", {"use_uvloop": False}), id="asyncio"),
    pytest.param(
        ("asyncio", {"use_uvloop": True}),
        id="asyncio+uvloop",
        marks=pytest.mark.skipif(
            _UVLOOP_UNAVAILABLE,
            reason="uvloop is unsupported or not installed on this platform",
        ),
    ),
    pytest.param("trio", id="trio"),
]


@pytest.fixture(params=_PARAMS)
def anyio_backend(request: pytest.FixtureRequest) -> object:
    """Run async tests against asyncio, asyncio+uvloop when available, and trio."""
    return request.param
