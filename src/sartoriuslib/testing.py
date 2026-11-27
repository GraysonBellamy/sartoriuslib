r"""First-class public testing support — :mod:`sartoriuslib.testing`.

Re-exports the test doubles and fixture helpers used to drive
:mod:`sartoriuslib` without real hardware. See design doc §8.2.

Exposed now:

- :class:`FakeTransport` — scripted in-process :class:`Transport`.
- :class:`ScriptedReply` — the type alias for scripted-reply values.
- :class:`CannedFrames` — reference xBPI wire frames from real balances,
  available as ``canned_frames``.
- :func:`build_identify_script` — assemble a scripted ``{tx: rx}`` dict
  for :func:`open_device` identify sequences (model / manufacturer /
  software / factory / SBN).
- :func:`parse_xbpi_fixture` — turn a text fixture file (``> send`` /
  ``< reply`` lines, ``#`` comments) into a scripted mapping ready for
  :class:`FakeTransport`.
- :func:`parse_sbi_fixture` — the SBI counterpart, accepting readable
   tokens like ``ESC P``.
- :class:`AdjustmentSimulator` — a scripted Cubis balance running internal
  adjustments on the measured MSE1203S timeline, for exercising
  ``internal_adjust(wait=True)`` / ``wait_for_adjustment()`` /
  ``abort_adjustment()`` without hardware.
- :func:`build_cal_record_body` — a ``0xB9`` cal-record body from a
  temperature and timestamp.
"""

from __future__ import annotations

import struct
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sartoriuslib.errors import ErrorContext, SartoriusValidationError
from sartoriuslib.protocol.sbi import (
    LINE_TERMINATOR,
    TOKEN_SERIAL,
    TOKEN_SOFTWARE,
    TOKEN_TYPE,
    normalize_token,
)
from sartoriuslib.protocol.xbpi import (
    BALANCE_SBN_DEFAULT,
    HOST_SBN_DEFAULT,
    build_command,
    checksum,
)
from sartoriuslib.transport.fake import FakeTransport, ScriptedReply

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "MSE_ADJUST_ABORT_TAIL_S",
    "MSE_INTERNAL_ADJUST_DONE_AT_S",
    "MSE_INTERNAL_ADJUST_RECORD_AT_S",
    "MSE_INTERNAL_ADJUST_TIMELINE",
    "AdjustmentSimulator",
    "CannedFrames",
    "FakeTransport",
    "ScriptedReply",
    "build_cal_record_body",
    "build_identify_script",
    "build_metrology_script",
    "build_parameter_read_script",
    "build_parameter_write_script",
    "build_sbi_identify_script",
    "build_temperature_script",
    "canned_frames",
    "parse_sbi_fixture",
    "parse_xbpi_fixture",
]


# ---------------------------------------------------------------------------
# Canned frames — real balance replies, byte-accurate.
# ---------------------------------------------------------------------------


def _rx(subtype: int, body: bytes) -> bytes:
    """Synthesise a balance→host xBPI frame carrying ``body``.

    ``length`` = ``marker + subtype + body + chk`` bytes (everything that
    follows the length byte itself). See ``docs/protocol.md`` §3.1.
    """
    length = 1 + 1 + len(body) + 1
    pre = bytes([length, 0x41, subtype]) + body
    return pre + bytes([checksum(pre)])


def _ascii_blob(subtype: int, size: int, text: str) -> bytes:
    """Synthesise an xBPI frame carrying ``text`` as a null-padded ASCII blob."""
    body = text.encode("ascii").ljust(size, b"\x00")
    return _rx(subtype, body)


def _short_data_u8(value: int) -> bytes:
    """Synthesise an xBPI short_data (subtype 0x21, 1-byte body) reply."""
    return _rx(0x21, bytes([value]))


