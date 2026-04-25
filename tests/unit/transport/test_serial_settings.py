"""Tests for :class:`sartoriuslib.transport.SerialSettings` defaults."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from anyserial import ByteSize, Parity, StopBits

from sartoriuslib.transport import SerialSettings


def test_defaults_are_8_o_1_at_9600() -> None:
    """Default framing must be 8-O-1 per docs/protocol.md §2.1."""
    s = SerialSettings(port="/dev/null")
    assert s.baudrate == 9600
    assert s.bytesize is ByteSize.EIGHT
    assert s.parity is Parity.ODD
    assert s.stopbits is StopBits.ONE


def test_defaults_exclusive_is_true() -> None:
    """Two processes over one port corrupts xBPI framing — default exclusive."""
    assert SerialSettings(port="/dev/null").exclusive is True


def test_flow_control_defaults_off() -> None:
    s = SerialSettings(port="/dev/null")
    assert s.rtscts is False
    assert s.xonxoff is False


def test_replace_produces_new_settings() -> None:
    """dataclasses.replace is how SerialTransport.reopen mutates settings."""
    s = SerialSettings(port="/dev/null")
    s2 = replace(s, baudrate=1200, parity=Parity.EVEN)
    assert s2.baudrate == 1200
    assert s2.parity is Parity.EVEN
    # Original is untouched.
    assert s.baudrate == 9600
    assert s.parity is Parity.ODD


def test_settings_are_frozen() -> None:
    s = SerialSettings(port="/dev/null")
    with pytest.raises(FrozenInstanceError):
        s.baudrate = 1200  # type: ignore[misc]


class TestCoercion:
    """``__post_init__`` accepts native enums plus int/str/float shorthand
    and rejects garbage with :class:`SartoriusConfigurationError` rather
    than letting the wrong type leak into anyserial's termios layer (where
    it produces a confusing ``NoneType.iflag`` deep stack)."""

    def test_native_enums_pass_through(self) -> None:
        s = SerialSettings(
            port="/dev/null",
            bytesize=ByteSize.SEVEN,
            parity=Parity.EVEN,
            stopbits=StopBits.TWO,
        )
        assert s.bytesize is ByteSize.SEVEN
        assert s.parity is Parity.EVEN
        assert s.stopbits is StopBits.TWO

    # The static type of ``bytesize`` / ``parity`` / ``stopbits`` is the
    # corresponding enum — passing ``int`` / ``str`` is a type-checker
    # warning by design (the right strict form is the enum constant). The
    # tests below intentionally exercise the runtime fallback path that
    # ``__post_init__`` provides for ad-hoc callers (e.g. CLI args,
    # environment-derived strings); each call site that violates the
    # static type carries a single ``# type: ignore[arg-type]``.

    def test_stopbits_int_one_normalised(self) -> None:
        """The hardware-day reproducer: ``stopbits=1`` (raw int) used to crash
        anyserial mid-termios with a ``NoneType.iflag`` error. Now coerces."""
        s = SerialSettings(port="/dev/null", stopbits=1)  # type: ignore[arg-type]
        assert s.stopbits is StopBits.ONE

    def test_stopbits_int_two_normalised(self) -> None:
        s = SerialSettings(port="/dev/null", stopbits=2)  # type: ignore[arg-type]
        assert s.stopbits is StopBits.TWO

    def test_stopbits_float_one_point_five(self) -> None:
        """1.5 stop bits — the only non-integer value that maps to a real enum."""
        s = SerialSettings(port="/dev/null", stopbits=1.5)  # type: ignore[arg-type]
        assert s.stopbits is StopBits.ONE_POINT_FIVE

    def test_stopbits_float_integer_value(self) -> None:
        """``stopbits=2.0`` rounds to "2" → :class:`StopBits.TWO`."""
        s = SerialSettings(port="/dev/null", stopbits=2.0)  # type: ignore[arg-type]
        assert s.stopbits is StopBits.TWO

    def test_stopbits_str_normalised(self) -> None:
        s = SerialSettings(port="/dev/null", stopbits="2")  # type: ignore[arg-type]
        assert s.stopbits is StopBits.TWO

    def test_stopbits_invalid_int_raises_typed_error(self) -> None:
        from sartoriuslib.errors import SartoriusConfigurationError

        with pytest.raises(SartoriusConfigurationError) as exc_info:
            SerialSettings(port="/dev/null", stopbits=3)  # type: ignore[arg-type]
        # Error message should list the valid values so the user can self-fix
        # without grepping anyserial source.
        assert "1.5" in str(exc_info.value)
        assert "stopbits" in str(exc_info.value)

    def test_parity_str_lowercase(self) -> None:
        s = SerialSettings(port="/dev/null", parity="odd")  # type: ignore[arg-type]
        assert s.parity is Parity.ODD

    def test_parity_str_case_insensitive(self) -> None:
        s = SerialSettings(port="/dev/null", parity="ODD")  # type: ignore[arg-type]
        assert s.parity is Parity.ODD

    def test_parity_invalid_raises_typed_error(self) -> None:
        from sartoriuslib.errors import SartoriusConfigurationError

        with pytest.raises(SartoriusConfigurationError) as exc_info:
            SerialSettings(port="/dev/null", parity="weird")  # type: ignore[arg-type]
        assert "parity" in str(exc_info.value)
        assert "odd" in str(exc_info.value)

    def test_parity_wrong_type_raises_typed_error(self) -> None:
        """``parity=1`` (an int) is not a thing — must surface as config error."""
        from sartoriuslib.errors import SartoriusConfigurationError

        with pytest.raises(SartoriusConfigurationError):
            SerialSettings(port="/dev/null", parity=1)  # type: ignore[arg-type]

    def test_bytesize_int_normalised(self) -> None:
        s = SerialSettings(port="/dev/null", bytesize=8)  # type: ignore[arg-type]
        assert s.bytesize is ByteSize.EIGHT

    def test_bytesize_str_normalised(self) -> None:
        s = SerialSettings(port="/dev/null", bytesize="7")  # type: ignore[arg-type]
        assert s.bytesize is ByteSize.SEVEN

    def test_bytesize_invalid_raises_typed_error(self) -> None:
        from sartoriuslib.errors import SartoriusConfigurationError

        with pytest.raises(SartoriusConfigurationError):
            SerialSettings(port="/dev/null", bytesize=9)  # type: ignore[arg-type]
