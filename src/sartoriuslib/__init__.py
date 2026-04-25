"""sartoriuslib — Python library for Sartorius balances.

Supports both wire protocols the hardware speaks:

- **xBPI**: binary, length-prefixed, checksum-protected, SBN-addressed.
- **SBI**: ASCII command/response and autoprint.

The public API is semantic and protocol-neutral — a caller asks for
``poll()``, ``tare()``, ``status()``; the session dispatches the xBPI or SBI
variant selected at open time. Both protocols decode into the same frozen
:class:`Reading` / :class:`BalanceStatus` / :class:`DeviceInfo` models.

Core API is ``async`` (built on ``anyio``); a sync facade is available at
:mod:`sartoriuslib.sync` for scripts, notebooks, and REPL use.

See ``docs/design.md`` for the architectural design.
"""

from __future__ import annotations

from sartoriuslib.devices import (
    Availability,
    BalanceFamily,
    Capability,
    ProbeSource,
    SafetyTier,
)
from sartoriuslib.devices.balance import Balance
from sartoriuslib.devices.discovery import DiscoveryResult, discover_port
from sartoriuslib.devices.factory import open_balance, open_device
from sartoriuslib.devices.models import (
    BalanceState,
    BalanceStatus,
    CalRecord,
    DeviceInfo,
    ParameterEntry,
    ProbeOutcome,
    Quantity,
    Reading,
    TemperatureReading,
)
from sartoriuslib.devices.session import SessionState
from sartoriuslib.errors import (
    ErrorContext,
    InvalidParameterIndexError,
    InvalidSbnError,
    SartoriusAutoprintActiveError,
    SartoriusCapabilityError,
    SartoriusCapabilityWarning,
    SartoriusCommandRejectedError,
    SartoriusConfigurationError,
    SartoriusConfirmationRequiredError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusFirmwareError,
    SartoriusFrameError,
    SartoriusIndexOutOfRangeError,
    SartoriusMissingArgsError,
    SartoriusOperationNotApplicableError,
    SartoriusParseError,
    SartoriusProtocolError,
    SartoriusProtocolUnsupportedError,
    SartoriusSinkDependencyError,
    SartoriusSinkError,
    SartoriusSinkSchemaError,
    SartoriusSinkWriteError,
    SartoriusTimeoutError,
    SartoriusTransportError,
    SartoriusUnsupportedCommandError,
    SartoriusValidationError,
    SartoriusValueOutOfRangeError,
    UnknownUnitError,
)
from sartoriuslib.firmware import FirmwareVersion
from sartoriuslib.manager import (
    BalanceManager,
    DeviceResult,
    ErrorPolicy,
    SartoriusManager,
)
from sartoriuslib.protocol import DetectionResult, ProtocolKind, detect_protocol
from sartoriuslib.registry.units import Sign, Unit
from sartoriuslib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    Sample,
    StreamingSession,
    StreamMode,
    record,
)
from sartoriuslib.version import __version__

__all__ = [
    "AcquisitionSummary",
    "Availability",
    "Balance",
    "BalanceFamily",
    "BalanceManager",
    "BalanceState",
    "BalanceStatus",
    "CalRecord",
    "Capability",
    "DetectionResult",
    "DeviceInfo",
    "DeviceResult",
    "DiscoveryResult",
    "ErrorContext",
    "ErrorPolicy",
    "FirmwareVersion",
    "InvalidParameterIndexError",
    "InvalidSbnError",
    "OverflowPolicy",
    "ParameterEntry",
    "PollSource",
    "ProbeOutcome",
    "ProbeSource",
    "ProtocolKind",
    "Quantity",
    "Reading",
    "SafetyTier",
    "Sample",
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
    "SartoriusManager",
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
    "SessionState",
    "Sign",
    "StreamMode",
    "StreamingSession",
    "TemperatureReading",
    "Unit",
    "UnknownUnitError",
    "__version__",
    "detect_protocol",
    "discover_port",
    "open_balance",
    "open_device",
    "record",
]