class CannedFrames:
    """Reference xBPI wire frames from real balances.

    Attribute values are TX/RX :class:`bytes` ready to drop into a
    :class:`FakeTransport` script. RX frames use checksums validated by
    :func:`sartoriuslib.protocol.xbpi.checksum`.
    """

    # TX — host→balance frames (assembled via build_command).
    TX_READ_SBN: bytes = build_command(0x71)
    TX_READ_NET: bytes = build_command(0x1E)
    TX_TARE: bytes = build_command(0x14)
    TX_ZERO: bytes = build_command(0x18)
    TX_READ_STATUS_BLOCK: bytes = build_command(0x30)
    TX_READ_MODEL: bytes = build_command(0x02)
    TX_READ_MANUFACTURER: bytes = build_command(0x07)
    TX_READ_SW_VERSION: bytes = build_command(0x00)
    TX_READ_FACTORY_NUMBER: bytes = build_command(0x01)

    # RX — balance→host frames.
    #: docs/protocol.md §3.3 — empty-pan reading, -0.005 g stable negative.
    RX_NET_WEIGHT_EMPTY_PAN: bytes = bytes.fromhex(
        "0b4148bba3d70a3d30824507",
    )
    #: docs/protocol.md §3.3 — SBN = 0x00.
    RX_SBN_00: bytes = bytes.fromhex("0441210066")
    #: docs/protocol.md §11.1 — tare / zero / menu-EEPROM ACK reply.
    RX_ACK: bytes = bytes.fromhex("03410044")
    #: MSE1203S-100-DR model string (§7.1 capture). 20-byte null-padded ASCII.
    RX_MODEL_MSE: bytes = _ascii_blob(0x54, 20, "MSE1203S-100-DR")
    #: WZA8202-N model string — classifies as OEM_WEIGH_CELL.
    RX_MODEL_WZA: bytes = _ascii_blob(0x54, 20, "WZA8202-N")
    #: BCE3202-1S model string — classifies as BASIC_LAB.
    RX_MODEL_BCE: bytes = _ascii_blob(0x54, 20, "BCE3202-1S")
    #: ``Sartorius`` — 16-byte null-padded ASCII (§7.1, opcodes 0x05 / 0x07).
    RX_MANUFACTURER_SARTORIUS: bytes = _ascii_blob(0x50, 16, "Sartorius")
    #: Software-version blob from our MSE unit (§7.1). Subtype 0x4A, 10 bytes.
    RX_SW_VERSION_MSE: bytes = _rx(
        0x4A,
        bytes([0x00, 0x39, 0x21, 0x00, 0x39, 0x01, 0x39, 0x01, 0x00, 0x01]),
    )
    #: Factory-number blob from our MSE unit (§7.1). Subtype 0x45, 5 bytes.
    RX_FACTORY_NUMBER_MSE: bytes = _rx(
        0x45,
        bytes([0x00, 0x31, 0x80, 0x11, 0x65]),
    )
    #: Cubis stable status block (§8.2): state=0x88, status=0x18.
    RX_STATUS_STABLE_CUBIS: bytes = _rx(
        0x48,
        bytes([0x00, 0x00, 0x81, 0x88, 0x18, 0x10, 0x00, 0x42]),
    )


canned_frames = CannedFrames()
"""Module-level singleton of :class:`CannedFrames` for ergonomic access
(``from sartoriuslib.testing import canned_frames``)."""


# ---------------------------------------------------------------------------
# Helper: build a {tx: rx} script that satisfies one full identify() run.
# ---------------------------------------------------------------------------


def build_identify_script(
    *,
    model: str = "MSE1203S-100-DR",
    manufacturer: str = "Sartorius",
    software: bytes | None = None,
    factory_number: bytes | None = None,
    sbn: int = 0x00,
) -> dict[bytes, bytes]:
    """Build a scripted transport mapping for :meth:`Balance.identify`.

    The returned dict covers every TX frame the identify sequence
    sends (model, manufacturer, software, factory number, SBN) so a
    :class:`FakeTransport` constructed with it can drive
    :func:`open_device` from end to end without hardware.
    """
    if software is None:
        software = bytes([0x00, 0x39, 0x21, 0x00, 0x39, 0x01, 0x39, 0x01, 0x00, 0x01])
    if factory_number is None:
        factory_number = bytes([0x00, 0x31, 0x80, 0x11, 0x65])

    return {
        canned_frames.TX_READ_MODEL: _ascii_blob(0x54, 20, model),
        canned_frames.TX_READ_MANUFACTURER: _ascii_blob(0x50, 16, manufacturer),
        canned_frames.TX_READ_SW_VERSION: _rx(0x4A, software),
        canned_frames.TX_READ_FACTORY_NUMBER: _rx(0x45, factory_number),
        canned_frames.TX_READ_SBN: _short_data_u8(sbn),
    }


