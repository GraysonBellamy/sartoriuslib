"""Regression test: every reply in the real-MSE snapshot decodes cleanly.

The unit codec suite proves the decoder works on synthetic bytes — these
tests pin it to bytes that came off a real MSE1203S during hardware day.
A decoder change that breaks real-world replies fails this test even if
every synthetic test still passes.

The snapshot file is `tests/fixtures/captures/mse_xbpi/snapshot.json`,
produced by `sarto-diag snapshot` against the unit. See the sibling
README.md for capture conditions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sartoriuslib.protocol.xbpi.framing import parse_frame

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "captures" / "mse_xbpi" / "snapshot.json"
)


def _load_snapshot() -> list[dict[str, str]]:
    if not _FIXTURE.exists():
        pytest.skip(f"fixture not present: {_FIXTURE}")
    with _FIXTURE.open() as f:
        return list(json.load(f))


SNAPSHOT_ROWS: list[dict[str, str]] = _load_snapshot()


# ---------------------------------------------------------------------------
# Identity expectations — these specific bytes are the exact unit on file.
# ---------------------------------------------------------------------------


def _row(opcode: int) -> dict[str, str]:
    for row in SNAPSHOT_ROWS:
        if int(row["opcode"], 16) == opcode:
            return row
    # ``pytest.skip`` raises, never returns — the unreachable trailer
    # is omitted so mypy's reachability analysis stays satisfied.
    pytest.skip(f"opcode 0x{opcode:02x} not in snapshot")


class TestIdentityFromRealMSE:
    """Hard-pin identity bytes from the captured unit so a downstream
    rename/refactor that breaks the decode path fails fast."""

    def test_model_is_mse1203s(self) -> None:
        row = _row(0x02)
        assert row["status"] == "ok"
        # ASCII-decodable, null-padded — 'MSE1203S-100-DR\x00\x00\x00\x00\x00'
        body = bytes.fromhex(row["body"])
        decoded = body.rstrip(b"\x00").decode("ascii")
        assert decoded == "MSE1203S-100-DR"

    def test_manufacturer_is_sartorius(self) -> None:
        row = _row(0x07)
        assert row["status"] == "ok"
        body = bytes.fromhex(row["body"])
        decoded = body.rstrip(b"\x00").decode("ascii")
        assert decoded == "Sartorius"

    def test_software_version_blob_unchanged(self) -> None:
        """The 10-byte software-version blob is not yet structurally
        decoded (need cross-firmware captures — see design §16 Q5).
        Pin the raw bytes so a future structural decoder is checked
        against the same input."""
        row = _row(0x00)
        assert row["status"] == "ok"
        assert row["body"] == "00392100390139010001"


# ---------------------------------------------------------------------------
# Frame-codec regression: every "ok" row's raw is a parseable xBPI frame.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [r for r in SNAPSHOT_ROWS if r["status"] == "ok"],
    ids=lambda r: f"0x{int(r['opcode'], 16):02x}_{r['name']}",
)
def test_every_ok_reply_round_trips_through_parse_frame(row: dict[str, str]) -> None:
    """For every snapshot row marked ``status=ok``, the raw bytes must
    decode through :func:`parse_frame` without raising and the parsed
    body must equal the recorded body. Catches checksum / length
    regressions with byte-accurate granularity."""
    raw = bytes.fromhex(row["raw"])
    frame = parse_frame(raw)
    assert frame.body.hex() == row["body"], (
        f"opcode 0x{int(row['opcode'], 16):02x} ({row['name']}): body mismatch"
    )
    # The subtype byte is the third byte (after length + 0x41 marker).
    assert frame.subtype == int(row["subtype"], 16)


# ---------------------------------------------------------------------------
# Error-row regression: error rows carry the right typed exception class.
# ---------------------------------------------------------------------------


def test_error_rows_carry_named_exceptions() -> None:
    """Snapshot's `error_type` column must always be a known
    :class:`SartoriusError` subclass name. A future error-class rename
    that forgets to update this snapshot fails here."""
    expected_classes = {
        "SartoriusValueOutOfRangeError",
        "SartoriusUnsupportedCommandError",
        "SartoriusOperationNotApplicableError",
        "SartoriusMissingArgsError",
        "SartoriusIndexOutOfRangeError",
        "SartoriusCommandRejectedError",
    }
    for row in SNAPSHOT_ROWS:
        if row["status"] != "error":
            continue
        assert row["error_type"] in expected_classes, (
            f"opcode {row['opcode']} ({row['name']}): unknown error class {row['error_type']!r}"
        )
