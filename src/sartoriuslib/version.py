"""Package version — kept in sync with ``project.version`` in ``pyproject.toml``."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("sartoriuslib")
except PackageNotFoundError:  # pragma: no cover - editable / uninstalled
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
