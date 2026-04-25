r"""Transport layer abstraction — moves bytes, knows nothing about Sartorius.

The :class:`Transport` :pep:`544` Protocol is the structural interface every
backend implements. :class:`SerialSettings` is the port-configuration
dataclass consumed by
:class:`sartoriuslib.transport.serial.SerialTransport`.

Sartorius balances speak two wire protocols: xBPI (binary, length-prefixed)
and SBI (ASCII, line-oriented). The transport surface exposes both shapes:

- :meth:`Transport.read_exact` — fixed-count read for xBPI length-prefix
  framing (read 1 byte for ``len``, then read ``len`` more bytes).
- :meth:`Transport.read_until` — delimiter read for SBI's ``\r\n``-terminated
  lines.
- :meth:`Transport.read_available` — idle-bounded read for passive SBI
  autoprint sniffing during :func:`sartoriuslib.open_device` auto-detection.

Default serial framing is 8-O-1. Per ``docs/protocol.md`` §2.1 the balance's
PC-USB receive path is parity-forgiving, but 8-O-1 is universal on TX so the
default is the safe one.

Design reference: ``docs/design.md`` §8.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anyserial import ByteSize, Parity, StopBits

from sartoriuslib.errors import ErrorContext, SartoriusConfigurationError

__all__ = [
    "ByteSize",
    "Parity",
    "SerialSettings",
    "StopBits",
    "Transport",
]


def _coerce_bytesize(value: object) -> ByteSize:
    """Coerce ``value`` to :class:`anyserial.ByteSize`.

    Accepts the enum directly, an ``int`` (5/6/7/8), or the string form
    (``"5"``/``"6"``/``"7"``/``"8"``). Anything else raises a typed
    configuration error with a helpful message — far better UX than
    letting the wrong type leak into anyserial's termios layer where it
    fails as a confusing ``NoneType.iflag`` deep stack.

    The parameter is typed ``object`` because the function is the
    runtime guard — :class:`SerialSettings` exposes the enum-only type
    statically, but ad-hoc callers (CLI, environment-derived strings)
    legitimately pass ``int``/``str`` here.
    """
    if isinstance(value, ByteSize):
        return value
    try:
        return ByteSize(str(value))
    except ValueError as exc:
        raise SartoriusConfigurationError(
            f"invalid bytesize {value!r}; expected ByteSize, int, or one of "
            f"{[m.value for m in ByteSize]!r}",
            context=ErrorContext(extra={"field": "bytesize", "value": repr(value)}),
        ) from exc


def _coerce_parity(value: object) -> Parity:
    """Coerce ``value`` to :class:`anyserial.Parity`.

    Accepts the enum directly or the string form (``"none"`` / ``"odd"``
    / ``"even"`` / ``"mark"`` / ``"space"``, case-insensitive). See
    :func:`_coerce_bytesize` for why ``value`` is typed ``object``.
    """
    if isinstance(value, Parity):
        return value
    if isinstance(value, str):
        try:
            return Parity(value.lower())
        except ValueError as exc:
            raise SartoriusConfigurationError(
                f"invalid parity {value!r}; expected Parity or one of "
                f"{[m.value for m in Parity]!r}",
                context=ErrorContext(extra={"field": "parity", "value": repr(value)}),
            ) from exc
    raise SartoriusConfigurationError(
        f"invalid parity {value!r}; expected Parity or str",
        context=ErrorContext(extra={"field": "parity", "value": repr(value)}),
    )


def _coerce_stopbits(value: object) -> StopBits:
    """Coerce ``value`` to :class:`anyserial.StopBits`.

    Accepts the enum directly, ``int`` (1 / 2), ``float`` (1.5 / 1.0 /
    2.0), or the string form (``"1"`` / ``"1.5"`` / ``"2"``).

    Why this exists: passing ``stopbits=1`` (a raw ``int``) to
    :class:`SerialSettings` used to land an unconverted ``int`` in
    anyserial's termios setter, where the ``match StopBits.ONE`` arms
    fall through and the function returns ``None`` — the next call
    crashes with ``AttributeError: 'NoneType' object has no attribute
    'iflag'`` deep in anyserial. We caught this on hardware day; the
    runbook's old script set ``stopbits=1`` and bombed out unhelpfully.

    See :func:`_coerce_bytesize` for why ``value`` is typed ``object``.
    """
    if isinstance(value, StopBits):
        return value
    # ``bool`` is a subclass of ``int``; reject it explicitly so
    # ``stopbits=True`` doesn't silently pass through as 1.
    if isinstance(value, bool):
        raise SartoriusConfigurationError(
            f"invalid stopbits {value!r}; bool is not a valid stopbits value",
            context=ErrorContext(extra={"field": "stopbits", "value": repr(value)}),
        )
    # Normalize 1/2 (int) and 1.0/2.0/1.5 (float) to the enum's string keys.
    if isinstance(value, int):
        key = str(value)
    elif isinstance(value, float):
        # 1.0 / 2.0 round to "1" / "2"; 1.5 stays "1.5".
        key = str(int(value)) if value.is_integer() else str(value)
    elif isinstance(value, str):
        key = value
    else:
        raise SartoriusConfigurationError(
            f"invalid stopbits {value!r}; expected StopBits, int, float, or str",
            context=ErrorContext(extra={"field": "stopbits", "value": repr(value)}),
        )
    try:
        return StopBits(key)
    except ValueError as exc:
        raise SartoriusConfigurationError(
            f"invalid stopbits {value!r}; expected StopBits or one of "
            f"{[m.value for m in StopBits]!r}",
            context=ErrorContext(extra={"field": "stopbits", "value": repr(value)}),
        ) from exc


class Transport(Protocol):
    """Byte-level transport.

    Every I/O boundary takes an explicit timeout. On expiry, implementations
    raise :class:`sartoriuslib.errors.SartoriusTimeoutError` — never return an
    empty or partial ``bytes`` silently. Backend exceptions normalize to
    :class:`sartoriuslib.errors.SartoriusTransportError` (or a subclass) with
    ``__cause__`` preserving the original exception.

    Lifecycle is single-shot: :meth:`open` once, :meth:`close` once.
    :meth:`reopen` closes + reopens with any subset of serial-setting
    overrides, used for the WZA SBI→xBPI protocol flip that also swaps baud
    and parity.
    """

    async def open(self) -> None:
        """Open the underlying port. Idempotent re-calls are an error."""
        ...

    async def close(self) -> None:
        """Close the underlying port. Safe to call when already closed."""
        ...

    async def reopen(
        self,
        *,
        baudrate: int | None = None,
        parity: Parity | None = None,
        stopbits: StopBits | None = None,
    ) -> None:
        """Close and re-open the port, optionally changing serial framing.

        Used by :meth:`Balance.configure_protocol` for the WZA SBI→xBPI
        flip, which swaps both baud rate and parity, and by operations
        like ``set_baud_rate`` that retune the port after the device has
        already switched mid-sequence. Any argument left as ``None`` keeps
        the existing value.

        Implementations must leave the transport in a consistent state:
        either fully reopened with the new settings, or clearly closed so
        callers can recognise a failure. Silent partial states are the
        worst failure mode for this method.

        Non-serial transports (e.g. a future TCP adapter) may raise
        :class:`NotImplementedError` — baud/parity don't apply there.
        """
        ...

    async def write(self, data: bytes, *, timeout: float) -> None:
        """Write every byte of ``data``.

        Raises :class:`sartoriuslib.errors.SartoriusTimeoutError` on
        expiry. A bounded write timeout is mandatory because sends can
        block on RS-485 hardware flow control or a stuck device. Callers
        that block indefinitely hide real hangs.
        """
        ...

    async def read_exact(self, n: int, *, timeout: float) -> bytes:
        """Read exactly ``n`` bytes.

        The canonical shape for xBPI framing: read one byte to discover
        the length, then read that many bytes. Raises
        :class:`sartoriuslib.errors.SartoriusTimeoutError` if fewer than
        ``n`` bytes arrive before ``timeout``. Partial buffers are retained
        for the next call — implementations must not discard them.
        """
        ...

    async def read_until(self, separator: bytes, *, timeout: float) -> bytes:
        """Read bytes up to and including the next occurrence of ``separator``.

        Raises :class:`sartoriuslib.errors.SartoriusTimeoutError` if the
        separator does not arrive before ``timeout``. Bytes received
        after the separator remain buffered for the next call —
        implementations must not discard them.
        """
        ...

    async def read_available(
        self,
        *,
        idle_timeout: float,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read until the line goes idle for ``idle_timeout`` seconds.

        Never raises on idle expiry — an idle timeout is the *expected*
        exit. Returns whatever was accumulated (possibly empty). Used
        for best-effort drain, passive SBI autoprint sniffing during
        protocol detection, and stream-stop recovery.
        """
        ...

    async def drain_input(self) -> None:
        """Discard any buffered input bytes. Best-effort; never raises."""
        ...

    @property
    def is_open(self) -> bool:
        """Whether :meth:`open` has run without a matching :meth:`close`."""
        ...

    @property
    def label(self) -> str:
        """Short identifier (port path, URL, ``"fake://..."``) used in errors."""
        ...