# ---------------------------------------------------------------------------
# Metrology + parameter-table fixture helpers.
# ---------------------------------------------------------------------------


def _typed_float_rx(value: float, aux: int = 0x00) -> bytes:
    """Synthesise a typed_float (``0x35``) reply carrying ``value``."""
    body = struct.pack(">f", value) + bytes([aux])
    return _rx(0x35, body)


def _parameter_rx(current: int, max_value: int) -> bytes:
    """Synthesise a ``0x55`` parameter-table reply.

    The subtype byte ``0x21`` doubles as the first TLV tag, so the
    on-wire layout is ``[len][0x41][0x21][current][0x21][max][chk]``.
    """
    return _rx(0x21, bytes([current, 0x21, max_value]))


def build_metrology_script(
    *,
    capacity_g: float = 1200.0,
    increment_g: float = 0.001,
    config_counter: int | None = 1,
    area: int = 0,
) -> dict[bytes, bytes]:
    """Build a ``{tx: rx}`` script covering capacity / increment / counter.

    Pair with :func:`build_identify_script` to script a full
    open sequence (identity + metrology probe). Defaults match the
    MSE1203S unit in ``docs/protocol.md``.

    Pass ``config_counter=None`` to omit the ``0xBA`` reply entirely —
    use that when simulating a balance that genuinely lacks
    :attr:`Capability.CONFIG_COUNTER` (e.g. WZA). The new probe-driven
    capability detection in :meth:`Balance._probe_dispatch_capabilities`
    relies on a missing reply (not a falsy value) to decide the cap is
    absent, so unfaithful "always reply" fixtures would silently
    over-report the capability.
    """
    tx_capacity = build_command(0x0C, bytes([0x21, area]))
    tx_increment = build_command(0x0D, bytes([0x21, area]))
    script: dict[bytes, bytes] = {
        tx_capacity: _typed_float_rx(capacity_g),
        tx_increment: _typed_float_rx(increment_g),
    }
    if config_counter is not None:
        tx_counter = build_command(0xBA)
        script[tx_counter] = _short_data_u8(config_counter)
    return script


def build_temperature_script(
    *,
    sensor_celsius: dict[int, float | None],
    out_of_range_after: int | None = None,
    value_out_of_range_at: set[int] | None = None,
) -> dict[bytes, bytes]:
    """Script the per-sensor replies for ``temperature(N)`` calls.

    ``sensor_celsius`` maps sensor index → temperature in °C, or ``None``
    to script the ``7f ff ff ff`` "reserved slot" sentinel for that
    index. ``out_of_range_after`` adds an xBPI ``0x04`` (unknown opcode)
    reply for index ``N`` (and the helper does NOT script higher
    indices, mirroring the wire reality where the device stops
    replying past the end). ``value_out_of_range_at`` scripts a ``0x03``
    (value out of range) reply for each listed index — how a WZ8202
    reports an absent sensor slot instead of the sentinel. Useful for
    testing :meth:`Balance.discover_temperature_sensors` against a sparse
    layout (e.g. MSE: ``{0: 25.5, 1: 25.6, 2: None, 3: 36.7}`` +
    ``out_of_range_after=4``; WZ8202: ``{0: 20.0, 1: 19.8}`` +
    ``value_out_of_range_at={2, 3}`` + ``out_of_range_after=4``).
    """
    sentinel_body = b"\x7f\xff\xff\xff\xff"
    script: dict[bytes, bytes] = {}
    for sensor, celsius in sensor_celsius.items():
        tx = build_command(0x76, bytes([0x21, sensor]))
        if celsius is None:
            script[tx] = _rx(0x35, sentinel_body)
        else:
            script[tx] = _typed_float_rx(celsius)
    for sensor in value_out_of_range_at or set():
        tx = build_command(0x76, bytes([0x21, sensor]))
        # xBPI 0x01 0x03 = value out of range.
        script[tx] = _rx(0x01, b"\x03")
    if out_of_range_after is not None:
        tx = build_command(0x76, bytes([0x21, out_of_range_after]))
        # xBPI 0x01 0x04 = unknown/unsupported opcode.
        script[tx] = _rx(0x01, b"\x04")
    return script


