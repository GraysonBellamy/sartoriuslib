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
        DiscoverySummary,
        SartoriusDiscoveryResult,
        discover_port,
        find_devices,
        summarize_discovery,
    )

    assert 9600 in DEFAULT_DISCOVERY_BAUDRATES
    assert callable(discover_port)
    assert callable(find_devices)
    assert callable(summarize_discovery)
    assert DiscoveryResult.__name__ == "DiscoveryResult"
    assert DiscoverySummary.__name__ == "DiscoverySummary"
    assert issubclass(SartoriusDiscoveryResult, DiscoveryResult)


def test_unified_cross_lib_import_symmetry() -> None:
    """Verify the unified-spec import set is callable on sartoriuslib.

    Cross-lib spec §6 acceptance criterion. The same import shape must
    work on every sibling library (alicatlib, watlowlib, nidaqlib).
    PollSourceAdapter method signatures intentionally differ per lib;
    this test only verifies export presence.
    """
    from sartoriuslib import (
        DeviceResult,
        PollSourceAdapter,
        Recording,
        find_devices,
        open_device,
        sample_to_row,
    )
    from sartoriuslib.units import to_pint

    assert callable(open_device)
    assert callable(find_devices)
    assert callable(sample_to_row)
    assert callable(to_pint)
    assert DeviceResult.__name__ == "DeviceResult"
    assert PollSourceAdapter.__name__ == "PollSourceAdapter"
    assert Recording.__name__ == "Recording"
    # The factory shape spec §E.0 demands.
    ok: DeviceResult[int] = DeviceResult.success(42)
    assert ok.ok is True
    assert ok.value == 42
    # Failure factory uses any SartoriusError — pick the cheapest.
    from sartoriuslib import SartoriusError

    bad: DeviceResult[int] = DeviceResult.failure(SartoriusError("boom"))
    assert bad.ok is False
    assert bad.value is None
