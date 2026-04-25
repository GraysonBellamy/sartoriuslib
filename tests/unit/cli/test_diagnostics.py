"""``sarto-diag`` — diagnostics namespace tests.

Covers all five subcommands plus the dispatcher:

- snapshot — read-only opcode battery via ``--include`` and fixture.
- tap — line capture, driven via ``capture_lines`` core function with
  a pre-fed :class:`FakeTransport`.
- stream — byte capture, driven via ``capture_bytes`` core function.
- sweep — destructive opcode walker; ``--i-understand-this-is-destructive``
  gate; default exclude list shields persistent-state opcodes.
- argfuzz — single-opcode argument fuzzer; same destructive gate.
- dispatcher — `sarto-diag SUBCOMMAND ...` routes correctly; unknown
  subcommands exit cleanly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from sartoriuslib.cli.diagnostics import argfuzz, snapshot, stream, sweep, tap
from sartoriuslib.cli.diagnostics import main as diag_main
from sartoriuslib.protocol.xbpi import build_command, encode_tlv
from sartoriuslib.testing import FakeTransport, canned_frames

if TYPE_CHECKING:
    from pathlib import Path


def _xbpi_fixture(text: str, tmp_path: Path, name: str = "diag.fixture") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_writes_per_opcode_status_line(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
            tmp_path,
        )
        rc = snapshot.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--include",
                "0x02",
                "--timeout",
                "0.05",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "snapshot: 1/1 opcodes responded" in out
        assert "0x02" in out
        assert "subtype=0x54" in out

    def test_unsupported_opcode_records_error_entry(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # No script → 0x02 will time out.
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = snapshot.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--include",
                "0x02",
                "--timeout",
                "0.02",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "snapshot: 0/1 opcodes responded" in out

    def test_json_output_to_file(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _xbpi_fixture(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
            tmp_path,
        )
        out_file = tmp_path / "results.json"
        rc = snapshot.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--include",
                "0x02",
                "--out",
                str(out_file),
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 0
        payload = json.loads(out_file.read_text())
        assert payload[0]["opcode"] == "0x02"
        assert payload[0]["status"] == "ok"
        assert payload[0]["name"] == "read_weigh_cell_model"


# ---------------------------------------------------------------------------
# tap (uses capture_lines core function with a manually-fed FakeTransport)
# ---------------------------------------------------------------------------


class TestTap:
    @pytest.mark.anyio
    async def test_capture_lines_returns_what_was_fed(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"+     0.00 g\r\n")
        transport.feed(b"+     0.50 g\r\n")
        try:
            lines = await tap.capture_lines(transport, duration=0.05, max_lines=2)
        finally:
            await transport.close()
        assert lines == ["+     0.00 g", "+     0.50 g"]

    @pytest.mark.anyio
    async def test_capture_lines_stops_on_idle_timeout(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(b"only one\r\n")
        try:
            lines = await tap.capture_lines(transport, duration=0.03)
        finally:
            await transport.close()
        # Captured the one line; loop exited when the next read_until
        # timed out (no more bytes to form a CRLF line).
        assert lines == ["only one"]


# ---------------------------------------------------------------------------
# stream (uses capture_bytes core function)
# ---------------------------------------------------------------------------


class TestStream:
    @pytest.mark.anyio
    async def test_capture_bytes_dumps_pre_fed_buffer(self) -> None:
        transport = FakeTransport()
        await transport.open()
        transport.feed(bytes.fromhex("0b 41 48 bb a3 d7 0a 3d 30 82 45 07"))
        try:
            captured = await stream.capture_bytes(
                transport,
                duration=0.05,
                idle_timeout=0.01,
            )
        finally:
            await transport.close()
        assert captured.hex() == "0b4148bba3d70a3d30824507"


# ---------------------------------------------------------------------------
# sweep (destructive)
# ---------------------------------------------------------------------------


class TestSweep:
    def test_destructive_gate_refuses_without_ack(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            sweep.main(
                [
                    "placeholder",
                    "--fixture",
                    str(fixture),
                    "--protocol",
                    "xbpi",
                    "--start",
                    "0x02",
                    "--end",
                    "0x02",
                    "--timeout",
                    "0.02",
                ],
            )
        assert excinfo.value.code == 2
        assert "destructive" in capsys.readouterr().err

    def test_with_ack_runs_the_sweep(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
            tmp_path,
        )
        rc = sweep.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--start",
                "0x02",
                "--end",
                "0x02",
                "--timeout",
                "0.05",
                "--i-understand-this-is-destructive",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "1/1 opcodes responded" in out

    def test_default_exclude_shield_skips_destructive_opcodes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Range 0x5C..0x5C — 0x5C is in DEFAULT_SWEEP_EXCLUDE; sweep should
        # report 0 opcodes (excluded) without sending anything.
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = sweep.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--start",
                "0x5C",
                "--end",
                "0x5C",
                "--timeout",
                "0.02",
                "--i-understand-this-is-destructive",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        # Zero opcodes after exclude shield → 0/0.
        assert "0/0 opcodes responded" in out

    def test_include_all_disables_shield(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # No script → 0x5C times out, but sweep still attempts it now
        # the shield is off.
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = sweep.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--start",
                "0x5C",
                "--end",
                "0x5C",
                "--timeout",
                "0.02",
                "--include-all",
                "--i-understand-this-is-destructive",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "0/1 opcodes responded" in out

    def test_invalid_range_returns_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = sweep.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--start",
                "0x10",
                "--end",
                "0x05",
                "--timeout",
                "0.02",
                "--i-understand-this-is-destructive",
            ],
        )
        assert rc == 1
        assert "--start" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# argfuzz (destructive)
# ---------------------------------------------------------------------------


class TestArgfuzz:
    def test_destructive_gate_refuses_without_ack(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            argfuzz.main(
                [
                    "placeholder",
                    "--fixture",
                    str(fixture),
                    "--protocol",
                    "xbpi",
                    "--opcode",
                    "0x55",
                    "--timeout",
                    "0.02",
                ],
            )
        assert excinfo.value.code == 2
        assert "destructive" in capsys.readouterr().err

    def test_tlv21_sweep_emits_per_arg_entries(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Script three valid replies for parameter-table reads at idx 0..2.
        rx_tail = canned_frames.RX_ACK  # any valid frame works for the smoke
        text_lines: list[str] = []
        for idx in range(3):
            tx = build_command(0x55, encode_tlv(0x21, idx))
            text_lines.append(f"> {tx.hex(' ')}")
            text_lines.append(f"< {rx_tail.hex(' ')}")
        fixture = _xbpi_fixture("\n".join(text_lines) + "\n", tmp_path)

        rc = argfuzz.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--opcode",
                "0x55",
                "--mode",
                "tlv21-sweep",
                "--start",
                "0x00",
                "--end",
                "0x02",
                "--timeout",
                "0.05",
                "--i-understand-this-is-destructive",
            ],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "argfuzz 0x55: 3/3 args responded" in out
        # Each iteration's args byte appears in the output.
        assert "args=2100" in out
        assert "args=2101" in out
        assert "args=2102" in out

    def test_raw_bytes_mode_requires_payload(
        self,
        tmp_path: Path,
    ) -> None:
        fixture = _xbpi_fixture("# empty\n", tmp_path)
        rc = argfuzz.main(
            [
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--opcode",
                "0x55",
                "--mode",
                "raw-bytes",
                "--timeout",
                "0.02",
                "--i-understand-this-is-destructive",
            ],
        )
        # SartoriusValidationError → exit 1.
        assert rc == 1


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_routes_snapshot_subcommand(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fixture = _xbpi_fixture(
            f"> {canned_frames.TX_READ_MODEL.hex(' ')}\n< {canned_frames.RX_MODEL_MSE.hex(' ')}\n",
            tmp_path,
        )
        rc = diag_main(
            [
                "snapshot",
                "placeholder",
                "--fixture",
                str(fixture),
                "--protocol",
                "xbpi",
                "--include",
                "0x02",
                "--timeout",
                "0.05",
            ],
        )
        assert rc == 0
        assert "snapshot: 1/1 opcodes responded" in capsys.readouterr().out

    def test_unknown_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            diag_main(["does-not-exist"])

    def test_no_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            diag_main([])
