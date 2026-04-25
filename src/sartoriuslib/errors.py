"""Typed exception hierarchy for :mod:`sartoriuslib`.

Every library exception inherits from :class:`SartoriusError` and carries a
structured :class:`ErrorContext`. See design doc §12.

In addition to exceptions, the library emits :class:`SartoriusCapabilityWarning`
(a :class:`UserWarning` subclass) when a command is attempted against a device
whose priors do not match in non-strict mode (design doc §6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _empty_extra() -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`SartoriusError`.

    Fields are best-effort — missing data is ``None`` rather than raising.
    """

    command_name: str | None = None
    command_bytes: bytes | None = None
    opcode: int | None = None
    sbi_token: bytes | None = None
    raw_response: bytes | str | None = None
    protocol: str | None = None
    port: str | None = None
    model: str | None = None
    family: str | None = None
    sbn_address: int | None = None
    elapsed_s: float | None = None
    extra: dict[str, Any] = field(default_factory=_empty_extra)


class SartoriusError(Exception):
    """Base class for every :mod:`sartoriuslib` exception."""

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context or ErrorContext()


# --- Configuration -------------------------------------------------------


class SartoriusConfigurationError(SartoriusError):
    """Configuration-level error (bad args, wrong confirm flag, etc.)."""


class UnknownUnitError(SartoriusConfigurationError):
    """The unit code is not recognised."""


class InvalidParameterIndexError(SartoriusConfigurationError):
    """Parameter-table index is out of range for this device."""


class InvalidSbnError(SartoriusConfigurationError):
    """SBN bus address is invalid."""


class SartoriusConfirmationRequiredError(SartoriusConfigurationError):
    """A PERSISTENT / DANGEROUS command was attempted without ``confirm=True``."""


class SartoriusValidationError(SartoriusConfigurationError):
    """Request validation failed before I/O."""


# --- Transport -----------------------------------------------------------


class SartoriusTransportError(SartoriusError):
    """I/O-layer error from the serial transport."""


class SartoriusTimeoutError(SartoriusTransportError):
    """A transport read or write timed out."""


class SartoriusConnectionError(SartoriusTransportError):
    """Could not open / lost the connection to the balance."""


# --- Protocol ------------------------------------------------------------


class SartoriusProtocolError(SartoriusError):
    """Protocol-level error (framing, parsing, device refusal)."""


class SartoriusFrameError(SartoriusProtocolError):
    """Bad checksum, bad length, malformed TLV, etc."""


class SartoriusParseError(SartoriusProtocolError):
    """Unknown xBPI subtype or unparseable SBI line."""


class SartoriusCommandRejectedError(SartoriusProtocolError):
    """The device returned an xBPI subtype ``0x01`` / SBI refusal response."""


class SartoriusProtocolUnsupportedError(SartoriusProtocolError):
    """Command has no variant defined for the active protocol."""


class SartoriusAutoprintActiveError(SartoriusProtocolError):
    """SBI autoprint is active, so command/reply traffic is not reliable."""


# --- Capability ----------------------------------------------------------


class SartoriusCapabilityError(SartoriusError):
    """Command is not available on this device / firmware / family."""


class SartoriusUnsupportedCommandError(SartoriusCapabilityError, SartoriusCommandRejectedError):
    """Device returned xBPI err ``0x04`` (unsupported/unknown opcode)."""


class SartoriusValueOutOfRangeError(SartoriusCapabilityError, SartoriusCommandRejectedError):
    """Device returned xBPI err ``0x03`` (value out of range)."""


class SartoriusOperationNotApplicableError(SartoriusCapabilityError, SartoriusCommandRejectedError):
    """Device returned xBPI err ``0x06`` (operation not applicable)."""


class SartoriusMissingArgsError(SartoriusCapabilityError, SartoriusCommandRejectedError):
    """Device returned xBPI err ``0x07`` (invalid or missing args)."""


class SartoriusIndexOutOfRangeError(SartoriusCapabilityError, SartoriusCommandRejectedError):
    """Device returned xBPI err ``0x10`` (index out of range)."""


class SartoriusFirmwareError(SartoriusCapabilityError):
    """Command is outside the supported firmware window."""


# --- Sinks ---------------------------------------------------------------


class SartoriusSinkError(SartoriusError):
    """Base class for errors raised by sinks (CSV, JSONL, SQLite, Parquet, Postgres)."""


class SartoriusSinkDependencyError(SartoriusSinkError, SartoriusConfigurationError):
    """A sink's optional backing library is not installed.

    Raised when the user instantiates (or calls ``open()`` on) a sink
    whose extras have not been installed — e.g. ``ParquetSink`` without
    ``sartoriuslib[parquet]`` or ``PostgresSink`` without
    ``sartoriuslib[postgres]``. The message names the exact extra to
    install so the remediation is copy-pasteable.

    Multi-inherits :class:`SartoriusConfigurationError` because callers
    that already branch on configuration errors (missing extras being a
    configuration problem from their perspective) keep working without
    changes.
    """


class SartoriusSinkSchemaError(SartoriusSinkError):
    """A batch's shape is incompatible with the sink's locked schema.

    Raised when a sink has locked its schema on the first batch (or
    validated against an existing table) and a subsequent batch carries
    rows whose shape can't be reconciled — for example, a Postgres
    target table that's missing a required column.

    Dropping unknown *optional* columns is handled by a per-sink WARN
    log and does not raise.
    """


class SartoriusSinkWriteError(SartoriusSinkError):
    """The backing store rejected a write.

    Wraps the underlying driver exception (sqlite3, asyncpg, pyarrow)
    so downstream error handlers don't need to import optional
    dependencies. The original exception is preserved via
    ``raise ... from original`` so tracebacks remain intact.
    """


# --- Warnings ------------------------------------------------------------


class SartoriusCapabilityWarning(UserWarning):
    """Emitted when a command's family/capability priors do not match the device.

    In non-strict mode (the default) the library attempts the command anyway
    and updates availability from the device's response. See design doc §6.1.
    """


__all__ = [
    "ErrorContext",
    "InvalidParameterIndexError",
    "InvalidSbnError",
    "SartoriusAutoprintActiveError",
    "SartoriusCapabilityError",
    "SartoriusCapabilityWarning",
    "SartoriusCommandRejectedError",
    "SartoriusConfigurationError",
    "SartoriusConfirmationRequiredError",
    "SartoriusConnectionError",
    "SartoriusError",
    "SartoriusFirmwareError",
    "SartoriusFrameError",
    "SartoriusIndexOutOfRangeError",
    "SartoriusMissingArgsError",
    "SartoriusOperationNotApplicableError",
    "SartoriusParseError",
    "SartoriusProtocolError",
    "SartoriusProtocolUnsupportedError",
    "SartoriusSinkDependencyError",
    "SartoriusSinkError",
    "SartoriusSinkSchemaError",
    "SartoriusSinkWriteError",
    "SartoriusTimeoutError",
    "SartoriusTransportError",
    "SartoriusUnsupportedCommandError",
    "SartoriusValidationError",
    "SartoriusValueOutOfRangeError",
    "UnknownUnitError",
]
