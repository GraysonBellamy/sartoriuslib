"""Session: I/O lock, safety gates, availability cache, prior checks.

One :class:`Session` wraps one balance on one serial port. It is the
single dispatch point between the :class:`Balance` facade and the
protocol clients. Every :meth:`Session.execute` call walks the
gates in the design-doc §6.1 order, then runs the command via the
active protocol's client, then updates the per-command availability
cache per §6.1.1.

What lives here
    - Active-protocol selection (xBPI vs SBI).
    - Safety-tier gate (``PERSISTENT`` / ``DANGEROUS`` need ``confirm=True``).
    - Protocol gate (command's variant for the active protocol must be set).
    - Availability gate (commands known ``UNSUPPORTED`` short-circuit pre-I/O).
    - Prior gate (family / capability hints; soft by default, hard under ``strict``).
    - Per-call availability update (success → SUPPORTED, 0x04 → UNSUPPORTED,
      0x06 → INAPPLICABLE, else unchanged).

What does *not* live here
    - Transport I/O: delegated to the protocol client.
    - Device identification: the factory pre-computes family /
      capabilities / firmware and hands them in at construction.

Result cache
    :meth:`cached_execute` fronts results keyed on command + caller-
    supplied ``cache_key`` and xBPI's ``0xBA`` config counter
    (``docs/protocol.md`` §7.11 and design §6.3). Before returning a
    cached value, the session re-reads ``0xBA``; a mismatch flushes the
    entry. Writes invalidate the affected key explicitly — the §6.3
    caveat says ``p13`` / ``p50`` writes don't bump the counter, so the
    cache stays correct only if the :class:`Balance` facade clears
    those entries on its own.

    Sessions without :attr:`Capability.CONFIG_COUNTER` (WZA, SBI) fall
    back to un-cached dispatch transparently — ``cached_execute``
    behaves identically to :meth:`execute` in that mode.
"""

from __future__ import annotations

import warnings
from enum import Enum
from typing import TYPE_CHECKING, Any

import anyio

