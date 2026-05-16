"""Typed exception hierarchy for :mod:`sartoriuslib`.

Every library exception inherits from :class:`SartoriusError` and carries a
structured :class:`ErrorContext`. See design doc §12.

In addition to exceptions, the library emits :class:`SartoriusCapabilityWarning`
(a :class:`UserWarning` subclass) when a command is attempted against a device
whose priors do not match in non-strict mode (design doc §6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping


_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


def _empty_extra() -> Mapping[str, Any]:
    return _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`SartoriusError`.

    Fields are best-effort — missing data is ``None`` rather than raising.

    ``extra`` accepts any ``Mapping`` and is always frozen into a read-only
    :class:`types.MappingProxyType` at construction so the shared empty
    sentinel can never be mutated through ``error.context.extra[k] = v``.
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
    extra: Mapping[str, Any] = field(default_factory=_empty_extra)

    def __post_init__(self) -> None:
        if not isinstance(self.extra, MappingProxyType):
            object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @property
    def address(self) -> int | None:
        """Unified cross-library accessor for the device address.

        For sartoriuslib this is the xBPI SBN address (``sbn_address``).
        Consumers that work across sibling libraries (alicatlib, watlowlib,
        nidaqlib) read ``ctx.address`` uniformly; sartorius-internal code
        keeps using ``sbn_address`` because it carries protocol-layer
        semantics.
        """
        return self.sbn_address

    def merged(self, **updates: Any) -> Self:
        """Return a new context with ``updates`` overlaid. Unknown keys go to ``extra``."""
        known: dict[str, Any] = {}
        extra_updates: dict[str, Any] = {}
        for key, value in updates.items():
            if key in _CONTEXT_KNOWN_FIELDS:
                known[key] = value
            else:
                extra_updates[key] = value

        new_extra: Mapping[str, Any] = (
            MappingProxyType({**self.extra, **extra_updates}) if extra_updates else self.extra
        )
        return replace(self, **known, extra=new_extra)


_CONTEXT_KNOWN_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(ErrorContext) if f.name != "extra"
)


_EMPTY_CONTEXT = ErrorContext()


class SartoriusError(Exception):
    """Base class for every :mod:`sartoriuslib` exception.

    Carries a typed :class:`ErrorContext`. The ``message`` is the human-readable
    summary; the context is the machine-readable detail.
    """

    context: ErrorContext

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context if context is not None else _EMPTY_CONTEXT

    def with_context(self, **updates: Any) -> Self:
        """Return a copy of this error with its context updated.

        Useful when an inner layer raises and an outer layer wants to enrich
        the context (for instance adding ``port`` or ``elapsed_s``).
        """
        cls = type(self)
        new = cls.__new__(cls)
        new.args = self.args
        try:
            new.__dict__.update(self.__dict__)
        except AttributeError:  # pragma: no cover — no slotted subclass today
            for slot in getattr(cls, "__slots__", ()):
                if hasattr(self, slot):
                    object.__setattr__(new, slot, getattr(self, slot))
        new.context = self.context.merged(**updates)
        new.__cause__ = self.__cause__
        new.__context__ = self.__context__
        new.__traceback__ = self.__traceback__
        return new

    def __str__(self) -> str:
        base = super().__str__()
        ctx = self.context
        bits: list[str] = []
        if ctx.command_name is not None:
            bits.append(f"command={ctx.command_name}")
        if ctx.opcode is not None:
            bits.append(f"opcode=0x{ctx.opcode:02X}")
        if ctx.sbi_token is not None:
            bits.append(f"sbi_token={ctx.sbi_token!r}")
        if ctx.protocol is not None:
            bits.append(f"protocol={ctx.protocol}")
        if ctx.port is not None:
            bits.append(f"port={ctx.port}")
        if ctx.model is not None:
            bits.append(f"model={ctx.model}")
        if ctx.family is not None:
            bits.append(f"family={ctx.family}")
        if ctx.sbn_address is not None:
            bits.append(f"sbn=0x{ctx.sbn_address:02X}")
        if ctx.elapsed_s is not None:
            bits.append(f"elapsed_s={ctx.elapsed_s:.3f}")
        if ctx.command_bytes is not None:
            bits.append(f"command_bytes={ctx.command_bytes!r}")
        if ctx.raw_response is not None:
            bits.append(f"raw_response={ctx.raw_response!r}")
        if ctx.extra:
            bits.append(f"extra={dict(ctx.extra)!r}")
        return f"{base} [{', '.join(bits)}]" if bits else base


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


class SartoriusTransientTransportError(SartoriusTransportError):
    """Transport-layer hiccup that is safe to retry without reopening.

    Raised in the cold-open window when the device is still settling and
    a read returns 0 bytes (transport layer) or the first frame arrives
    short of ``MIN_FRAME_SIZE`` (protocol layer underrun). Callers may
    retry the same operation up to 3 times before escalating to
    :class:`SartoriusConnectionError`; :func:`sartoriuslib.open_device`
    swallows up to 3 inside the first identify so cold-open is invisible
    to most callers.
    """


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
    "SartoriusTransientTransportError",
    "SartoriusTransportError",
    "SartoriusUnsupportedCommandError",
    "SartoriusValidationError",
    "SartoriusValueOutOfRangeError",
    "UnknownUnitError",
]
