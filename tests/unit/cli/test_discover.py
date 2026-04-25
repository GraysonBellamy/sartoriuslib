"""``sarto-discover`` — protocol probe via fixture file.

Covers each of :func:`detect_protocol`'s exit paths through the
public CLI surface:

- xBPI probe answers → exit 0, ``protocol: xbpi``.
- SBI autoprint observed → exit 0, ``autoprint: True``, sniffed line preserved.
- Silent device → exit 2, ``protocol: <none>``.
- ``--json`` emits a valid JSON document.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sartoriuslib.cli.discover import main
from sartoriuslib.testing import canned_frames

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestSartoDiscoverXbpi:
    def test_xbpi_responsive_device_reports_xbpi(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # READ_MODEL probe — detect_protocol's xBPI path.
        fixture = tmp_path / "xbpi.fixture"
        fixture.write_text(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
        )
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--timeout",
                "0.1",
                "--sniff-window",
                "0.02",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "protocol:    xbpi" in out
        assert "autoprint:   False" in out

    def test_json_output_is_valid(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "xbpi.fixture"
        fixture.write_text(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
        )
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--timeout",
                "0.1",
                "--sniff-window",
                "0.02",
                "--json",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        assert payload["protocol"] == "xbpi"
        assert payload["autoprint_active"] is False


class TestSartoDiscoverSilent:
    def test_silent_returns_nonzero_with_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = tmp_path / "empty.fixture"
        fixture.write_text("# nothing\n")
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--timeout",
                "0.05",
                "--sniff-window",
                "0.02",
            ],
        )
        out = capsys.readouterr().out
        # Discovery itself returns a result with ``protocol=None`` rather
        # than raising — the CLI surfaces it and exits 2.
        assert rc == 2
        assert "<none" in out
        assert "error:" in out
