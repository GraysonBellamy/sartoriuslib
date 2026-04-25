"""Logger tree for :mod:`sartoriuslib`.

The library **never** configures root handlers — users do. This module only
provides the canonical logger names so every module reaches for the same tree.
"""

from __future__ import annotations

import logging
from typing import Final

ROOT: Final[str] = "sartoriuslib"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``sartoriuslib`` tree.

    Args:
        name: Dotted suffix below the root, e.g. ``"transport"`` or
            ``"session"``. Pass ``""`` to get the root logger.

    Returns:
        The logger, never configured with handlers by this library.
    """
    if not name:
        return logging.getLogger(ROOT)
    return logging.getLogger(f"{ROOT}.{name}")
