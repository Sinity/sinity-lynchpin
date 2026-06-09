from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.mcp.conftest import dt, setup_substrate


def _stub_signal_materialization(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_ensure_substrate_materialized_for_read(*, caller: str, window=None):
        calls.append(caller)
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.signals.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    return calls


def test_daily_rhythm_fingerprint_percentages_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.signals import daily_rhythm_fingerprint
    from lynchpin.sources.git import GitCommitFact
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.work_commits import promote_commits

    commits = [
        GitCommitFact(
            repo="lynchpin",
            commit=f"sha{i}",
            authored_at=dt(2026, 5, 4 + i, hour),
            author="Sinity",
            subject="feat: test",
            lines_added=1,
            lines_deleted=0,
            lines_changed=1,
            files_changed=1,
            paths=(f"src/{i}.py",),
            path_roots=("src",),
        )
        for i, hour in enumerate((6, 13, 18, 23))
    ]
    with connect(substrate_path()) as conn:
        promote_commits(conn, refresh_id="r1", facts=commits)

    calls = _stub_signal_materialization(monkeypatch)
    row = daily_rhythm_fingerprint(refresh_id="r1")[0]
    assert calls == []
    assert (
        row["morning_pct"]
        + row["afternoon_pct"]
        + row["evening_pct"]
        + row["night_pct"]
        == 100.0
    )
    assert 0.0 <= row["weekend_pct"] <= 100.0


def test_daily_rhythm_defaults_to_commit_fact_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.signals import daily_rhythm_fingerprint
    from lynchpin.sources.git import GitCommitFact
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.work_commits import promote_commits

    with connect(substrate_path()) as conn:
        promote_commits(
            conn,
            refresh_id="commit-refresh",
            facts=[
                GitCommitFact(
                    repo="lynchpin",
                    commit="sha1",
                    authored_at=dt(2026, 5, 4, 6),
                    author="Sinity",
                    subject="feat: test",
                    lines_added=1,
                    lines_deleted=0,
                    lines_changed=1,
                    files_changed=1,
                    paths=("src/a.py",),
                    path_roots=("src",),
                )
            ],
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "newer-status-only",
                "spotify_daily",
                "empty",
                "status row without commit rows",
                0,
                date(2026, 5, 4),
                date(2026, 5, 4),
                dt(2026, 5, 5),
            ],
        )

    calls = _stub_signal_materialization(monkeypatch)
    result = daily_rhythm_fingerprint()
    assert calls == ["daily_rhythm_fingerprint"]
    assert result[0]["project"] == "lynchpin"
    assert result[0]["total_commits"] == 1


def test_source_observation_bounds_uses_substrate_spotify_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.signals import source_observation_bounds
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO spotify_daily (
                date, track_count, minutes_played, unique_artists,
                unique_tracks, top_artists, top_tracks, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [date(2025, 12, 18), 1, 3.0, 1, 1, ["Artist"], ["Track"], "r1"],
        )

    rows = {row["source"]: row for row in source_observation_bounds()}
    assert rows["spotify"]["last_known_data"] == "2025-12-18"
    assert rows["spotify"]["basis"] == "substrate"
    assert "stale" not in rows["spotify"]


def test_source_observation_bounds_renders_source_observation_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.mcp.tools.signals import source_observation_bounds
    from lynchpin.sources.source_observations import SourceObservation

    monkeypatch.setattr(
        "lynchpin.sources.source_observations.source_observations",
        lambda **_kwargs: (
            SourceObservation(
                source="spotify",
                available=True,
                last_observed=date(2025, 12, 18),
                basis="substrate",
                recommendation=None,
                path=None,
            ),
        ),
    )

    assert source_observation_bounds() == [
        {
            "source": "spotify",
            "available": True,
            "last_known_data": "2025-12-18",
            "recommendation": None,
            "basis": "substrate",
            "path": None,
        }
    ]


def test_operator_day_rows_returns_date_filtered_dicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from datetime import timedelta

    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.mcp.tools.signals import operator_day_rows
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import promote_operator_day_rows

    base = date(2026, 6, 1)
    rows = [
        OperatorDay(
            date=base + timedelta(days=i),
            git_commits=i + 1,
            stress_mean=float(30 + i),
            spo2_pct=97.0 if i % 2 == 0 else None,
            sources_present=frozenset({"git", "health"}),
        )
        for i in range(5)
    ]
    with connect(substrate_path()) as conn:
        promote_operator_day_rows(conn, refresh_id="r1", rows=rows)

    _stub_signal_materialization(monkeypatch)
    result = operator_day_rows(start="2026-06-02", end="2026-06-04", refresh_id="r1")

    assert len(result) == 3
    # i=1 → date 2026-06-02 (odd): spo2_pct absent
    assert result[0]["date"] == "2026-06-02"
    assert result[0]["git_commits"] == 2
    assert result[0]["spo2_pct"] is None  # odd index → absent
    # i=2 → date 2026-06-03 (even): spo2_pct set
    assert result[1]["date"] == "2026-06-03"
    assert result[1]["spo2_pct"] == 97.0


