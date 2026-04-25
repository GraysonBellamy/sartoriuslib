"""Tests for :func:`sartoriuslib.devices.kind.classify_family`."""

from __future__ import annotations

import pytest

from sartoriuslib.devices.kind import BalanceFamily, classify_family


class TestClassifyFamily:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("MSE1203S-100-DR", BalanceFamily.CUBIS),
            ("MSE225P-100-DU", BalanceFamily.CUBIS),
            ("mse1203s-100-dr", BalanceFamily.CUBIS),  # case-insensitive
            ("WZA8202-N", BalanceFamily.OEM_WEIGH_CELL),
            ("WZA2202-N", BalanceFamily.OEM_WEIGH_CELL),
            ("WZ6202-L", BalanceFamily.OEM_WEIGH_CELL),
            ("BCE3202-1S", BalanceFamily.BASIC_LAB),
            ("BCE223-1S", BalanceFamily.BASIC_LAB),
        ],
    )
    def test_known_prefixes(self, model: str, expected: BalanceFamily) -> None:
        assert classify_family(model) is expected

    def test_unknown_prefix_returns_unknown(self) -> None:
        assert classify_family("QZQ9999") is BalanceFamily.UNKNOWN

    def test_empty_string_returns_unknown(self) -> None:
        assert classify_family("") is BalanceFamily.UNKNOWN

    def test_whitespace_tolerant(self) -> None:
        assert classify_family("  MSE1203S  ") is BalanceFamily.CUBIS
