"""Balance family taxonomy. See design doc §5."""

from __future__ import annotations

from enum import StrEnum


class BalanceFamily(StrEnum):
    """Classification from the model string returned by xBPI ``0x02`` or SBI identify.

    - :attr:`CUBIS` — MSE and related Cubis strings; full xBPI plus Cubis extensions.
    - :attr:`OEM_WEIGH_CELL` — WZ*/WZA*; ships from the factory in SBI
      autoprint (1200-7-O-1) and requires a front-panel menu change to switch
      to xBPI. (MSE and BCE also ship in SBI by default — switching to xBPI
      is a front-panel menu change on every family.)
    - :attr:`BASIC_LAB` — BCE*; MSE opcode subset, no Cubis extensions.
    - :attr:`UNKNOWN` — anything we have not classified; every call becomes a live probe.
    """

    CUBIS = "cubis"
    OEM_WEIGH_CELL = "oem_weigh_cell"
    BASIC_LAB = "basic_lab"
    UNKNOWN = "unknown"


def classify_family(model: str) -> BalanceFamily:
    """Classify a balance family by its model-string prefix.

    Rules (design §5):

    - ``MSE*`` and related Cubis strings → :attr:`BalanceFamily.CUBIS`
    - ``WZ*`` / ``WZA*`` → :attr:`BalanceFamily.OEM_WEIGH_CELL`
    - ``BCE*`` → :attr:`BalanceFamily.BASIC_LAB`
    - anything else → :attr:`BalanceFamily.UNKNOWN`

    Case-insensitive and whitespace-tolerant. Returns ``UNKNOWN`` for
    empty input — every call becomes a live probe for unclassified
    devices (design §5.1).
    """
    stripped = model.strip().upper()
    if stripped.startswith("MSE"):
        return BalanceFamily.CUBIS
    if stripped.startswith("WZ"):
        return BalanceFamily.OEM_WEIGH_CELL
    if stripped.startswith("BCE"):
        return BalanceFamily.BASIC_LAB
    return BalanceFamily.UNKNOWN


__all__ = ["BalanceFamily", "classify_family"]
