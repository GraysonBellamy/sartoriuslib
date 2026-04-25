"""xBPI and SBI fixtures produce equivalent common ``Reading`` fields.

Keystone parity test: every captured balance state that *can* round-trip
through both protocols' weight-read decoders must produce the same
public ``Reading`` fields modulo ``protocol``, ``raw``, ``sequence``,
``status_flags``, ``received_at``, and ``monotonic_ns``.

Off-scale states (overload / underload) are intentionally excluded —
xBPI ``READ_NET`` decoders leave both flags ``False`` because the short
measurement body cannot disambiguate the two; the canonical xBPI source
of truth is ``STATUS_BLOCK`` (``0x30``), which yields a different shape
(:class:`BalanceStatus`). SBI's ``H`` / ``L`` autoprint markers feed
overload / underload through the weight-line parser directly. That
asymmetry is documented in :mod:`sartoriuslib.commands.weight` and
covered by family-specific tests; it is not a parity violation.
"""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

from sartoriuslib.commands.base import CommandContext
from sartoriuslib.commands.weight import READ_NET
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi import parse_reply, require_reading
from sartoriuslib.protocol.xbpi import checksum, parse_frame

if TYPE_CHECKING:
    from sartoriuslib.devices.models import Reading

# ---------------------------------------------------------------------------
# xBPI short-measurement fixture builder (subtype 0x48, 8-byte body).
# ---------------------------------------------------------------------------


def _xbpi_body(
    *,
    value: float,
    decimals: int,
    sign_bits: int,
    unit_id: int,
    stable: bool,
) -> bytes:
    """Pack a short-measurement body per ``docs/protocol.md`` §8.

    ``sign_bits`` is the top-2-bit sign field of byte [6]: ``0x00`` zero,
    ``0x40`` positive, ``0x80`` negative. ``unit_id`` is the low-6-bit
    base-unit ID (``0x02`` g, ``0x0D`` mg, ``0x20`` unknown, …).
    """
    flt = struct.pack(">f", value)
    aux = b"\x00"
    byte5 = bytes([(decimals & 0x0F) << 4])
    byte6 = bytes([(sign_bits & 0xC0) | (unit_id & 0x3F)])
    byte7 = bytes([0x40 if stable else 0x00])
    return flt + aux + byte5 + byte6 + byte7


def _rx_frame(body: bytes) -> bytes:
    """Wrap an 8-byte body into a complete xBPI rx frame (subtype 0x48)."""
    pre = bytes([1 + 1 + len(body) + 1, 0x41, 0x48]) + body
    return pre + bytes([checksum(pre)])


def _decode_xbpi(body: bytes) -> Reading:
    frame = parse_frame(_rx_frame(body))
    assert READ_NET.xbpi is not None
    return READ_NET.xbpi.decode(frame, CommandContext(protocol=ProtocolKind.XBPI))


def _decode_sbi(line: bytes) -> Reading:
    return require_reading(parse_reply(line))


def _assert_parity(xbpi: Reading, sbi: Reading) -> None:
    """Compare common fields. Documented divergences live in the module docstring."""
    assert xbpi.protocol is ProtocolKind.XBPI
    assert sbi.protocol is ProtocolKind.SBI
    if xbpi.value is None or sbi.value is None:
        assert xbpi.value is sbi.value
    else:
        # float32 round-trip on the xBPI side perturbs the value by ~1e-7;
        # the SBI side is a Python float of the printed decimal. Compare
        # with a tolerance loose enough for either source.
        assert math.isclose(sbi.value, xbpi.value, rel_tol=1e-5, abs_tol=1e-9)
    assert sbi.unit is xbpi.unit
    assert sbi.sign is xbpi.sign
    assert sbi.stable is xbpi.stable
    assert sbi.overload is xbpi.overload
    assert sbi.underload is xbpi.underload
    assert sbi.decimals == xbpi.decimals


# ---------------------------------------------------------------------------
# Cases.
# ---------------------------------------------------------------------------


def test_zero_grams_stable() -> None:
    body = _xbpi_body(value=0.0, decimals=2, sign_bits=0x00, unit_id=0x02, stable=True)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"+     0.00 g  \r\n"))


def test_positive_grams_stable() -> None:
    body = _xbpi_body(value=1.23, decimals=2, sign_bits=0x40, unit_id=0x02, stable=True)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"+     1.23 g  \r\n"))


def test_negative_milligrams_stable() -> None:
    body = _xbpi_body(value=-12.345, decimals=3, sign_bits=0x80, unit_id=0x0D, stable=True)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"-   12.345 mg \r\n"))


def test_unstable_positive_grams() -> None:
    body = _xbpi_body(value=1.0, decimals=1, sign_bits=0x40, unit_id=0x02, stable=False)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"?+     1.0 g\r\n"))


def test_space_sign_integer_grams() -> None:
    body = _xbpi_body(value=123.0, decimals=0, sign_bits=0x40, unit_id=0x02, stable=True)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"     123 g  \r\n"))


def test_22char_id_prefix_unknown_unit() -> None:
    # 0x20 is not in the xBPI unit-ID table; SBI "pcs" likewise has no
    # mapping. Both decoders converge on Unit.UNKNOWN, which is the
    # forward-compatibility contract for unrecognised units.
    body = _xbpi_body(value=253.0, decimals=0, sign_bits=0x40, unit_id=0x20, stable=True)
    _assert_parity(_decode_xbpi(body), _decode_sbi(b"Qnt +    253 pcs  \r\n"))
