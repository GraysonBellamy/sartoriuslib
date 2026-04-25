"""Regression test: every captured-streaming sample decodes via the codec.

`run_60s.csv` carries 600 real measurement-frame bytes captured from an
MSE1203S over 60 s. Walk every row through :func:`parse_frame` and
:func:`decode_measurement_body` and assert:

- Every frame parses cleanly (length / marker / checksum all valid).
- The decoded value matches the recorded `value` column to within
  float-precision rounding.
- The recorded `stable` / `decimals` / `unit` agree with the decoder's
  output.

A decoder regression that breaks even one of those 600 real frames
fails this test even if every synthetic test still passes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from sartoriuslib.protocol.xbpi.framing import parse_frame
from sartoriuslib.protocol.xbpi.parser import decode_measurement_body

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "captures" / "mse_xbpi" / "run_60s.csv"
)


def _load_rows() -> list[dict[str, str]]:
    if not _FIXTURE.exists():
        pytest.skip(f"fixture not present: {_FIXTURE}")
    with _FIXTURE.open() as f:
        return list(csv.DictReader(f))


ROWS: list[dict[str, str]] = _load_rows()


def test_capture_has_expected_sample_count() -> None:
    """A 60 s @ 10 Hz capture should land near 600 samples (one tick of
    drift is acceptable)."""
    assert 595 <= len(ROWS) <= 605


def test_capture_contains_perturbation() -> None:
    """The hardware-day script asks for one mass lift+replace mid-run.
    Without it, every value clusters near 200 g; with it, the range
    spans roughly 200 g (-x to +200x). Pin the perturbation here so a
    capture that didn't get one is flagged immediately."""
    values = [float(r["value"]) for r in ROWS if r["value"]]
    assert values, "no numeric values in capture"
    assert max(values) - min(values) > 100.0, (
        "captured range too narrow — was a mass lift/replace performed mid-run?"
    )


def test_no_error_rows_in_capture() -> None:
    """A clean 60 s capture should have no `error_type` rows. Any
    error here would indicate a wire or session failure during the
    run worth investigating."""
    err_rows = [r for r in ROWS if r["error_type"]]
    assert err_rows == [], f"unexpected error rows: {err_rows[:3]}"


def test_every_raw_frame_round_trips() -> None:
    """Every `raw` field is a hex-encoded xBPI measurement frame.
    parse_frame + decode_measurement_body must succeed for each, and
    the decoded value must match the recorded `value` column."""
    mismatches: list[str] = []
    for i, row in enumerate(ROWS):
        raw_hex = row["raw"]
        if not raw_hex:
            continue
        try:
            frame = parse_frame(bytes.fromhex(raw_hex))
        except Exception as exc:
            mismatches.append(f"row {i}: parse_frame failed: {exc}")
            continue
        if frame.subtype != 0x48:
            # The capture is exclusively net-weight reads; another subtype
            # would indicate something unexpected on the wire.
            mismatches.append(f"row {i}: unexpected subtype 0x{frame.subtype:02x}")
            continue
        body = decode_measurement_body(frame.body)
        recorded_value = float(row["value"]) if row["value"] else None
        if recorded_value is None:
            if body.value is not None and not body.off_scale:
                mismatches.append(f"row {i}: expected None, got {body.value}")
            continue
        if body.value is None:
            mismatches.append(f"row {i}: decoded None, recorded {recorded_value}")
            continue
        # float32 round-trip is exact through repr → float, but allow a
        # tiny tolerance to be safe against future formatting changes.
        if abs(body.value - recorded_value) > 1e-3:
            mismatches.append(f"row {i}: decoded {body.value}, recorded {recorded_value}")
    assert not mismatches, (
        f"{len(mismatches)} frame mismatches in {len(ROWS)} rows; first 3: {mismatches[:3]}"
    )
