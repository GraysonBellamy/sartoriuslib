"""Conservative protocol auto-detection.

Drain input → passively sniff for SBI autoprint → probe xBPI (``0x02``) →
probe SBI (``ESC x1_`` then ``ESC P`` fallback) → fail clearly. Never sweeps
opcodes, never changes baud, never changes the balance's protocol mode. See
design doc §4.3.

The SBI probe tries ``ESC x1_`` first (which gives an identity string we
can validate later) and falls back to ``ESC P`` (a print/weight read) if
the identity token is silent. The ``ESC P`` fallback is kept as
defense-in-depth for unknown firmware revisions: an earlier hardware-day
note claimed Cubis MSE1203S silently ignored Format-2 identity tokens,
but re-testing on 2026-04-25 (MSE1203S-100-DR, BAC ``00-39-21``) showed
all three identity tokens reply cleanly. Both probes are READ_ONLY,
so the fallback costs nothing on devices that do reply to ``ESC x1_``.

The result is a :class:`DetectionResult` carrying the resolved
:class:`ProtocolKind` plus, when autoprint was observed, the sniffed line so
the caller can re-queue it on the eventual SBI client and not lose the first
sample. Anything observed during the sniff that does *not* match an autoprint
pattern is discarded — those bytes are ambiguous and we drain before each
subsequent probe.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

from sartoriuslib.errors import (
    ErrorContext,
    SartoriusError,
    SartoriusProtocolError,
    SartoriusTimeoutError,
)
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi.framing import LINE_TERMINATOR, is_autoprint_line
from sartoriuslib.protocol.sbi.tables import TOKEN_PRINT, TOKEN_TYPE
from sartoriuslib.protocol.xbpi.framing import (
    BALANCE_SBN_DEFAULT,
    HOST_SBN_DEFAULT,
    build_command,
    parse_frame,
)

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport


__all__ = ["DetectionResult", "detect_protocol"]


_DEFAULT_SNIFF_WINDOW: float = 0.25
_DEFAULT_PROBE_TIMEOUT: float = 1.0
# READ_MODEL — the universal identity opcode, supported on every family
# that speaks xBPI (MSE / WZA / BCE captures all reply to it). Using a
# getter keeps the probe READ_ONLY: the device cannot change state.
_XBPI_PROBE_OPCODE: int = 0x02
_MIN_SNIFF_REMAINING_S: float = 0.001


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of :func:`detect_protocol`.

    Attributes:
        protocol: The resolved :class:`ProtocolKind`. Always ``XBPI`` or
            ``SBI`` — never ``AUTO`` (a successful detect has resolved it).
        autoprint_active: ``True`` only when the SBI passive sniff observed
            an unsolicited autoprint/status line.
        pending_lines: Complete CRLF-terminated SBI lines consumed during
            the sniff that the caller may want to re-queue on the live
            client. Empty unless ``autoprint_active`` is ``True``.
    """

    protocol: ProtocolKind
    autoprint_active: bool = False
    pending_lines: tuple[bytes, ...] = field(default_factory=tuple)


async def detect_protocol(
    transport: Transport,
    *,
    timeout: float = _DEFAULT_PROBE_TIMEOUT,
    sniff_window: float = _DEFAULT_SNIFF_WINDOW,
    src_sbn: int = HOST_SBN_DEFAULT,
    dst_sbn: int = BALANCE_SBN_DEFAULT,
) -> DetectionResult:
    """Detect xBPI vs SBI on an already-open ``transport``.

    Runs the conservative sequence from design §4.3 in order — drain →
    passive sniff → xBPI probe → SBI probe → fail. Each probe writes at
    most one frame. The transport's serial settings are never changed.

    Arguments:
        transport: An open :class:`Transport`. Caller owns lifecycle.
        timeout: Per-probe timeout for the xBPI and SBI identity probes.
        sniff_window: Passive listen window for SBI autoprint, in seconds.
        src_sbn: Source SBN for the xBPI probe frame (``0x01`` host
            convention by default).
        dst_sbn: Destination SBN for the xBPI probe frame (``0x09``
            balance factory default by default).

    Returns:
        A :class:`DetectionResult` whose ``protocol`` is ``XBPI`` or
        ``SBI``. When ``autoprint_active`` is ``True``, ``pending_lines``
        carries the sniffed bytes for re-queue.

    Raises:
        SartoriusError: No xBPI or SBI device responded — neither the
            passive sniff nor either probe produced a recognisable reply.
            Hard transport faults (e.g. the port closed mid-detect)
            propagate as :class:`SartoriusConnectionError` unchanged.
    """
    label = transport.label

    # 1. Passive autoprint sniff. We deliberately do NOT drain first: a
    #    balance left in autoprint mode may have a complete line already
    #    sitting in the OS buffer when we connect, and the design promises
    #    we will not drop the first sample. Stale partial bytes (no CRLF)
    #    can't fool the sniff — read_until times out without consuming them
    #    and the pre-probe drain below clears them before xBPI runs.
    autoprint, sniffed = await _sniff_for_autoprint(transport, sniff_window)
    if autoprint:
        return DetectionResult(
            protocol=ProtocolKind.SBI,
            autoprint_active=True,
            pending_lines=sniffed,
        )

    # 2. Drain anything left over from the sniff (partial lines, stray
    #    bytes that didn't form CRLF) so the xBPI length-prefix read starts
    #    from a clean buffer.
    with contextlib.suppress(SartoriusError):
        await transport.drain_input()

    # 3. xBPI probe — READ_MODEL. A valid frame (even one carrying an error
    #    subtype) confirms xBPI: only an xBPI device builds a length-prefixed
    #    marker-tagged frame.
    if await _probe_xbpi(transport, timeout=timeout, src_sbn=src_sbn, dst_sbn=dst_sbn):
        return DetectionResult(protocol=ProtocolKind.XBPI)

    with contextlib.suppress(SartoriusError):
        await transport.drain_input()

    # 4. SBI identity probe. Any CRLF-terminated reply counts as evidence —
    #    content interpretation is the SBI parser's job, not detection's.
    if await _probe_sbi(transport, timeout=timeout):
        return DetectionResult(protocol=ProtocolKind.SBI)

    # 5. Clear failure. No opcode sweeps, no fallback baud rates.
    raise SartoriusError(
        f"auto-detect: no responsive xBPI or SBI device on {label!r} "
        f"(sniff {sniff_window}s, probe timeout {timeout}s)",
        context=ErrorContext(
            port=label,
            command_name="auto_detect",
            extra={"sniff_window_s": sniff_window, "probe_timeout_s": timeout},
        ),
    )


