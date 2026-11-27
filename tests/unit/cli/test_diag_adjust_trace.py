"""``sarto-diag adjust-trace`` against :class:`AdjustmentSimulator`."""

from __future__ import annotations

import io
import json
import re
from typing import TYPE_CHECKING, Any, TypedDict

import pytest

from sartoriuslib import open_device
from sartoriuslib.cli import diagnostics
from sartoriuslib.cli.diagnostics import adjust_trace
from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.testing import (
    AdjustmentSimulator,
    FakeTransport,
    build_identify_script,
    build_metrology_script,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sartoriuslib import Balance


class _Timing(TypedDict):
    pre_s: float
    period_s: float
    post_s: float
    max_s: float


_FAST_RUN: _Timing = {"pre_s": 0.02, "period_s": 0.003, "post_s": 0.02, "max_s": 1.0}


async def _open(sim: AdjustmentSimulator) -> Balance:
    transport = FakeTransport({**build_identify_script(), **build_metrology_script()})
    sim.install(transport)
    return await open_device(transport, protocol=ProtocolKind.XBPI, timeout=0.05)


def _lines(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


@pytest.mark.anyio
async def test_records_a_complete_adjustment() -> None:
    sim = AdjustmentSimulator(speed=200.0)
    bal = await _open(sim)
    buf = io.StringIO()
    summary = await adjust_trace.trace_adjustment(bal, buf, **_FAST_RUN)
    await bal.close()

    assert summary.adjust_reply == "ok"
    assert summary.t_adjust is not None
    assert summary.t_record_changed is not None
    assert summary.t_record_changed > summary.t_adjust
    states = [state for _, state, _ in summary.transitions]
    assert any(state & 0x41 for state in states)
    assert not states[-1] & 0x41  # ends idle
    lines = _lines(buf)
    assert [ln["tag"] for ln in lines if ln["tag"] != "poll" and ln["tag"] != "slow"] == ["ADJUST"]
    assert any(ln.get("dec", {}) and ln["dec"].get("adjusting") for ln in lines)
    assert any(ln.get("record_changed") for ln in lines)
    text = adjust_trace.format_summary(summary)
    assert "0xB9 changed" in text
    assert re.search(r"state=0xc[019]", text)  # a weight-phase (bit 0x40) run was listed


@pytest.mark.anyio
async def test_fast_mode_writes_compact_lines() -> None:
    sim = AdjustmentSimulator(speed=200.0)
    bal = await _open(sim)
    buf = io.StringIO()
    summary = await adjust_trace.trace_adjustment(bal, buf, fast=True, **_FAST_RUN)
    await bal.close()
    polls = [ln for ln in _lines(buf) if "tag" not in ln]
    assert polls
    assert set(polls[0]) == {"t", "op", "body"}
    assert {ln["op"] for ln in polls} == {"0x32", "0x1e", "0xb9"}
    assert summary.t_record_changed is not None


@pytest.mark.anyio
async def test_abort_and_reentry() -> None:
    sim = AdjustmentSimulator(speed=50.0)  # 0.05 s real = 2.5 s into the adjust
    bal = await _open(sim)
    buf = io.StringIO()
    summary = await adjust_trace.trace_adjustment(
        bal, buf, pre_s=0.02, period_s=0.003, max_s=0.4, abort_after=0.1, reenter_after=0.05
    )
    await bal.close()
    tagged = {ln["tag"]: ln for ln in _lines(buf) if ln["tag"] in ("ABORT", "REENTER")}
    assert tagged["REENTER"]["err"] == "0x06"  # refused while running
    assert tagged["ABORT"]["kind"] == "ok"
    assert summary.t_record_changed is None
    assert sim.aborted == 1


@pytest.mark.anyio
async def test_dry_run_never_sends_0x28() -> None:
    sim = AdjustmentSimulator(speed=200.0)
    bal = await _open(sim)
    buf = io.StringIO()
    summary = await adjust_trace.trace_adjustment(
        bal, buf, dry_run=True, pre_s=0.03, period_s=0.003
    )
    await bal.close()
    assert summary.t_adjust is None
    assert sim.completed == 0
    assert not sim.running
    assert "(not sent)" in adjust_trace.format_summary(summary)


def test_gate_refuses_without_ack(tmp_path: Path) -> None:
    out = tmp_path / "trace.jsonl"
    with pytest.raises(SystemExit) as ei:
        diagnostics.main(["adjust-trace", "COM_PLACEHOLDER", "--out", str(out)])
    assert ei.value.code == 2
    assert not out.exists()


def test_cli_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sim = AdjustmentSimulator(speed=200.0)

    async def fake_open(*_args: object, **_kwargs: object) -> Balance:
        return await _open(sim)

    monkeypatch.setattr(adjust_trace, "open_device", fake_open)
    out = tmp_path / "trace.jsonl"
    code = diagnostics.main(
        [
            "adjust-trace",
            "COM_PLACEHOLDER",
            "--out",
            str(out),
            "--pre",
            "0.02",
            "--period",
            "0.003",
            "--post",
            "0.02",
            "--max",
            "1",
            "--extra-ops",
            "77,7a",
            "--i-understand-this-is-destructive",
        ]
    )
    assert code == 0
    assert sim.completed == 1
    assert "0xB9 changed" in capsys.readouterr().out
    ops = {json.loads(line)["op"] for line in out.read_text(encoding="utf-8").splitlines()}
    assert {"0x28", "0x30", "0xb9", "0x77", "0x7a"} <= ops