def build_parameter_read_script(
    index: int,
    current: int,
    max_value: int,
) -> dict[bytes, bytes]:
    """Build a one-entry script for a ``read_parameter(index)`` call."""
    tx = build_command(0x55, bytes([0x21, index]))
    return {tx: _parameter_rx(current, max_value)}


def build_parameter_write_script(index: int, value: int) -> dict[bytes, bytes]:
    """Build a one-entry script for a ``write_parameter(index, value)`` call."""
    tx = build_command(0x56, bytes([0x21, index, 0x21, value]))
    return {tx: canned_frames.RX_ACK}


def build_sbi_identify_script(
    *,
    model: str = "WZA8202-N",
    serial: str = "12345678",
    software: str = "1.0",
) -> dict[bytes, bytes]:
    """Build a scripted SBI identity mapping for ``open_device(..., SBI)``."""
    return {
        TOKEN_TYPE: _sbi_line(model),
        TOKEN_SERIAL: _sbi_line(serial),
        TOKEN_SOFTWARE: _sbi_line(software),
    }


# ---------------------------------------------------------------------------
# Fixture-file parser — turns design §8.2 text fixtures into scripted dicts.
# ---------------------------------------------------------------------------


def parse_xbpi_fixture(text: str) -> dict[bytes, bytes]:
    r"""Parse an xBPI text fixture into a ``{tx_bytes: rx_bytes}`` mapping.

    Format (design §8.2)::

        # xBPI fixture: read net weight
        > 04 01 09 1e 2c
        < 0b 41 48 bb a3 d7 0a 3d 30 82 45 07

    - Blank lines and ``#``-comment lines are ignored.
    - ``>`` lines carry TX bytes (host→balance).
    - ``<`` lines carry RX bytes (balance→host); they attach to the most
      recent ``>`` line.
    - Hex digits may be separated by whitespace in any form.
    - Multiple ``<`` lines following one ``>`` concatenate into one
      reply blob (useful for synthesising multi-frame responses).

    Raises :class:`SartoriusValidationError` for malformed input (an
    ``<`` line before any ``>``, odd-length hex tokens, etc.).
    """
    mapping: dict[bytes, bytes] = {}
    current_tx: bytes | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        marker, _, rest = line.partition(" ")
        payload = rest.strip()
        if marker == ">":
            if not payload:
                raise SartoriusValidationError(
                    f"fixture line {lineno}: empty TX payload",
                    context=ErrorContext(extra={"line": lineno}),
                )
            current_tx = _decode_hex_tokens(payload, lineno)
            mapping.setdefault(current_tx, b"")
        elif marker == "<":
            if current_tx is None:
                raise SartoriusValidationError(
                    f"fixture line {lineno}: '<' reply before any '>' request",
                    context=ErrorContext(extra={"line": lineno}),
                )
            rx = _decode_hex_tokens(payload, lineno) if payload else b""
            mapping[current_tx] = mapping[current_tx] + rx
        else:
            raise SartoriusValidationError(
                f"fixture line {lineno}: unrecognised marker {marker!r} (expected '>' or '<')",
                context=ErrorContext(extra={"line": lineno, "marker": marker}),
            )
    return mapping


