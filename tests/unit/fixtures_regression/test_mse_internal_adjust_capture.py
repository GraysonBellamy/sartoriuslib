"""Regression test: the internal-adjust bench captures still say what the docs say.

Pins ``docs/protocol.md`` §7.7.1 / §7.12 / §8.1 / §8.2 to the raw bytes
captured on the MSE1203S (2026-11-27). If a decoder change or a doc edit
drifts from what the hardware actually did, this fails.

Fixtures: ``tests/fixtures/captures/mse_xbpi/internal_adjust/`` (see its
README for capture conditions).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sartoriuslib.devices.models import CalRecord

_DIR = (
    Path(__file__).resolve().parents[2] / "fixtures" / "captures" / "mse_xbpi" / "internal_adjust"
)

_ADJUST_BITS = 0x41
_GAP_STATE, _GAP_STATUS = 0x88, 0x28


def _load(name: str) -> list[dict[str, Any]]:
    path = _DIR / name
    if not path.exists():
        pytest.skip(f"fixture not present: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _record(body_hex: str) -> CalRecord:
    body = bytes.fromhex(body_hex.replace(" ", ""))
    return CalRecord(
        temperature_celsius=None,
        signature=body[4:13],
        counters=body[13:16],
        padding=body[16],
        raw=body,
    )


# ---------------------------------------------------------------------------
# Run G — ~200 Hz 0x32 trace through one complete adjust
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_g() -> dict[str, Any]:
    rows = _load("run_g_fast.jsonl.gz")
    t_adj = next(r["t"] for r in rows if r["op"] == "0x28")
    status = [
        (r["t"] - t_adj, bytes.fromhex(r["body"])) for r in rows if r["op"] == "0x32" and r["body"]
    ]
    records = [(r["t"] - t_adj, r["body"]) for r in rows if r["op"] == "0xb9" and r["body"]]
    first_adjusting = next(t for t, b in status if t >= 0 and b[0] & _ADJUST_BITS)
    last_adjusting = max(t for t, b in status if b[0] & _ADJUST_BITS)
    done = min(t for t, _ in status if t > last_adjusting)
    return {
        "status": status,
        "records": records,
        "first_adjusting": first_adjusting,
        "last_adjusting": last_adjusting,
        "done": done,
        "net": [(r["t"] - t_adj, bytes.fromhex(r["body"])) for r in rows if r["op"] == "0x1e"],
    }


class TestRunGTimeline:
    def test_duration(self, run_g: dict[str, Any]) -> None:
        assert run_g["first_adjusting"] < 0.05
        assert 24.0 < run_g["done"] < 24.6

    def test_adjust_bits_never_set_at_rest(self, run_g: dict[str, Any]) -> None:
        rest = [b for t, b in run_g["status"] if t < 0 or t >= run_g["done"]]
        assert rest
        assert all(not b[0] & _ADJUST_BITS for b in rest)
        assert all(b[0] in (0x80, 0x88) for b in rest)
        assert all(not b[1] & 0x20 for b in rest)

    def test_exactly_one_gap_of_about_200ms(self, run_g: dict[str, Any]) -> None:
        """The reason ``wait_for_adjustment`` debounces idle (§7.7.1)."""
        inside = [
            (t, b)
            for t, b in run_g["status"]
            if run_g["first_adjusting"] <= t <= run_g["last_adjusting"]
        ]
        idle = [(t, b) for t, b in inside if not b[0] & _ADJUST_BITS]
        assert idle, "expected the mid-adjust idle window"
        assert {(b[0], b[1]) for _, b in idle} == {(_GAP_STATE, _GAP_STATUS)}
        start, end = idle[0][0], idle[-1][0]
        assert 5.8 < start < 6.1
        assert 0.15 < end - start < 0.25
        # contiguous: every sample between start and end is a gap sample
        assert all(not b[0] & _ADJUST_BITS for t, b in inside if start <= t <= end)

    def test_record_rewritten_at_the_end_just_before_idle(self, run_g: dict[str, Any]) -> None:
        bodies = run_g["records"]
        changes = [t for (t, b), (_, prev) in zip(bodies[1:], bodies, strict=False) if b != prev]
        assert len(changes) == 1
        assert run_g["done"] - 0.5 < changes[0] <= run_g["done"]
        before = _record(bodies[0][1]).timestamp
        after = _record(bodies[-1][1]).timestamp
        assert before is not None
        assert after is not None
        assert after > before

    def test_net_flag_04_clear_during_adjust(self, run_g: dict[str, Any]) -> None:
        during = [b[7] for t, b in run_g["net"] if 0.05 < t < run_g["done"]]
        rest = [b[7] for t, b in run_g["net"] if t < 0 or t > run_g["done"] + 0.05]
        assert during
        assert all(not f & 0x04 for f in during)
        assert all(f & 0x04 for f in rest)


# ---------------------------------------------------------------------------
# Runs A / B / C — ~4 Hz multi-opcode polls
# ---------------------------------------------------------------------------


def _changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev: str | None = None
    for r in rows:
        if r.get("op") == "0xb9" and r.get("kind") == "ok":
            if prev is not None and r["body"] != prev:
                out.append(r)
            prev = r["body"]
    return out


def test_cal_timestamp_tracks_host_clock_across_runs() -> None:
    """Run A and run C records are as far apart as the host saw them complete."""
    a = _changes(_load("run_a_adjust.jsonl.gz"))
    c = _changes(_load("run_c_reenter.jsonl.gz"))
    assert len(a) == 1
    assert len(c) == 1
    stamp_a = _record(a[0]["body"]).timestamp
    stamp_c = _record(c[0]["body"]).timestamp
    assert stamp_a == datetime(2026, 11, 27, 15, 56, 49)
    assert stamp_c == datetime(2026, 11, 27, 16, 0, 20)
    assert stamp_a is not None
    assert stamp_c is not None
    host = datetime.fromisoformat(c[0]["wall"]) - datetime.fromisoformat(a[0]["wall"])
    assert abs((stamp_c - stamp_a).total_seconds() - host.total_seconds()) < 1.5


def test_abort_leaves_record_and_clears_within_seconds() -> None:
    rows = _load("run_b_abort.jsonl.gz")
    assert _changes(rows) == []
    t_abort = next(r["t"] for r in rows if r.get("tag") == "ABORT")
    abort = next(r for r in rows if r.get("tag") == "ABORT")
    assert abort["raw"] == "03 41 00 44"  # ACK mid-adjust
    states = [
        (r["t"], r["dec"]["state"])
        for r in rows
        if r.get("op") == "0x30" and r.get("kind") == "ok" and r["t"] > t_abort
    ]
    idle_at = next(t for t, s in states if not s & _ADJUST_BITS)
    assert 1.5 < idle_at - t_abort < 4.0


def test_reentry_refused_with_0x06() -> None:
    rows = _load("run_c_reenter.jsonl.gz")
    again = next(r for r in rows if r.get("tag") == "REENTER")
    assert again["kind"] == "err"
    assert again["err"] == "0x06"
    assert len(_changes(rows)) == 1  # the original adjust still completed


def test_no_timeouts_or_frame_errors_mid_adjust() -> None:
    for name in ("run_a_adjust.jsonl.gz", "run_b_abort.jsonl.gz", "run_c_reenter.jsonl.gz"):
        kinds = {r["kind"] for r in _load(name) if "kind" in r}
        assert kinds <= {"ok", "err"}, name
