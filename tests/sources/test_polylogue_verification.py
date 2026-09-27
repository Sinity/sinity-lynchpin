"""Polylogue verification lane: read contract, promotion and status."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from lynchpin.sources.polylogue_verification import (
    PolylogueVerificationContractError,
    PolylogueVerificationUnavailable,
    iter_observations,
    read_verification_snapshot,
    verification_lane_path,
)


def _receipt(run_id: str, *, status: str = "passed", semantic: str = "success", **extra: object) -> dict:
    row = {
        "kind": "polylogue.verification-receipt",
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "semantic_status": semantic,
        "started_at": "2026-09-27T10:00:00+00:00",
        "finished_at": "2026-09-27T10:01:00+00:00",
        "duration_s": 60.0,
        "source_revision": "0" * 40,
        "artifact_ref": f"polylogue://verification/{run_id}",
        "pytest": {"selection_mode": "focused", "outcomes": {"passed": 3}},
        "steps": [
            {
                "name": "pytest focused",
                "status": status,
                "exit_code": 0 if status == "passed" else 1,
                "artifact_ref": f"polylogue://verification/{run_id}/steps/01",
            }
        ],
    }
    row.update(extra)
    return row


def _write(path: Path, rows: list[dict], *, tail: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + tail, encoding="utf-8")
    return path


def test_lane_path_matches_the_polylogue_writer(tmp_path: Path) -> None:
    assert verification_lane_path({"POLYLOGUE_VERIFICATION_EVIDENCE_PATH": "/x/e.jsonl"}) == Path("/x/e.jsonl")
    assert verification_lane_path({"XDG_STATE_HOME": str(tmp_path)}) == (
        tmp_path / "polylogue" / "verification" / "evidence.jsonl"
    )


def test_last_row_per_run_wins_and_in_flight_tail_is_ignored(tmp_path: Path) -> None:
    """Anti-vacuity: keep the first row per run_id, or parse the unterminated
    tail, and the settled status or the row count changes."""
    lane = _write(
        tmp_path / "evidence.jsonl",
        [
            _receipt("20260927T100000Z-focused-test-1-aaaa", status="unknown", semantic="running"),
            _receipt("20260927T100000Z-focused-test-1-aaaa", status="interrupted", semantic="failed"),
            _receipt("20260927T100500Z-quick-2-bbbb", status="failed", semantic="failed"),
        ],
        tail='{"kind": "polylogue.verification-rec',
    )

    snapshot = read_verification_snapshot(lane)

    by_id = {row.source_id: row for row in snapshot.observations}
    assert set(by_id) == {
        "polylogue-verification:20260927T100000Z-focused-test-1-aaaa",
        "polylogue-verification:20260927T100500Z-quick-2-bbbb",
    }
    settled = by_id["polylogue-verification:20260927T100000Z-focused-test-1-aaaa"]
    assert settled.status == "interrupted"
    assert settled.recovery_state == "interrupted"
    assert settled.operation == "focused-test"
    assert by_id["polylogue-verification:20260927T100500Z-quick-2-bbbb"].exit_code == 1
    assert snapshot.superseded_rows == 1
    assert snapshot.generation["in_flight_tail"] is True


def test_non_terminal_run_is_not_an_outcome(tmp_path: Path) -> None:
    lane = _write(tmp_path / "e.jsonl", [_receipt("20260927T1Z-all-3-cccc", status="unknown", semantic="running")])

    (row,) = read_verification_snapshot(lane).observations

    assert row.outcome_known is None
    assert row.exit_code is None
    assert "must not be interpreted as a result" in row.caveats_json


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"kind": "something-else", "schema_version": 1}),
        json.dumps(_receipt("20260927T1Z-quick-4-dddd", schema_version=2)),
        json.dumps({k: v for k, v in _receipt("20260927T1Z-quick-4-dddd").items() if k != "started_at"}),
    ],
)
def test_malformed_complete_row_fails_closed(tmp_path: Path, bad: str) -> None:
    """Anti-vacuity: skipping bad rows instead of raising makes this pass silently."""
    lane = tmp_path / "e.jsonl"
    lane.write_text(json.dumps(_receipt("20260927T1Z-quick-5-eeee")) + "\n" + bad + "\n", encoding="utf-8")

    with pytest.raises(PolylogueVerificationContractError):
        read_verification_snapshot(lane)


def test_missing_lane_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(PolylogueVerificationUnavailable):
        read_verification_snapshot(tmp_path / "absent.jsonl")


def test_window_filter_is_half_open(tmp_path: Path) -> None:
    lane = _write(tmp_path / "e.jsonl", [_receipt("20260927T100000Z-quick-6-ffff")])
    snapshot = read_verification_snapshot(lane)
    at = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)

    assert list(iter_observations(start=at, end=at.replace(hour=11), snapshot=snapshot))
    assert not list(iter_observations(start=at.replace(hour=9), end=at, snapshot=snapshot))


def test_promotion_round_trip_is_idempotent(tmp_path: Path) -> None:
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import promote_polylogue_verification_observations

    lane = _write(tmp_path / "e.jsonl", [_receipt("20260927T100000Z-focused-test-7-abcd")])
    rows = read_verification_snapshot(lane).observations
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        assert promote_polylogue_verification_observations(conn, refresh_id="r1", rows=rows) == 1
        assert promote_polylogue_verification_observations(conn, refresh_id="r1", rows=rows) == 1
        stored = conn.execute(
            "SELECT source, work_kind, operation, status, git_commit, artifact_refs FROM work_observation"
        ).fetchall()

    assert len(stored) == 1
    source, work_kind, operation, status, git_commit, artifact_refs = stored[0]
    assert (source, work_kind, operation, status, git_commit) == (
        "polylogue_verification",
        "polylogue_verification_run",
        "focused-test",
        "success",
        "0" * 40,
    )
    assert "steps/01" in artifact_refs


def test_work_promotion_marks_a_malformed_lane_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Anti-vacuity: treating a contract error like an absent lane records
    'unavailable' instead of 'degraded'."""
    from lynchpin.analysis.active import substrate_promote_work as work_promote
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_WORK_OBSERVATIONS,
        SourceSelection,
    )
    from lynchpin.sources.agentctl import AgentctlObservationUnavailable

    lane = Path(verification_lane_path())
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.write_text("broken\n", encoding="utf-8")

    def no_agentctl():
        raise AgentctlObservationUnavailable("absent in test")

    monkeypatch.setattr("lynchpin.sources.agentctl.read_observation_snapshot", no_agentctl)
    statuses: list[dict] = []
    monkeypatch.setattr(work_promote, "record_source_status", lambda *a, **k: statuses.append(k))

    work_promote.promote_work_sources(
        SimpleNamespace(execute=lambda *a, **k: None),
        refresh_id="rid",
        window_start=date(2026, 9, 27),
        window_end=date(2026, 9, 28),
        counts={},
        selection=SourceSelection.from_collection({SOURCE_WORK_OBSERVATIONS}),
    )

    assert statuses[-1]["status"] == "degraded"
    assert "receipt" in statuses[-1]["reason"] or "JSON" in statuses[-1]["reason"]