def parse_sbi_fixture(text: str) -> dict[bytes, bytes]:
    r"""Parse an SBI text fixture into a ``{tx_bytes: rx_bytes}`` mapping.

    Format::

        # SBI fixture: print
        > ESC P
        < +     0.00 g

    Reply lines get ``\r\n`` appended when the fixture omits a terminator.
    Multiple ``<`` lines after one ``>`` concatenate into a multi-line reply.
    """
    mapping: dict[bytes, bytes] = {}
    current_tx: bytes | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        marker, _, rest = line.partition(" ")
        payload = rest.strip()
        if marker == ">":
            if not payload:
                raise SartoriusValidationError(
                    f"fixture line {lineno}: empty SBI TX payload",
                    context=ErrorContext(extra={"line": lineno}),
                )
            try:
                current_tx = normalize_token(payload)
            except Exception as exc:
                raise SartoriusValidationError(
                    f"fixture line {lineno}: invalid SBI token {payload!r}",
                    context=ErrorContext(extra={"line": lineno, "payload": payload}),
                ) from exc
            mapping.setdefault(current_tx, b"")
        elif marker == "<":
            if current_tx is None:
                raise SartoriusValidationError(
                    f"fixture line {lineno}: '<' reply before any '>' request",
                    context=ErrorContext(extra={"line": lineno}),
                )
            mapping[current_tx] = mapping[current_tx] + _sbi_line(payload)
        else:
            raise SartoriusValidationError(
                f"fixture line {lineno}: unrecognised marker {marker!r} (expected '>' or '<')",
                context=ErrorContext(extra={"line": lineno, "marker": marker}),
            )
    return mapping


def _decode_hex_tokens(payload: str, lineno: int) -> bytes:
    """Turn whitespace-separated hex tokens into :class:`bytes`."""
    compact = "".join(payload.split())
    if len(compact) % 2 != 0:
        raise SartoriusValidationError(
            f"fixture line {lineno}: odd hex length in {payload!r}",
            context=ErrorContext(extra={"line": lineno, "payload": payload}),
        )
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise SartoriusValidationError(
            f"fixture line {lineno}: invalid hex in {payload!r}",
            context=ErrorContext(extra={"line": lineno, "payload": payload}),
        ) from exc


def _sbi_line(text: str) -> bytes:
    """Encode one SBI fixture line, adding CRLF when omitted."""
    raw = text.encode("ascii", errors="replace")
    if raw.endswith(LINE_TERMINATOR):
        return raw
    return raw.rstrip(b"\r\n") + LINE_TERMINATOR


# ---------------------------------------------------------------------------
# Internal-adjustment simulator — replays the MSE1203S bench timeline.
# ---------------------------------------------------------------------------

#: State/status timeline of one MSE1203S internal adjustment, measured at
#: ~200 Hz (``docs/protocol.md`` §7.7.1, capture
#: ``tests/fixtures/captures/mse_xbpi/internal_adjust/run_g_fast.jsonl.gz``).
#: Seconds after the ``0x28`` ACK → (state byte, status byte). Includes the
#: 0.2 s window at +5.957 s where neither adjust bit is set.
MSE_INTERNAL_ADJUST_TIMELINE: tuple[tuple[float, int, int], ...] = (
    (0.000, 0xC0, 0x08),
    (0.031, 0xC1, 0x08),
    (1.002, 0xC9, 0x08),
    (5.954, 0xC9, 0x28),
    (5.957, 0x88, 0x28),
    (6.159, 0x89, 0x28),
    (8.333, 0x81, 0x08),
    (10.513, 0x89, 0x08),
    (15.265, 0x89, 0x28),
    (17.247, 0x81, 0x08),
    (19.228, 0x89, 0x08),
    (23.983, 0x89, 0x28),
)
#: When ``0xB9`` is rewritten (seconds after the ACK).
MSE_INTERNAL_ADJUST_RECORD_AT_S: float = 24.211
#: When the state byte goes idle (seconds after the ACK).
MSE_INTERNAL_ADJUST_DONE_AT_S: float = 24.283
#: How long the adjust bit stays set after a mid-adjust ``0x29``.
MSE_ADJUST_ABORT_TAIL_S: float = 2.5