from sartoriuslib.commands.base import NO_CAPABILITY, CommandContext
from sartoriuslib.commands.raw import SAFE_READ_ONLY_OPCODES
from sartoriuslib.devices.capability import Availability, Capability, SafetyTier
from sartoriuslib.devices.kind import BalanceFamily
from sartoriuslib.errors import (
    ErrorContext,
    SartoriusAutoprintActiveError,
    SartoriusCapabilityError,
    SartoriusCapabilityWarning,
    SartoriusConfirmationRequiredError,
    SartoriusConnectionError,
    SartoriusError,
    SartoriusIndexOutOfRangeError,
    SartoriusOperationNotApplicableError,
    SartoriusParseError,
    SartoriusProtocolUnsupportedError,
    SartoriusUnsupportedCommandError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi import (
    SBI_READ_ONLY_TOKENS,
    TOKEN_PRINT,
    is_autoprint_line,
    normalize_token,
    require_reading,
)
from sartoriuslib.protocol.xbpi import build_command

if TYPE_CHECKING:
    from sartoriuslib.commands.base import Command
    from sartoriuslib.devices.models import Reading
    from sartoriuslib.firmware import FirmwareVersion
    from sartoriuslib.protocol.sbi.client import SbiProtocolClient
    from sartoriuslib.protocol.sbi.types import SbiReply
    from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient
    from sartoriuslib.protocol.xbpi.types import XbpiFrame
    from sartoriuslib.transport.base import SerialSettings, Transport

__all__ = ["Session", "SessionState"]


class SessionState(Enum):
    """Lifecycle state of a :class:`Session`.

    ``OPERATIONAL`` is the normal state — commands dispatch freely.
    ``BROKEN`` is entered when an atomic lifecycle operation
    (``Balance.configure_protocol``) cannot reconcile the transport
    with the device's new state. A ``BROKEN`` session refuses every
    subsequent :meth:`execute` with :class:`SartoriusConnectionError`;
    the caller must construct a fresh session (typically via
    :func:`sartoriuslib.open_device`) to recover.
    """

    OPERATIONAL = "operational"
    BROKEN = "broken"


class Session:
    """One balance, one serial port. Enforces gates, serialises I/O.

    Arguments:
        xbpi_client: Client for xBPI dispatch, or ``None`` if this
            session is SBI-only.
        active_protocol: Which protocol this session currently speaks.
        family: Balance family discriminator (from ``DeviceInfo`` once
            identified; ``UNKNOWN`` means no prior).
        capabilities: Bitmap of capabilities believed present.
        firmware: Firmware version, if known.
        src_sbn: Host SBN address for xBPI frames.
        dst_sbn: Balance SBN address for xBPI frames.
        strict: If ``True``, family/capability prior mismatches refuse
            pre-I/O instead of emitting a warning.
        default_timeout: Per-call timeout when the caller passes
            ``None`` to :meth:`execute`.
    """

    def __init__(
        self,
        *,
        xbpi_client: XbpiProtocolClient | None = None,
        sbi_client: SbiProtocolClient | None = None,
        active_protocol: ProtocolKind,
        family: BalanceFamily = BalanceFamily.UNKNOWN,
        capabilities: Capability = NO_CAPABILITY,
        firmware: FirmwareVersion | None = None,
        src_sbn: int = 0x01,
        dst_sbn: int = 0x09,
        strict: bool = False,
        default_timeout: float = 1.0,
        serial_settings: SerialSettings | None = None,
    ) -> None:
        if active_protocol is ProtocolKind.AUTO:
            raise SartoriusError(
                "Session cannot be constructed with ProtocolKind.AUTO; "
                "detection must resolve to XBPI or SBI first",
            )
        if active_protocol is ProtocolKind.XBPI and xbpi_client is None:
            raise SartoriusError(
                "Session: active_protocol=XBPI requires xbpi_client",
            )
        if active_protocol is ProtocolKind.SBI and sbi_client is None:
            raise SartoriusError(
                "Session: active_protocol=SBI requires sbi_client",
            )
        self._xbpi = xbpi_client
        self._sbi = sbi_client
        self._active = active_protocol
        self._family = family
        self._capabilities = capabilities
        self._firmware = firmware
        self._src_sbn = src_sbn
        self._dst_sbn = dst_sbn
        self._strict = strict
        self._default_timeout = default_timeout
        self._availability: dict[str, Availability] = {}
        self._warned_priors: set[str] = set()
        # Result cache keyed on caller-supplied cache_key → (counter_snapshot, value).
        # Populated by :meth:`cached_execute`; cleared by :meth:`invalidate_cache`
        # and on any ``0xBA`` mismatch at read time (design §6.3).
        self._result_cache: dict[str, tuple[int, Any]] = {}
        self._state: SessionState = SessionState.OPERATIONAL
        # Serial settings the transport was opened with. Tracked here
        # (not on the transport) so :meth:`Balance.configure_protocol`
        # can roll back to the original framing without coupling the
        # rollback path to a specific Transport implementation.
        self._serial_settings = serial_settings

    # ------------------------------------------------------------------ props

    @property
    def active_protocol(self) -> ProtocolKind:
        """Protocol the session currently dispatches through."""
        return self._active

    @property
    def family(self) -> BalanceFamily:
        """Family discriminator; ``UNKNOWN`` means no seeded prior."""
        return self._family

    @property
    def capabilities(self) -> Capability:
        """Bitmap of capabilities believed present on the balance."""
        return self._capabilities

    @property
    def firmware(self) -> FirmwareVersion | None:
        """Firmware version if identified, else ``None``."""
        return self._firmware

    @property
    def strict(self) -> bool:
        """Whether prior mismatches refuse pre-I/O (``True``) or warn."""
        return self._strict

    @property
    def sbi_autoprint_active(self) -> bool:
        """Whether the SBI session has observed unsolicited autoprint output."""
        return (
            self._active is ProtocolKind.SBI
            and self._sbi is not None
            and self._sbi.autoprint_active
        )

    @property
    def state(self) -> SessionState:
        """Lifecycle state — ``BROKEN`` after a failed protocol/baud switch."""
        return self._state

    @property
    def transport(self) -> Transport:
        """The underlying :class:`Transport`, regardless of active protocol.

        Both protocol clients hold the same transport — return whichever
        is wired. The constructor enforces that the client matching
        ``active_protocol`` is non-None, so this always returns a real
        :class:`Transport`.
        """
        if self._xbpi is not None:
            return self._xbpi.transport
        assert self._sbi is not None  # noqa: S101
        return self._sbi.transport

    @property
    def xbpi_client(self) -> XbpiProtocolClient | None:
        """The xBPI protocol client wired to this session, if any."""
        return self._xbpi

    @property
    def sbi_client(self) -> SbiProtocolClient | None:
        """The SBI protocol client wired to this session, if any."""
        return self._sbi

    @property
    def default_timeout(self) -> float:
        """Per-call timeout used when callers pass ``None``."""
        return self._default_timeout

    @property
    def src_sbn(self) -> int:
        """Host-side SBN included in xBPI request frames."""
        return self._src_sbn

    @property
    def dst_sbn(self) -> int:
        """Balance-side SBN used as the destination in xBPI request frames."""
        return self._dst_sbn

    def set_dst_sbn(self, dst_sbn: int) -> None:
        """Update the destination SBN.

        Used after :meth:`Balance.write_sbn_address` on multidrop links
        where the new address must address the device going forward.
        """
        self._dst_sbn = dst_sbn

    def check_state(self) -> None:
        """Raise :class:`SartoriusConnectionError` if the session is BROKEN.

        Public alias for the internal gate run by every dispatch path,
        so lifecycle helpers on :class:`Balance` can reuse the same
        guard without poking at private attributes.
        """
        self._check_state()

    @property
    def serial_settings(self) -> SerialSettings | None:
        """Serial settings the transport was opened with, if known.

        Set at construction time by :func:`sartoriuslib.open_device` and
        updated after a successful :meth:`Balance.configure_protocol`.
        ``None`` for sessions built from a pre-existing :class:`Transport`
        whose framing the library did not control.
        """
        return self._serial_settings

    def availability_of(self, command_name: str) -> Availability:
        """Current availability for ``command_name`` (``UNKNOWN`` if unseen)."""
        return self._availability.get(command_name, Availability.UNKNOWN)

    async def refresh_sbi_autoprint_state(self, *, timeout: float | None = None) -> bool:
        """Passively re-sniff whether SBI autoprint is currently active."""
        if self._active is not ProtocolKind.SBI:
            raise SartoriusProtocolUnsupportedError(
                "refresh_sbi_autoprint_state requires an SBI session",
                context=ErrorContext(protocol=str(self._active.value)),
            )
        if self._sbi is None:
            raise SartoriusError(
                "refresh_sbi_autoprint_state: session in SBI mode but no SBI client wired",
                context=ErrorContext(protocol="sbi"),
            )
        return await self._sbi.refresh_autoprint_state(timeout=timeout)

    def update_identity(
        self,
        *,
        family: BalanceFamily | None = None,
        capabilities: Capability | None = None,
        firmware: FirmwareVersion | None = None,
    ) -> None:
        """Replace session-level identity state after a live identify call.

        Called by :func:`sartoriuslib.devices.factory.open_device` after
        running the identify commands, so subsequent prior gating sees
        the discovered family and capabilities instead of the
        placeholder ``UNKNOWN`` / empty values.

        Each argument left as ``None`` keeps the existing value.
        """
        if family is not None:
            self._family = family
        if capabilities is not None:
            self._capabilities = capabilities
        if firmware is not None:
            self._firmware = firmware

    def replace_clients(
        self,
        *,
        xbpi_client: XbpiProtocolClient | None,
        sbi_client: SbiProtocolClient | None,
        active_protocol: ProtocolKind,
        serial_settings: SerialSettings | None = None,
    ) -> None:
        """Swap protocol clients atomically — used by ``configure_protocol``.

        The host-side flip closes the old protocol client, reopens the
        transport at new serial framing, builds a new client, and
        verifies. On verification success this method installs the new
        clients and the new active protocol; the availability cache,
        prior warnings, and result cache are all
        cleared because the command surface changes when the protocol
        does (xBPI-only commands have no SBI variant and vice versa,
        and any ``0xBA``-pinned cache entries belong to the old session).

        Refuses to install ``ProtocolKind.AUTO`` — detection must
        resolve to ``XBPI`` or ``SBI`` first. Refuses if the
        corresponding client for ``active_protocol`` is missing.
        """
        if active_protocol is ProtocolKind.AUTO:
            raise SartoriusError(
                "replace_clients: cannot install ProtocolKind.AUTO; "
                "detection must resolve to XBPI or SBI first",
            )
        if active_protocol is ProtocolKind.XBPI and xbpi_client is None:
            raise SartoriusError(
                "replace_clients: active_protocol=XBPI but xbpi_client is None",
            )
        if active_protocol is ProtocolKind.SBI and sbi_client is None:
            raise SartoriusError(
                "replace_clients: active_protocol=SBI but sbi_client is None",
            )
        self._xbpi = xbpi_client
        self._sbi = sbi_client
        self._active = active_protocol
        if serial_settings is not None:
            self._serial_settings = serial_settings
        # Cross-protocol availability and prior warnings do not
        # transfer — clear them so the new protocol starts clean.
        self._availability.clear()
        self._warned_priors.clear()
        self._result_cache.clear()

    def mark_broken(self) -> None:
        """Transition the session to :attr:`SessionState.BROKEN`.

        Called only from lifecycle operations
        (``Balance.configure_protocol``) when a rollback fails. Once
        broken, every subsequent dispatch refuses with
        :class:`SartoriusConnectionError`.
        """
        self._state = SessionState.BROKEN

    def _check_state(self) -> None:
        """Refuse to dispatch when the session is :attr:`SessionState.BROKEN`."""
        if self._state is SessionState.BROKEN:
            raise SartoriusConnectionError(
                "session is BROKEN after a failed lifecycle operation; "
                "close this session and re-open via open_device(...) to recover",
                context=ErrorContext(
                    extra={"session_state": self._state.value},
                ),
            )

    # ------------------------------------------------------------------ dispatch

    async def execute[Req, Resp](
        self,
        command: Command[Req, Resp],
        request: Req,
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> Resp:
        """Dispatch ``command`` with full pre-I/O gating.

        Gates fire in the design doc §6.1 order; each raise happens
        before any byte is sent, so a gate failure is observably
        equivalent to the call never leaving the host.
        """
        self._check_state()
        self._gate_safety(command, confirm)
        self._gate_protocol(command)
        self._gate_known_denied(command)
        self._gate_priors(command)
        if self._active is ProtocolKind.XBPI:
            return await self._execute_xbpi(command, request, timeout)
        if self._active is ProtocolKind.SBI:
            return await self._execute_sbi(command, request, timeout)
        raise SartoriusError(f"unreachable: session has no active protocol ({self._active!r})")

    # ------------------------------------------------------------------ cache

    async def cached_execute[Req, Resp](
        self,
        command: Command[Req, Resp],
        request: Req,
        *,
        cache_key: str,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> Resp:
        """Dispatch ``command`` with a ``0xBA``-keyed result cache.

        Sessions without :attr:`Capability.CONFIG_COUNTER` fall through
        to :meth:`execute`. When the capability is present, the session
        re-reads the config counter before returning a cached value;
        any change flushes the entry and the command re-runs.

        ``cache_key`` is caller-supplied so a command that takes
        arguments (``capacity(area=N)``, ``read_parameter(idx)``) can
        cache separate entries per distinct call.
        """
        if Capability.CONFIG_COUNTER not in self._capabilities:
            return await self.execute(command, request, confirm=confirm, timeout=timeout)
        counter = await self._read_config_counter(timeout=timeout)
        cached = self._result_cache.get(cache_key)
        if cached is not None and cached[0] == counter:
            return cached[1]  # type: ignore[no-any-return]
        result = await self.execute(command, request, confirm=confirm, timeout=timeout)
        self._result_cache[cache_key] = (counter, result)
        return result

    def invalidate_cache(self, cache_key: str | None = None) -> None:
        """Drop one cached entry, or clear all when ``cache_key`` is ``None``.

        The :class:`Balance` facade calls this after writes whose
        ``0xBA`` bump isn't guaranteed (the §6.3 caveat). Unknown
        keys are a silent no-op — idempotent.
        """
        if cache_key is None:
            self._result_cache.clear()
        else:
            self._result_cache.pop(cache_key, None)

    async def _read_config_counter(self, *, timeout: float | None = None) -> int:
        """Read ``0xBA`` via :meth:`execute` (no cache recursion)."""
        # Local import keeps the session → commands dependency one-way
        # at module-load time.
        from sartoriuslib.commands.system import (  # noqa: PLC0415
            CONFIG_COUNTER,
            SystemRequest,
        )

        return await self.execute(CONFIG_COUNTER, SystemRequest(), timeout=timeout)

    def cache_snapshot(self) -> dict[str, int]:
        """Copy of ``cache_key → counter_snapshot`` for test assertions.

        The actual cached values are intentionally not surfaced —
        tests assert on the *presence* and *counter pinning* of an
        entry, not its decoded content.
        """
        return {key: counter for key, (counter, _value) in self._result_cache.items()}

    # ------------------------------------------------------------------ gates

    def _gate_protocol(self, command: Command[Any, Any]) -> None:
        """Active protocol must have a variant declared on the command."""
        # self._active is XBPI or SBI (AUTO is rejected in __init__).
        if self._active is ProtocolKind.XBPI:
            if command.xbpi is None:
                raise SartoriusProtocolUnsupportedError(
                    f"{command.name}: no xBPI variant; session is in XBPI mode",
                    context=ErrorContext(
                        command_name=command.name,
                        protocol=str(ProtocolKind.XBPI.value),
                        extra={"active_protocol": "xbpi"},
                    ),
                )
            return
        if command.sbi is None:
            raise SartoriusProtocolUnsupportedError(
                f"{command.name}: no SBI variant; session is in SBI mode",
                context=ErrorContext(
                    command_name=command.name,
                    protocol=str(ProtocolKind.SBI.value),
                    extra={"active_protocol": "sbi"},
                ),
            )

    def _gate_safety(self, command: Command[Any, Any], confirm: bool) -> None:
        if command.safety in (SafetyTier.READ_ONLY, SafetyTier.STATEFUL):
            return
        if confirm:
            return
        raise SartoriusConfirmationRequiredError(
            f"{command.name}: {command.safety.name} commands require confirm=True",
            context=ErrorContext(
                command_name=command.name,
                protocol=str(self._active.value),
                extra={"safety": command.safety.name},
            ),
        )

    def _gate_known_denied(self, command: Command[Any, Any]) -> None:
        if self._availability.get(command.name) is Availability.UNSUPPORTED:
            raise SartoriusUnsupportedCommandError(
                f"{command.name}: device previously responded UNSUPPORTED; "
                "session will not re-probe",
                context=ErrorContext(
                    command_name=command.name,
                    protocol=str(self._active.value),
                    extra={"availability": Availability.UNSUPPORTED.value},
                ),
            )

    def _gate_priors(self, command: Command[Any, Any]) -> None:
        """Check family + capability priors; warn (or raise under strict)."""
        family_ok = not command.family_hints or self._family in command.family_hints
        caps_ok = (
            command.capability_hints == NO_CAPABILITY
            or (self._capabilities & command.capability_hints) == command.capability_hints
        )
        if family_ok and caps_ok:
            return
        missing_caps = command.capability_hints & ~self._capabilities
        reason_parts: list[str] = []
        if not family_ok:
            reason_parts.append(
                f"family {self._family.value!r} not in hints "
                f"{sorted(f.value for f in command.family_hints)!r}"
            )
        if not caps_ok:
            reason_parts.append(f"missing capabilities {missing_caps!r}")
        reason = "; ".join(reason_parts)

        ctx = ErrorContext(
            command_name=command.name,
            protocol=str(self._active.value),
            family=self._family.value,
            extra={
                "family_hints": sorted(f.value for f in command.family_hints),
                "capability_hints": command.capability_hints.value,
                "missing_capabilities": missing_caps.value,
                "reason": reason,
            },
        )
        if self._strict:
            raise SartoriusCapabilityError(
                f"{command.name}: priors mismatch in strict mode ({reason})",
                context=ctx,
            )
        # Non-strict: emit one warning per (command, session) pair.
        if command.name in self._warned_priors:
            return
        self._warned_priors.add(command.name)
        warnings.warn(
            f"{command.name}: priors mismatch ({reason}); attempting anyway",
            SartoriusCapabilityWarning,
            stacklevel=3,
        )

    # ------------------------------------------------------------------ xBPI

    async def _execute_xbpi[Req, Resp](
        self,
        command: Command[Req, Resp],
        request: Req,
        timeout: float | None,
    ) -> Resp:
        variant = command.xbpi
        if variant is None:  # pragma: no cover — gate_protocol refuses first
            raise SartoriusProtocolUnsupportedError(
                f"{command.name}: xBPI variant missing at dispatch",
                context=ErrorContext(command_name=command.name, protocol="xbpi"),
            )
        if self._xbpi is None:
            raise SartoriusError(
                f"{command.name}: session in XBPI mode but no xBPI client wired",
                context=ErrorContext(command_name=command.name),
            )
        ctx = CommandContext(
            protocol=ProtocolKind.XBPI,
            src_sbn=self._src_sbn,
            dst_sbn=self._dst_sbn,
            firmware=self._firmware,
            family=self._family,
        )
        request_bytes = variant.encode(ctx, request)
        t = timeout if timeout is not None else self._default_timeout
        try:
            frame = await self._xbpi.execute(
                request_bytes,
                timeout=t,
                command_name=command.name,
                opcode=variant.opcode,
            )
        except SartoriusUnsupportedCommandError as exc:
            # Parameterized commands (sensor/parameter/area-indexed)
            # frequently get xBPI 0x04 from firmware that should have
            # returned 0x10 (index out of range). Don't poison the
            # availability cache for the whole command in that case —
            # in-range arguments may still be supported. Translate to
            # the semantic-intent error so callers can distinguish
            # "this index is out of range" from "this command doesn't
            # exist on this device".
            if command.parameterized:
                raise SartoriusIndexOutOfRangeError(
                    f"{command.name}: argument out of range "
                    f"(device returned 0x04; treated as index error because "
                    f"command is parameterized)",
                    context=exc.context,
                ) from exc
            self._availability[command.name] = Availability.UNSUPPORTED
            raise
        except SartoriusOperationNotApplicableError:
            self._availability[command.name] = Availability.INAPPLICABLE
            raise
        # Success: update availability, then decode.
        self._availability[command.name] = Availability.SUPPORTED
        return variant.decode(frame, ctx)

    # ------------------------------------------------------------------ SBI

    async def _execute_sbi[Req, Resp](
        self,
        command: Command[Req, Resp],
        request: Req,
        timeout: float | None,
    ) -> Resp:
        if command.sbi is None:  # pragma: no cover — gate_protocol refuses first
            raise SartoriusProtocolUnsupportedError(
                f"{command.name}: SBI variant missing at dispatch",
                context=ErrorContext(command_name=command.name, protocol="sbi"),
            )
        if self._sbi is None:
            raise SartoriusError(
                f"{command.name}: session in SBI mode but no SBI client wired",
                context=ErrorContext(command_name=command.name),
            )
        ctx = CommandContext(
            protocol=ProtocolKind.SBI,
            src_sbn=self._src_sbn,
            dst_sbn=self._dst_sbn,
            firmware=self._firmware,
            family=self._family,
        )
        variant = command.sbi
        request_bytes = variant.encode(ctx, request)
        t = timeout if timeout is not None else self._default_timeout
        expect_lines = variant.expect_lines
        if self.sbi_autoprint_active and expect_lines > 0:
            self._raise_sbi_autoprint_active(
                command.name,
                sbi_token=variant.token,
            )
        reply = await self._sbi.execute(
            request_bytes,
            timeout=t,
            command_name=command.name,
            sbi_token=variant.token,
            expect_lines=expect_lines,
        )
        if self._reply_is_surprise_autoprint(reply, sbi_token=variant.token):
            self._sbi.mark_autoprint_active(pending=reply.raw)
            self._raise_sbi_autoprint_active(
                command.name,
                sbi_token=variant.token,
            )
        self._availability[command.name] = Availability.SUPPORTED
        return variant.decode(reply, ctx)

    # ------------------------------------------------------------------ raw

    async def execute_raw_xbpi(
        self,
        opcode: int,
        args: bytes = b"",
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> XbpiFrame:
        """Send an arbitrary xBPI opcode and return the raw reply frame.

        Bypasses the declarative :class:`Command` layer — the opcode is
        a per-call parameter, so none of the prior / capability gating
        applies. The one hard gate is a safe-list check: opcodes in
        :data:`sartoriuslib.commands.raw.SAFE_READ_ONLY_OPCODES` run
        freely; anything else requires ``confirm=True`` because the
        library cannot know it is safe.

        The availability cache is *not* updated — raw calls are opaque
        at the command-name level.
        """
        self._check_state()
        if self._active is not ProtocolKind.XBPI:
            raise SartoriusProtocolUnsupportedError(
                f"raw_xbpi: session is in {self._active.value.upper()} mode",
                context=ErrorContext(
                    opcode=opcode,
                    protocol=str(self._active.value),
                ),
            )
        if self._xbpi is None:
            raise SartoriusError(
                "raw_xbpi: session in XBPI mode but no xBPI client wired",
                context=ErrorContext(opcode=opcode, protocol="xbpi"),
            )
        if opcode not in SAFE_READ_ONLY_OPCODES and not confirm:
            raise SartoriusConfirmationRequiredError(
                f"raw_xbpi: opcode 0x{opcode:02x} is not on the read-only safe-list; "
                "pass confirm=True to proceed",
                context=ErrorContext(
                    opcode=opcode,
                    protocol="xbpi",
                    extra={"safe_listed": False},
                ),
            )
        request_bytes = build_command(
            opcode,
            args,
            src_sbn=self._src_sbn,
            dst_sbn=self._dst_sbn,
        )
        t = timeout if timeout is not None else self._default_timeout
        return await self._xbpi.execute(
            request_bytes,
            timeout=t,
            command_name=f"raw_xbpi[0x{opcode:02x}]",
            opcode=opcode,
        )

    async def execute_raw_sbi(
        self,
        command: bytes | str,
        *,
        confirm: bool = False,
        timeout: float | None = None,
        expect_lines: int = 1,
    ) -> SbiReply:
        """Send an arbitrary SBI command token and return the parsed reply."""
        self._check_state()
        if self._active is not ProtocolKind.SBI:
            raise SartoriusProtocolUnsupportedError(
                f"raw_sbi: session is in {self._active.value.upper()} mode",
                context=ErrorContext(protocol=str(self._active.value)),
            )
        if self._sbi is None:
            raise SartoriusError(
                "raw_sbi: session in SBI mode but no SBI client wired",
                context=ErrorContext(protocol="sbi"),
            )
        token = normalize_token(command)
        if token not in SBI_READ_ONLY_TOKENS and not confirm:
            raise SartoriusConfirmationRequiredError(
                f"raw_sbi: token {token!r} is not on the read-only safe-list; "
                "pass confirm=True to proceed",
                context=ErrorContext(
                    sbi_token=token,
                    protocol="sbi",
                    extra={"safe_listed": False},
                ),
            )
        t = timeout if timeout is not None else self._default_timeout
        if self.sbi_autoprint_active and expect_lines > 0:
            self._raise_sbi_autoprint_active("raw_sbi", sbi_token=token)
        reply = await self._sbi.execute(
            token,
            timeout=t,
            command_name="raw_sbi",
            sbi_token=token,
            expect_lines=expect_lines,
        )
        if self._reply_is_surprise_autoprint(reply, sbi_token=token):
            self._sbi.mark_autoprint_active(pending=reply.raw)
            self._raise_sbi_autoprint_active("raw_sbi", sbi_token=token)
        return reply

    async def read_sbi_line(self, *, timeout: float | None = None) -> SbiReply:
        """Read one unsolicited SBI line, used by autoprint streaming."""
        if self._active is not ProtocolKind.SBI:
            raise SartoriusProtocolUnsupportedError(
                f"read_sbi_line: session is in {self._active.value.upper()} mode",
                context=ErrorContext(protocol=str(self._active.value)),
            )
        if self._sbi is None:
            raise SartoriusError(
                "read_sbi_line: session in SBI mode but no SBI client wired",
                context=ErrorContext(protocol="sbi"),
            )
        t = timeout if timeout is not None else self._default_timeout
        return await self._sbi.read_line(timeout=t)

    async def read_sbi_autoprint_reading(
        self,
        *,
        timeout: float | None = None,
    ) -> Reading:
        """Read the next valid SBI autoprint weight line without writing."""
        if self._active is not ProtocolKind.SBI:
            raise SartoriusProtocolUnsupportedError(
                f"read_sbi_autoprint_reading: session is in {self._active.value.upper()} mode",
                context=ErrorContext(protocol=str(self._active.value)),
            )
        if self._sbi is None:
            raise SartoriusError(
                "read_sbi_autoprint_reading: session in SBI mode but no SBI client wired",
                context=ErrorContext(protocol="sbi"),
            )
        t = timeout if timeout is not None else self._default_timeout
        deadline = anyio.current_time() + t
        while True:
            remaining = max(0.001, deadline - anyio.current_time())
            reply = await self._sbi.read_line(timeout=remaining)
            try:
                reading = require_reading(reply)
            except SartoriusParseError:
                if anyio.current_time() >= deadline:
                    raise
                continue
            self._sbi.mark_autoprint_active()
            return reading

    def _reply_is_surprise_autoprint(
        self,
        reply: SbiReply,
        *,
        sbi_token: bytes | None,
    ) -> bool:
        if self._sbi is None or self._sbi.autoprint_active:
            return False
        if sbi_token == TOKEN_PRINT:
            return False
        return any(is_autoprint_line(line.raw) for line in reply.lines)

    def _raise_sbi_autoprint_active(
        self,
        command_name: str,
        *,
        sbi_token: bytes | None = None,
    ) -> None:
        raise SartoriusAutoprintActiveError(
            "SBI autoprint is active; command replies are not reliable. "
            "Open with identify=False and use stream(mode='autoprint') or poll(), "
            "or disable autoprint on the balance before using SBI command/reply APIs.",
            context=ErrorContext(
                command_name=command_name,
                sbi_token=sbi_token,
                protocol="sbi",
                extra={"autoprint_active": True},
            ),
        )

    # ------------------------------------------------------------------ lifecycle

    async def close(self) -> None:
        """Close the underlying transport, if one is wired.

        Idempotent — safe to call multiple times. The factory owns the
        transport's construction and hands it into the session via the
        protocol client; closing the session closes the transport.

        Both clients hold the same transport, so close it once via the
        session's :attr:`transport` accessor rather than once per client
        slot — guards against a future caller that wires both clients
        simultaneously.
        """
        await self.transport.close()
