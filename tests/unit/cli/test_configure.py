"""``sarto-configure`` — confirmed configuration ops via fixture file.

Covers:

- ``switch-protocol``: xBPI → SBI host-side flip; verify via SBI identity.
- ``set-baud-rate``: xBPI ``0x5C`` ACK + transport reopen + identity verify.
- ``write-sbn-address``: xBPI ``0x72`` ACK + readback via ``0x71``.
- ``--confirm`` gate refuses every op.
- ``--target sbi`` / wire-code / sbn arg parsing happy paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sartoriuslib.cli.configure import main
from sartoriuslib.protocol.sbi import (
    LINE_TERMINATOR,
    TOKEN_SERIAL,
    TOKEN_SOFTWARE,
    TOKEN_TYPE,
)
from sartoriuslib.protocol.xbpi import build_command, encode_tlv
from sartoriuslib.testing import build_identify_script, canned_frames

if TYPE_CHECKING:
    from pathlib import Path


def _sbi_line(text: str) -> bytes:
    return text.encode("ascii") + LINE_TERMINATOR


def _fixture_text(script: dict[bytes, bytes]) -> str:
    out: list[str] = []
    for tx, rx in script.items():
        out.append(f"> {tx.hex(' ')}")
        out.append(f"< {rx.hex(' ')}")
    return "\n".join(out) + "\n"


def _switch_xbpi_to_sbi_script() -> dict[bytes, bytes]:
    s: dict[bytes, bytes] = build_identify_script()  # initial xBPI identity
    s.update(
        {
            TOKEN_TYPE: _sbi_line("WZA8202-N"),
            TOKEN_SERIAL: _sbi_line("12345678"),
            TOKEN_SOFTWARE: _sbi_line("1.0"),
        },
    )
    return s


def _set_baud_script(wire_code: int = 0x01) -> dict[bytes, bytes]:
    s: dict[bytes, bytes] = build_identify_script()
    s[build_command(0x5C, encode_tlv(0x21, wire_code))] = canned_frames.RX_ACK
    return s


def _write_sbn_script(sbn: int = 0x05) -> dict[bytes, bytes]:
    s: dict[bytes, bytes] = build_identify_script(sbn=sbn)
    s[build_command(0x72, encode_tlv(0x21, sbn))] = canned_frames.RX_ACK
    return s


class TestSwitchProtocol:
    def test_xbpi_to_sbi_succeeds_with_confirm(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "switch.fixture"
        fixture.write_text(_fixture_text(_switch_xbpi_to_sbi_script()))
        rc = main(
            [
                "switch-protocol",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--target",
                "sbi",
                "--new-baudrate",
                "1200",
                "--timeout",
                "0.1",
                "--confirm",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "switch-protocol complete:" in out
        assert "protocol:     sbi" in out
        assert "baudrate:     1200" in out
        assert "model:        WZA8202-N" in out

    def test_refuses_without_confirm(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "switch.fixture"
        fixture.write_text(_fixture_text(_switch_xbpi_to_sbi_script()))
        rc = main(
            [
                "switch-protocol",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--target",
                "sbi",
                "--new-baudrate",
                "1200",
                "--timeout",
                "0.1",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 2
        assert "destructive" in captured.err
        assert "--confirm" in captured.err


class TestSetBaudRate:
    def test_succeeds_with_confirm(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "baud.fixture"
        fixture.write_text(_fixture_text(_set_baud_script(wire_code=0x01)))
        rc = main(
            [
                "set-baud-rate",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--wire-code",
                "0x01",
                "--target-baudrate",
                "19200",
                "--timeout",
                "0.1",
                "--confirm",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "set-baud-rate complete:" in out
        assert "baudrate:     19200" in out

    def test_accepts_decimal_wire_code(
        self,
        tmp_path: Path,
    ) -> None:
        """``--wire-code`` accepts both ``0x01`` and ``1`` via ``int(s, 0)``."""
        fixture = tmp_path / "baud.fixture"
        fixture.write_text(_fixture_text(_set_baud_script(wire_code=0x01)))
        rc = main(
            [
                "set-baud-rate",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--wire-code",
                "1",
                "--target-baudrate",
                "19200",
                "--timeout",
                "0.1",
                "--confirm",
            ],
        )
        assert rc == 0


class TestWriteSbnAddress:
    def test_succeeds_with_confirm_and_verifies(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "sbn.fixture"
        fixture.write_text(_fixture_text(_write_sbn_script(sbn=0x05)))
        rc = main(
            [
                "write-sbn-address",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--sbn",
                "0x05",
                "--timeout",
                "0.1",
                "--confirm",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "requested:  0x05" in out
        assert "readback:   0x05" in out
        assert "verified:   True" in out

    def test_refuses_without_confirm(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "sbn.fixture"
        fixture.write_text(_fixture_text(_write_sbn_script()))
        rc = main(
            [
                "write-sbn-address",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--sbn",
                "0x05",
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 2
        assert "destructive" in capsys.readouterr().err


class TestSubcommandGate:
    def test_no_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_unknown_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["nonsense-op", "placeholder"])