#: Weight readings while adjusting: off-scale until the internal weight is
#: in place, then a fake 1300 g, then 0 g (``docs/protocol.md`` §8.1).
_ADJUST_FAKE_1300_UNTIL_S: float = 15.265
_ADJUST_WEIGHT_PHASE_END_S: float = 5.957

_STATE_IDLE_UNSTABLE: int = 0x80
_STATE_IDLE_STABLE: int = 0x88
_STATUS_ADC_TRUSTED: int = 0x08
_STATUS_ISOCAL_DUE: int = 0x10
_STABLE_STATE_BIT: int = 0x08
_ERR_NOT_APPLICABLE: bytes = b"\x06"
_PARTIAL_REPLY_LEN: int = 3

type PollFault = Literal["silent", "partial", "busy"]
type AdjustOutcome = Literal["complete", "hang", "ignore"]


def build_cal_record_body(
    *,
    temperature_celsius: float | None,
    timestamp: datetime | None,
) -> bytes:
    """Build a 17-byte ``0xB9`` cal-record body (``docs/protocol.md`` §7.12).

    ``timestamp`` is encoded one decimal digit per byte as
    ``DDMMYYhhmmss``; ``None`` leaves those bytes zero (the post-cold-boot
    "no record" shape). ``temperature_celsius=None`` writes the all-zero
    "unset" sentinel.
    """
    temp = (
        b"\x00\x00\x00\x00"
        if temperature_celsius is None
        else struct.pack(">f", temperature_celsius)
    )
    if timestamp is None:
        stamp = bytes(12)
    else:
        text = timestamp.strftime("%d%m%y%H%M%S")
        stamp = bytes(int(c) for c in text)
    return temp + stamp + b"\x00"