def test_operator_day_rows_column_narrowing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.mcp.tools.signals import operator_day_rows
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import promote_operator_day_rows

    with connect(substrate_path()) as conn:
        promote_operator_day_rows(
            conn,
            refresh_id="r1",
            rows=[OperatorDay(date=date(2026, 6, 1), git_commits=7, sources_present=frozenset({"git"}))],
        )

    _stub_signal_materialization(monkeypatch)
    result = operator_day_rows(refresh_id="r1", columns=["date", "git_commits"])

    assert len(result) == 1
    assert set(result[0].keys()) == {"date", "git_commits"}
    assert result[0]["git_commits"] == 7


def test_operator_day_rows_invalid_column_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.mcp.tools.signals import operator_day_rows
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import promote_operator_day_rows

    with connect(substrate_path()) as conn:
        promote_operator_day_rows(
            conn,
            refresh_id="r1",
            rows=[OperatorDay(date=date(2026, 6, 1), sources_present=frozenset())],
        )

    _stub_signal_materialization(monkeypatch)
    result = operator_day_rows(refresh_id="r1", columns=["date", "not_a_real_column"])
    assert len(result) == 1
    assert "error" in result[0]


def test_cross_source_lag_reports_unavailable_when_attribution_has_no_event_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.signals import cross_source_lag
    from lynchpin.sources.git import GitCommitFact
    from lynchpin.sources.polylogue import WorkEvent
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.work_ai import promote_ai_work_events
    from lynchpin.substrate.work_commits import promote_commits

    with connect(substrate_path()) as conn:
        promote_commits(
            conn,
            refresh_id="r1",
            facts=[
                GitCommitFact(
                    repo="lynchpin",
                    commit="a" * 40,
                    authored_at=dt(2026, 5, 4, 12),
                    author="Sinity",
                    subject="feat: test",
                    lines_added=1,
                    lines_deleted=0,
                    lines_changed=1,
                    files_changed=1,
                    paths=("src/commit.py",),
                    path_roots=("src",),
                )
            ],
            project_lookup=lambda _repo: "lynchpin",
            annotations={
                "a" * 40: {
                    "ai_attribution": {
                        "classification": "medium",
                        "matched_via": "polylogue_session_project_day",
                    }
                }
            },
        )
        promote_ai_work_events(
            conn,
            refresh_id="r1",
            events=[
                WorkEvent(
                    event_id="we1",
                    conversation_id="conv1",
                    provider="claude-code",
                    kind="implementation",
                    confidence=0.8,
                    start=dt(2026, 5, 4, 11),
                    end=dt(2026, 5, 4, 11),
                    duration_ms=1,
                    file_paths=("src/other.py",),
                    tools_used=("Edit",),
                    summary="test",
                )
            ],
            project_resolver=lambda _event: "lynchpin",
        )

    calls = _stub_signal_materialization(monkeypatch)
    result = cross_source_lag(refresh_id="r1")
    assert calls == []
    assert result["materialization"]["status"] == "pinned"
    assert result["materialization"]["caller"] == "cross_source_lag"
    assert result["attributed_commits"] == 1
    assert result["pairs"] == 0
    assert result["caveats"]


def test_operator_day_rows_finds_promoted_data_via_source_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """operator_day_rows() with no refresh_id finds rows promoted with a source_status record."""
    from datetime import date

    setup_substrate(tmp_path, monkeypatch)
    _stub_signal_materialization(monkeypatch)

    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.analysis.active.substrate_promote_status import record_source_status
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import promote_operator_day_rows

    rows = [
        OperatorDay(date=date(2026, 5, 1), git_commits=7, aw_deep_work_min=90.0, sources_present=frozenset({"git"})),
        OperatorDay(date=date(2026, 5, 2), git_commits=2, aw_deep_work_min=30.0, sources_present=frozenset({"git"})),
    ]
    with connect(substrate_path()) as conn:
        n = promote_operator_day_rows(conn, refresh_id="op-r1", rows=rows)
        record_source_status(
            conn,
            refresh_id="op-r1",
            source="operator_day",
            status="ok",
            reason=None,
            row_count=n,
            window_start=date(2026, 5, 1),
            window_end=date(2026, 5, 2),
        )

    from lynchpin.mcp.tools.signals import operator_day_rows

    result = operator_day_rows(start="2026-05-01", end="2026-05-02")
    assert len(result) == 2
    assert result[0]["date"] == "2026-05-01"
    assert result[0]["git_commits"] == 7
    assert result[1]["git_commits"] == 2
