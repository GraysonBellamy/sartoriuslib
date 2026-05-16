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

The public surface tracks the cross-library unified API
(``UNIFIED_API_HANDOFF.md``): ``open_device(...)``,
:class:`SartoriusManager`, :func:`find_devices`, :class:`DiscoveryResult`
(per-probe row) plus the sartorius-typed :class:`SartoriusDiscoveryResult`
subclass, :class:`Sample` with the §C timestamp contract,
:class:`DeviceResult` with ``success()`` / ``failure()`` factories,
:class:`PollSourceAdapter`, :class:`Recording`, and
:func:`sartoriuslib.units.to_pint`.

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
from sartoriuslib.devices.balance import Balance, DeviceSnapshot, SartoriusDeviceSnapshot
from sartoriuslib.devices.discovery import (
    DEFAULT_DISCOVERY_BAUDRATES,
    DiscoveryResult,
    DiscoverySummary,
    SartoriusDiscoveryResult,
    discover_port,
    find_devices,
    summarize_discovery,
)
from sartoriuslib.devices.factory import open_device
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
    SartoriusTransientTransportError,
    SartoriusTransportError,
    SartoriusUnsupportedCommandError,
    SartoriusValidationError,
    SartoriusValueOutOfRangeError,
    UnknownUnitError,
)
from sartoriuslib.firmware import FirmwareVersion
from sartoriuslib.manager import (
    DeviceResult,
    ErrorPolicy,
    SartoriusManager,
)
from sartoriuslib.protocol import DetectionResult, ProtocolKind, detect_protocol
from sartoriuslib.registry.units import Sign, Unit
from sartoriuslib.sinks.base import sample_to_row
from sartoriuslib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    PollSourceAdapter,
    Recording,
    Sample,
    StreamingSession,
    StreamMode,
    record,
)
from sartoriuslib.units import to_pint
from sartoriuslib.version import __version__

__all__ = [
    "DEFAULT_DISCOVERY_BAUDRATES",
    "AcquisitionSummary",
    "Availability",
    "Balance",
    "BalanceFamily",
    "BalanceState",
    "BalanceStatus",
    "CalRecord",
    "Capability",
    "DetectionResult",
    "DeviceInfo",
    "DeviceResult",
    "DeviceSnapshot",
    "DiscoveryResult",
    "DiscoverySummary",
    "ErrorContext",
    "ErrorPolicy",
    "FirmwareVersion",
    "InvalidParameterIndexError",
    "InvalidSbnError",
    "OverflowPolicy",
    "ParameterEntry",
    "PollSource",
    "PollSourceAdapter",
    "ProbeOutcome",
    "ProbeSource",
    "ProtocolKind",
    "Quantity",
    "Reading",
    "Recording",
    "SafetyTier",
    "Sample",
    "SartoriusAutoprintActiveError",
    "SartoriusCapabilityError",
    "SartoriusCapabilityWarning",
    "SartoriusCommandRejectedError",
    "SartoriusConfigurationError",
    "SartoriusConfirmationRequiredError",
    "SartoriusConnectionError",
    "SartoriusDeviceSnapshot",
    "SartoriusDiscoveryResult",
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
    "SartoriusTransientTransportError",
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
    "find_devices",
    "open_device",
    "record",
    "sample_to_row",
    "summarize_discovery",
    "to_pint",
]
