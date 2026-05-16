from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.mcp.conftest import dt, setup_substrate


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

    row = daily_rhythm_fingerprint(refresh_id="r1")[0]
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
                "calendar",
                "empty",
                "status row without commit rows",
                0,
                date(2026, 5, 4),
                date(2026, 5, 4),
                dt(2026, 5, 5),
            ],
        )

    result = daily_rhythm_fingerprint()
    assert result[0]["project"] == "lynchpin"
    assert result[0]["total_commits"] == 1


def test_export_staleness_uses_substrate_spotify_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.signals import export_staleness
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

    rows = {row["source"]: row for row in export_staleness()}
    assert rows["spotify"]["last_known_data"] == "2025-12-18"
    assert rows["spotify"]["basis"] == "substrate"
    assert rows["spotify"]["stale"] is True


def test_export_staleness_renders_source_freshness_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.mcp.tools.signals import export_staleness
    from lynchpin.sources.freshness import SourceFreshness

    monkeypatch.setattr(
        "lynchpin.sources.freshness.source_freshness",
        lambda **_kwargs: (
            SourceFreshness(
                source="spotify",
                available=True,
                last_observed=date(2025, 12, 18),
                basis="substrate",
                stale=True,
                recommendation="Request new Spotify GDPR export",
                path=None,
            ),
        ),
    )

    assert export_staleness() == [
        {
            "source": "spotify",
            "available": True,
            "last_known_data": "2025-12-18",
            "stale": True,
            "recommendation": "Request new Spotify GDPR export",
            "basis": "substrate",
            "path": None,
        }
    ]


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

    result = cross_source_lag(refresh_id="r1")
    assert result["attributed_commits"] == 1
    assert result["pairs"] == 0
    assert result["caveats"]
