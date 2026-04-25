"""``sarto-read`` — open + identify + one poll, end-to-end via fixture file.

Each test writes a §8.2 text fixture into a tmp_path and drives
``sarto-read``'s ``main(argv)`` directly. The fixture file replaces
the serial port via the shared ``--fixture`` flag from
:mod:`sartoriuslib.cli._common`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sartoriuslib.cli.read import main
from sartoriuslib.testing import (
    build_identify_script,
    build_metrology_script,
    canned_frames,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _fixture_text(script: dict[bytes, bytes]) -> str:
    """Render a TX→RX dict as a §8.2 fixture file."""
    out: list[str] = []
    for tx, rx in script.items():
        out.append(f"> {tx.hex(' ')}")
        out.append(f"< {rx.hex(' ')}")
    return "\n".join(out) + "\n"


def _mse_full_script() -> dict[bytes, bytes]:
    script = build_identify_script()
    script.update(build_metrology_script())
    script[canned_frames.TX_READ_NET] = canned_frames.RX_NET_WEIGHT_EMPTY_PAN
    return script


class TestSartoRead:
    def test_xbpi_identify_and_poll(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "mse.fixture"
        fixture.write_text(_fixture_text(_mse_full_script()))

        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--timeout",
                "0.1",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        # Identify block surfaced.
        assert "model:        MSE1203S-100-DR" in out
        assert "manufacturer: Sartorius" in out
        assert "family:       cubis" in out
        # Poll surfaced with the §3.3 worked example value.
        assert "value:     -0.005 g" in out
        assert "stable:    True" in out
        assert "protocol:  xbpi" in out

    def test_no_identify_flag_skips_identity_block(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Only the poll TX/RX is needed when identify is skipped.
        fixture = tmp_path / "poll_only.fixture"
        fixture.write_text(
            _fixture_text(
                {canned_frames.TX_READ_NET: canned_frames.RX_NET_WEIGHT_EMPTY_PAN},
            ),
        )

        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--no-identify",
                "--timeout",
                "0.1",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "device:" not in out
        assert "value:     -0.005 g" in out

    def test_silent_device_returns_nonzero(
        self,
        tmp_path: Path,
    ) -> None:
        # Empty fixture → no scripted replies → AUTO detect fails cleanly.
        fixture = tmp_path / "silent.fixture"
        fixture.write_text("# empty\n")
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "auto",
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 1