async def _sniff_for_autoprint(
    transport: Transport,
    sniff_window: float,
) -> tuple[bool, tuple[bytes, ...]]:
    """Passively read complete CRLF lines for up to ``sniff_window`` seconds.

    Stops early on the first line matching :func:`is_autoprint_line` and
    returns ``(True, (matching_line,))`` so the caller can preserve it.
    Otherwise returns ``(False, ())`` — the lines we read but didn't
    classify as autoprint are dropped on purpose; they were ambiguous and
    re-queueing them risks misleading the next probe.
    """
    if sniff_window <= 0:
        return False, ()
    deadline = anyio.current_time() + sniff_window
    while anyio.current_time() < deadline:
        remaining = max(_MIN_SNIFF_REMAINING_S, deadline - anyio.current_time())
        try:
            raw = await transport.read_until(LINE_TERMINATOR, timeout=remaining)
        except SartoriusTimeoutError:
            return False, ()
        if is_autoprint_line(raw):
            return True, (raw,)
    return False, ()


async def _probe_xbpi(
    transport: Transport,
    *,
    timeout: float,
    src_sbn: int,
    dst_sbn: int,
) -> bool:
    """Send xBPI ``READ_MODEL`` and verify the response framing.

    Returns ``True`` when a length-prefixed frame with the correct marker
    and checksum comes back — even if it carries an error subtype, that
    still proves the device speaks xBPI. Timeouts and framing errors
    return ``False``.
    """
    request = build_command(_XBPI_PROBE_OPCODE, src_sbn=src_sbn, dst_sbn=dst_sbn)
    try:
        await transport.write(request, timeout=timeout)
        length_byte = await transport.read_exact(1, timeout=timeout)
        body = await transport.read_exact(length_byte[0], timeout=timeout)
        parse_frame(length_byte + body)
    except (SartoriusTimeoutError, SartoriusProtocolError):
        return False
    return True


async def _probe_sbi(transport: Transport, *, timeout: float) -> bool:
    """Probe for SBI line framing.

    Tries ``ESC x1_`` first (identity), then ``ESC P`` (print weight)
    if the identity probe is silent. The ``ESC P`` fallback is
    defense-in-depth for unknown firmware revisions; on the verified
    MSE1203S BAC ``00-39-21`` test unit (2026-04-25) all Format-2
    identity tokens reply cleanly, so the first probe usually
    succeeds. Both tokens are READ_ONLY (no state change), so the
    fallback is zero-risk on devices that do reply to ``ESC x1_``.

    .. note::
        ``ESC P`` has a documented side effect on MSE in autoprint
        mode: it pauses the autoprint stream until the next
        ``ESC V``/``ESC T`` or front-panel zero/tare. Detection runs
        AFTER the passive autoprint sniff, so a positive autoprint
        result short-circuits before we'd send ``ESC P`` — but if
        autoprint is paused (e.g. a prior session left it that way)
        and detection falls through to ``ESC P``, the fallback may
        keep autoprint paused. Callers that need autoprint can
        resume it by sending ``ESC V`` or by re-enabling from the
        front panel.

    Returns ``True`` as soon as either probe gets a CRLF-terminated
    line back; detection only needs evidence that something on the
    wire speaks SBI framing. Content interpretation is the SBI
    parser's job.

    Drains the input buffer between probes so a stale partial reply
    from the first attempt cannot fool the second.
    """
    for token in (TOKEN_TYPE, TOKEN_PRINT):
        try:
            await transport.write(token, timeout=timeout)
            await transport.read_until(LINE_TERMINATOR, timeout=timeout)
        except (SartoriusTimeoutError, SartoriusProtocolError):
            with contextlib.suppress(SartoriusError):
                await transport.drain_input()
            continue
        return True
    return False