def test_dataset_status_reports_lane_runs(tmp_path: Path) -> None:
    from lynchpin import materialization

    lane = Path(verification_lane_path())
    _write(lane, [_receipt("20260927T100000Z-quick-8-1234")])

    row = materialization._polylogue_verification_dataset(SimpleNamespace())

    assert row.status == "ready"
    assert row.row_count == 1
    assert row.first_date == date(2026, 9, 27)
    assert row.materialized_paths == ()


def test_xtask_and_polylogue_runs_share_tier_and_outcome_fields(tmp_path: Path) -> None:
    """Both verification producers fill the same columns with the same
    vocabulary. Anti-vacuity: drop XtaskInvocation.operation/outcome_known or
    the Polylogue status mapping and the promoted rows diverge."""
    from lynchpin.sources.xtask_history import XtaskInvocation
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_polylogue_verification_observations,
        promote_work_observations,
    )

    fields = {name: None for name in XtaskInvocation.__dataclass_fields__}
    fields.update(
        source_id="xtask:1",
        command=("check",),
        cwd="/repo",
        started_at=datetime(2026, 9, 27, 9, tzinfo=timezone.utc),
        status="success",
        host="h",
        args_json="[]",
        git_branch="master",
    )
    xtask_row = XtaskInvocation(**fields)
    lane = _write(tmp_path / "e.jsonl", [_receipt("20260927T100000Z-quick-9-5678")])
    polylogue_rows = read_verification_snapshot(lane).observations

    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r", rows=[xtask_row])
        promote_polylogue_verification_observations(
            conn, refresh_id="r", rows=polylogue_rows, delete_existing=False
        )
        rows = conn.execute(
            "SELECT source, operation, status, outcome_known, git_branch FROM work_observation ORDER BY source"
        ).fetchall()

    assert rows == [
        ("polylogue_verification", "quick", "success", True, None),
        ("xtask_history", "check", "success", True, "master"),
    ]


def test_receipt_describing_no_real_run_is_rejected_not_ingested(tmp_path: Path) -> None:
    """Anti-vacuity: remove _invalidity and the placeholder-commit row is ingested."""
    from lynchpin import materialization

    lane = Path(verification_lane_path())
    _write(
        lane,
        [
            _receipt("20260927T100000Z-quick-10-aaaa"),
            _receipt("20260927T100100Z-focused-test-11-bbbb", source_revision="abc123"),
        ],
    )

    snapshot = read_verification_snapshot(lane)
    row = materialization._polylogue_verification_dataset(SimpleNamespace())

    assert [o.source_id for o in snapshot.observations] == ["polylogue-verification:20260927T100000Z-quick-10-aaaa"]
    assert len(snapshot.rejected) == 1 and "not a git commit" in snapshot.rejected[0][1]
    assert row.status == "degraded"


def test_branch_is_carried_when_the_receipt_records_it(tmp_path: Path) -> None:
    lane = _write(tmp_path / "e.jsonl", [_receipt("20260927T100000Z-quick-12-cccc", branch="claude/x")])

    (row,) = read_verification_snapshot(lane).observations

    assert row.git_branch == "claude/x"
