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
"""

from __future__ import annotations

import struct

from sartoriuslib.errors import ErrorContext, SartoriusValidationError
from sartoriuslib.protocol.sbi import (
    LINE_TERMINATOR,
    TOKEN_SERIAL,
    TOKEN_SOFTWARE,
    TOKEN_TYPE,
    normalize_token,
)
from sartoriuslib.protocol.xbpi import build_command, checksum
from sartoriuslib.transport.fake import FakeTransport, ScriptedReply

__all__ = [
    "CannedFrames",
    "FakeTransport",
    "ScriptedReply",
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
) -> dict[bytes, bytes]:
    """Script the per-sensor replies for ``temperature(N)`` calls.

    ``sensor_celsius`` maps sensor index → temperature in °C, or ``None``
    to script the ``7f ff ff ff`` "reserved slot" sentinel for that
    index. ``out_of_range_after`` adds an xBPI ``0x04`` (unknown opcode)
    reply for index ``N`` (and the helper does NOT script higher
    indices, mirroring the wire reality where the device stops
    replying past the end). Useful for testing
    :meth:`Balance.discover_temperature_sensors` against a sparse
    layout (e.g. MSE: ``{0: 25.5, 1: 25.6, 2: None, 3: 36.7}`` +
    ``out_of_range_after=4``).
    """
    sentinel_body = b"\x7f\xff\xff\xff\xff"
    script: dict[bytes, bytes] = {}
    for sensor, celsius in sensor_celsius.items():
        tx = build_command(0x76, bytes([0x21, sensor]))
        if celsius is None:
            script[tx] = _rx(0x35, sentinel_body)
        else:
            script[tx] = _typed_float_rx(celsius)
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