@dataclass(frozen=True, slots=True)
class SerialSettings:
    """Serial-port configuration for :class:`SerialTransport`.

    Mirrors :class:`anyserial.SerialConfig` plus a ``port`` path. Default
    framing is 8-O-1 because that is universal across every Sartorius
    family we have captures for (MSE, WZA, BCE) per ``docs/protocol.md``
    §2.1. Baud defaults to 9600 because it matches the BCE default and
    sits in the middle of the supported range; the MSE uses 19200 and the
    WZA 1200, so callers that care will supply the right value or rely on
    :func:`sartoriuslib.open_device` auto-detection. Note: all three
    families ship from the factory in SBI mode (WZA in SBI autoprint at
    1200-7-O-1); xBPI is reached by a front-panel menu change.

    ``exclusive`` defaults ``True`` so two processes can't scribble over
    the same device — neither xBPI nor SBI is multi-master tolerant.

    The ``bytesize`` / ``parity`` / ``stopbits`` fields are typed as
    the enums and that is what callers should statically pass. Runtime
    accepts an equivalent ``int``/``str`` shorthand (e.g.
    ``stopbits=1``, ``parity="odd"``) — ``__post_init__`` normalises
    the value to the enum and raises
    :class:`SartoriusConfigurationError` on anything the coercer can't
    recognise. The static types stay strict so type-checkers point bad
    callers at the enum form, while the runtime stays forgiving for
    ad-hoc scripts and front-panel-derived values
    (``os.environ["SBI_PARITY"]`` → ``"odd"``). The widened
    constructor types are exposed only via the ``# type: ignore`` lines
    in :meth:`__post_init__` — :class:`SerialTransport` and
    :mod:`anyserial` below this layer can always rely on the field
    being an enum member.
    """

    port: str
    baudrate: int = 9600
    bytesize: ByteSize = ByteSize.EIGHT
    parity: Parity = Parity.ODD
    stopbits: StopBits = StopBits.ONE
    rtscts: bool = False
    xonxoff: bool = False
    exclusive: bool = True

    def __post_init__(self) -> None:
        # ``frozen=True`` blocks plain attribute assignment, so we reach
        # through ``object.__setattr__`` exactly here — same trick the
        # stdlib uses for frozen dataclass __post_init__ normalisation.
        # The coercers accept the widened input types (int/float/str)
        # at runtime; mypy sees the field as the enum already so the
        # call type-checks without ignores.
        object.__setattr__(self, "bytesize", _coerce_bytesize(self.bytesize))
        object.__setattr__(self, "parity", _coerce_parity(self.parity))
        object.__setattr__(self, "stopbits", _coerce_stopbits(self.stopbits))
