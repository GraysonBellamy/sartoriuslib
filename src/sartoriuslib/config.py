"""Runtime configuration for :mod:`sartoriuslib` (stub).

Environment-variable overrides follow the same pattern as ``alicatlib``. See
design doc §15 slice 0 for the skeleton scope.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SartoriusConfig:
    """Frozen runtime configuration.

    All fields have safe defaults; overrides come from environment via
    :func:`config_from_env` or are passed explicitly at ``open_device``.
    """

    # Serial defaults — MSE ships 19200-8-O-1, WZA ships 1200-8-O-1,
    # BCE ships 9600-8-O-1. Callers should supply serial params explicitly.
    default_baud: int = 19200
    default_parity: str = "O"
    default_stopbits: int = 1
    default_bytesize: int = 8

    # Timeouts (seconds)
    open_timeout: float = 2.0
    read_timeout: float = 1.0
    write_timeout: float = 1.0

    # Strict mode default — priors-as-contracts vs priors-as-hints (design §6.1).
    strict_capability_gating: bool = False


def config_from_env() -> SartoriusConfig:
    """Build a :class:`SartoriusConfig` from environment variables.

    Not yet implemented; returns the default configuration.
    """
    return SartoriusConfig()


__all__ = ["SartoriusConfig", "config_from_env"]