class AdjustmentSimulator:
    """Scripted Cubis balance running internal adjustments, for :class:`FakeTransport`.

    Installs handlers for ``0x28`` (start), ``0x29`` (abort), ``0x30`` /
    ``0x32`` (status), ``0xB9`` (cal record) and ``0x1E`` (net weight) that
    replay :data:`MSE_INTERNAL_ADJUST_TIMELINE`, the mid-adjust gap
    included. Combine with :func:`build_identify_script` /
    :func:`build_metrology_script` to drive :meth:`Balance.internal_adjust`
    (``wait=True``), :meth:`Balance.wait_for_adjustment` and
    :meth:`Balance.abort_adjustment` end to end::

        sim = AdjustmentSimulator(speed=100.0)  # 24 s adjust in ~0.25 s
        transport = FakeTransport({**build_identify_script(), **build_metrology_script()})
        sim.install(transport)
        balance = await open_device(transport)
        result = await balance.internal_adjust(confirm=True, wait=True)

    Behaviour matches the bench: ``0x28`` while running → err ``0x06``;
    ``0x29`` while running → ACK, then idle :data:`MSE_ADJUST_ABORT_TAIL_S`
    later with the cal record unchanged; ``0x29`` at rest → err ``0x06``.

    Time:
        By default simulated time is host monotonic time × ``speed``.
        Pass ``step_per_status_poll`` instead to advance a virtual clock
        by that many simulated seconds on every status request; pass
        :meth:`now` as the ``clock`` of
        :func:`sartoriuslib.devices.adjustment.wait_for_adjustment` for
        fully deterministic tests.

    Arguments:
        speed: Simulated seconds per real second (real-time mode).
        step_per_status_poll: Virtual-clock mode; see above.
        outcome: ``"complete"`` (default), ``"hang"`` (never finishes) or
            ``"ignore"`` (ACK ``0x28`` but never start).
        previous_record: ``0xB9`` body before the first adjustment.
        new_record: ``0xB9`` body written by the first completed
            adjustment. Later completions advance its timestamp by four
            minutes each, so every run produces a distinct record.
        net_value: Net weight reported at rest.
        isocal_due: Whether status bit ``0x10`` is set at rest before the
            first completed adjustment (a completed one clears it).
        poll_faults: Status-request index (0-based, counting ``0x30`` and
            ``0x32``) → ``"silent"`` (no reply), ``"partial"`` (truncated
            frame) or ``"busy"`` (err ``0x06``).
        src_sbn / dst_sbn: Addressing of the scripted request frames.
    """

    def __init__(
        self,
        *,
        speed: float = 1.0,
        step_per_status_poll: float | None = None,
        outcome: AdjustOutcome = "complete",
        previous_record: bytes | None = None,
        new_record: bytes | None = None,
        net_value: float = 0.0,
        isocal_due: bool = True,
        poll_faults: Mapping[int, PollFault] | None = None,
        src_sbn: int = HOST_SBN_DEFAULT,
        dst_sbn: int = BALANCE_SBN_DEFAULT,
    ) -> None:
        if speed <= 0:
            raise SartoriusValidationError(
                f"speed must be positive, got {speed}",
                context=ErrorContext(extra={"speed": speed}),
            )
        self._speed = speed
        self._step = step_per_status_poll
        self._outcome = outcome
        self._record = previous_record or build_cal_record_body(
            temperature_celsius=32.82, timestamp=datetime(2026, 11, 24, 19, 5, 50)
        )
        self._new_temp, self._new_stamp = 26.91, datetime(2026, 11, 27, 15, 56, 49)
        self._new_record_override = new_record
        self._net_value = net_value
        self._isocal_due = isocal_due
        self._faults: dict[int, PollFault] = dict(poll_faults or {})
        self._src = src_sbn
        self._dst = dst_sbn
        self._origin = time.monotonic()
        self._vt = 0.0
        self._t_start: float | None = None
        self._t_abort: float | None = None
        self._record_written_for: float | None = None
        self.status_requests = 0
        self.completed = 0
        self.aborted = 0
        #: Optional hook called on every status request, before the reply
        #: is produced — useful for timing a cancellation in a test.
        self.on_status_request: Callable[[], None] | None = None

    # -- clock / state ------------------------------------------------------

    def now(self) -> float:
        """Current simulated time in seconds."""
        if self._step is not None:
            return self._vt
        return (time.monotonic() - self._origin) * self._speed

    @property
    def running(self) -> bool:
        """Whether an adjustment is in progress (as of the last request)."""
        return self._t_start is not None

    @property
    def cal_record_body(self) -> bytes:
        """The current 17-byte ``0xB9`` body."""
        return self._record

    def start(self) -> None:
        """Start an adjustment as if the balance began one itself (isoCAL)."""
        self._t_start = self.now()
        self._t_abort = None

    def _next_record(self) -> bytes:
        if self.completed == 0 and self._new_record_override is not None:
            return self._new_record_override
        stamp = self._new_stamp + timedelta(minutes=4 * self.completed)
        return build_cal_record_body(temperature_celsius=self._new_temp, timestamp=stamp)

    def _phase(self) -> tuple[int, int, float | None]:
        """``(state, status, seconds-into-adjust or None at rest)``; advances completion."""
        idle_status = _STATUS_ADC_TRUSTED | (_STATUS_ISOCAL_DUE if self._isocal_due else 0)
        if self._t_start is None:
            return _STATE_IDLE_STABLE, idle_status, None
        now = self.now()
        t = now - self._t_start
        if self._t_abort is not None:
            if now >= self._t_abort + MSE_ADJUST_ABORT_TAIL_S:
                self._t_start = self._t_abort = None
                self.aborted += 1
                return _STATE_IDLE_STABLE, idle_status, None
            return 0x81, _STATUS_ADC_TRUSTED, t
        if self._outcome == "complete" and t >= MSE_INTERNAL_ADJUST_DONE_AT_S:
            if self._record_pending:
                self._write_record()
            self._t_start = None
            self.completed += 1
            self._isocal_due = False
            return _STATE_IDLE_STABLE, _STATUS_ADC_TRUSTED, None
        if (
            self._outcome == "complete"
            and t >= MSE_INTERNAL_ADJUST_RECORD_AT_S
            and self._record_pending
        ):
            self._write_record()
        state, status = next(
            (s, st) for t0, s, st in reversed(MSE_INTERNAL_ADJUST_TIMELINE) if t >= t0
        )
        return state, status | (_STATUS_ISOCAL_DUE if self._isocal_due else 0), t

    @property
    def _record_pending(self) -> bool:
        return self._record_written_for != self._t_start

    def _write_record(self) -> None:
        self._record = self._next_record()
        self._record_written_for = self._t_start

    # -- wire ---------------------------------------------------------------

    def install(self, transport: FakeTransport) -> None:
        """Register this simulator's handlers on ``transport``."""

        def tx(opcode: int, args: bytes = b"") -> bytes:
            return build_command(opcode, args, src_sbn=self._src, dst_sbn=self._dst)

        transport.add_script(tx(0x28, b"\x21\x78"), self._on_adjust)
        transport.add_script(tx(0x29), self._on_abort)
        transport.add_script(tx(0x30), self._on_status_block)
        transport.add_script(tx(0x32), self._on_status_short)
        transport.add_script(tx(0xB9), lambda _: _rx(0x51, self._record))
        transport.add_script(tx(0x1E), self._on_net)

    def _on_adjust(self, _: bytes) -> bytes:
        self._phase()  # settle any run that has already finished
        if self._t_start is not None:
            return _rx(0x01, _ERR_NOT_APPLICABLE)
        if self._outcome != "ignore":
            self.start()
        return canned_frames.RX_ACK

    def _on_abort(self, _: bytes) -> bytes:
        self._phase()
        if self._t_start is None:
            return _rx(0x01, _ERR_NOT_APPLICABLE)
        if self._t_abort is None:
            self._t_abort = self.now()
        return canned_frames.RX_ACK

    def _status_request(self) -> tuple[PollFault | None, int, int]:
        index = self.status_requests
        self.status_requests += 1
        if self._step is not None:
            self._vt += self._step
        if self.on_status_request is not None:
            self.on_status_request()
        state, status, _ = self._phase()
        return self._faults.get(index), state, status

    def _faulted(self, fault: PollFault | None, frame: bytes) -> bytes:
        if fault == "silent":
            return b""
        if fault == "partial":
            return frame[:_PARTIAL_REPLY_LEN]
        if fault == "busy":
            return _rx(0x01, _ERR_NOT_APPLICABLE)
        return frame

    def _on_status_block(self, _: bytes) -> bytes:
        fault, state, status = self._status_request()
        body = bytes([0x00, 0x00, 0x81, state, status, 0x10, 0x00, self.status_requests & 0xFF])
        return self._faulted(fault, _rx(0x48, body))

    def _on_status_short(self, _: bytes) -> bytes:
        fault, state, status = self._status_request()
        return self._faulted(fault, _rx(0x22, bytes([state, status])))

    def _on_net(self, _request: bytes) -> bytes:
        state, _status, t = self._phase()
        if t is None:
            flags = 0x45 if state & _STABLE_STATE_BIT else 0x05
            sign = 0x40 if self._net_value > 0 else (0x80 if self._net_value < 0 else 0x00)
            body = struct.pack(">f", self._net_value) + bytes([0x00, 0x30, sign | 0x02, flags])
            return _rx(0x48, body)
        if t < _ADJUST_WEIGHT_PHASE_END_S:
            return _rx(0x48, b"\x7f\xff\xff\xff\xff\x30\x02\x00")
        value = 1300.0 if t < _ADJUST_FAKE_1300_UNTIL_S else 0.0
        flags = 0x41 if state & _STABLE_STATE_BIT else 0x01
        return _rx(0x48, struct.pack(">f", value) + bytes([0x00, 0x30, 0x02, flags]))
