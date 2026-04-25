"""``sarto-raw`` — single-command bypass of the typed Command layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sartoriuslib.cli.raw import main
from sartoriuslib.testing import canned_frames

if TYPE_CHECKING:
    from pathlib import Path


def _xbpi_fixture(text: str, tmp_path: Path) -> Path:
    p = tmp_path / "raw.fixture"
    p.write_text(text)
    return p


class TestSartoRawXbpi:
    def test_read_net_dumps_decoded_reply(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture(
            f"> {canned_frames.TX_READ_NET.hex(' ')}\n"
            f"< {canned_frames.RX_NET_WEIGHT_EMPTY_PAN.hex(' ')}\n",
            tmp_path,
        )
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--xbpi",
                "0x1E",
                "--timeout",
                "0.1",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "subtype:    0x48" in out
        assert "value:    -0.005" in out

    def test_unsafe_opcode_without_confirm_refuses(
        self,
        tmp_path: Path,
    ) -> None:
        # 0x47 (save_menu) is PERSISTENT — without --confirm the session
        # raises pre-I/O.
        fixture = _xbpi_fixture("# nothing scripted\n", tmp_path)
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--xbpi",
                "0x47",
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 1


class TestSartoRawSbi:
    def test_print_token_decodes_weight_line(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "sbi_print.fixture"
        fixture.write_text("> ESC P\n< +     1.23 g\n")
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "sbi",
                "--sbi",
                "ESC P",
                "--timeout",
                "0.1",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "kind:   weight" in out
        assert "value:     1.23" in out


class TestSartoRawArgGate:
    def test_either_xbpi_or_sbi_required(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        with pytest.raises(SystemExit):
            main(
                [
                    "placeholder",
                    "--fixture",
                    str(fixture),
                    "--timeout",
                    "0.05",
                ],
            )

    def test_xbpi_with_sbi_session_refuses(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "sbi",
                "--xbpi",
                "0x1E",
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 1
