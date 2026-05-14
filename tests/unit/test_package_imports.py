"""Smoke test — the skeleton is importable and exports the expected symbols."""

from __future__ import annotations


def test_package_imports() -> None:
    import sartoriuslib

    assert sartoriuslib.__version__


def test_public_enums_present() -> None:
    from sartoriuslib import (
        Availability,
        BalanceFamily,
        Capability,
        ProtocolKind,
        SafetyTier,
    )

    assert ProtocolKind.AUTO.value == "auto"
    assert BalanceFamily.UNKNOWN.value == "unknown"
    assert SafetyTier.READ_ONLY < SafetyTier.DANGEROUS
    assert Availability.UNKNOWN.value == "unknown"
    assert Capability.XBPI_SUPPORT in Capability.XBPI_SUPPORT | Capability.SBI_SUPPORT


def test_error_hierarchy() -> None:
    from sartoriuslib import (
        SartoriusCapabilityError,
        SartoriusCommandRejectedError,
        SartoriusConfirmationRequiredError,
        SartoriusError,
        SartoriusProtocolError,
        SartoriusProtocolUnsupportedError,
        SartoriusUnsupportedCommandError,
    )

    assert issubclass(SartoriusProtocolUnsupportedError, SartoriusError)
    assert issubclass(SartoriusUnsupportedCommandError, SartoriusCapabilityError)
    assert issubclass(SartoriusUnsupportedCommandError, SartoriusCommandRejectedError)
    assert issubclass(SartoriusUnsupportedCommandError, SartoriusProtocolError)
    assert issubclass(SartoriusConfirmationRequiredError, SartoriusError)


def test_error_context_defaults() -> None:
    from sartoriuslib import ErrorContext, SartoriusError

    err = SartoriusError("boom")
    assert isinstance(err.context, ErrorContext)
    assert err.context.command_name is None
    assert err.context.extra == {}


def test_discovery_api_exported() -> None:
    from sartoriuslib import (
        DEFAULT_DISCOVERY_BAUDRATES,
        DiscoveryResult,
        FindResult,
        discover_port,
        find_devices,
    )

    assert 9600 in DEFAULT_DISCOVERY_BAUDRATES
    assert callable(discover_port)
    assert callable(find_devices)
    assert DiscoveryResult.__name__ == "DiscoveryResult"
    assert FindResult.__name__ == "FindResult"
