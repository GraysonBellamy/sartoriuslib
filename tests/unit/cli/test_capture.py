"""``sarto-capture`` — fixed-cadence acquisition into csv/jsonl/sqlite sinks."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import TYPE_CHECKING

from sartoriuslib.cli.capture import main
from sartoriuslib.testing import canned_frames

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _poll_fixture(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text(
        f"> {canned_frames.TX_READ_NET.hex(' ')}\n"
        f"< {canned_frames.RX_NET_WEIGHT_EMPTY_PAN.hex(' ')}\n",
    )
    return p


class TestSartoCaptureJsonl:
    def test_writes_jsonl_rows_at_target_rate(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _poll_fixture(tmp_path, "poll.fixture")
        out = tmp_path / "run.jsonl"
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--rate",
                "10",
                "--duration",
                "0.3",
                "--out",
                str(out),
                "--timeout",
                "0.05",
            ],
        )
        stdout = capsys.readouterr().out
        assert rc == 0
        assert "samples_emitted:" in stdout
        rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
        assert len(rows) >= 2
        # Each row is the standard schema; the §3.3 example value comes
        # back on every poll because the fixture replays one frame.
        assert rows[0]["unit"] == "g"
        assert rows[0]["protocol"] == "xbpi"
        assert rows[0]["error_type"] is None
        assert math.isclose(float(rows[0]["value"]), -0.005, rel_tol=1e-6, abs_tol=1e-12)


class TestSartoCaptureCsv:
    def test_writes_csv_with_schema_header(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _poll_fixture(tmp_path, "poll.fixture")
        out = tmp_path / "run.csv"
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--rate",
                "5",
                "--duration",
                "0.3",
                "--out",
                str(out),
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 0
        text = out.read_text()
        # Header + at least one data row.
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(lines) >= 2
        assert "value" in lines[0]
        assert "unit" in lines[0]
        assert "g" in lines[1]


class TestSartoCaptureSqlite:
    def test_writes_sqlite_table(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _poll_fixture(tmp_path, "poll.fixture")
        out = tmp_path / "run.sqlite"
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--rate",
                "5",
                "--duration",
                "0.3",
                "--out",
                str(out),
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 0
        conn = sqlite3.connect(out)
        try:
            rows = conn.execute("SELECT value, unit, protocol FROM samples").fetchall()
        finally:
            conn.close()
        assert len(rows) >= 1
        # value is recorded as TEXT-formatted float in the default schema.
        assert rows[0][1] == "g"
        assert rows[0][2] == "xbpi"


class TestSartoCaptureFormatGate:
    def test_unknown_extension_requires_explicit_format(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _poll_fixture(tmp_path, "poll.fixture")
        out = tmp_path / "run.weird"
        rc = main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--rate",
                "5",
                "--duration",
                "0.05",
                "--out",
                str(out),
                "--timeout",
                "0.05",
            ],
        )
        # SartoriusValidationError → exit 1.
        assert rc == 1
