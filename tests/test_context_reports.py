from __future__ import annotations

from datetime import date

from lynchpin.context.bundles import EvidenceBundle, EvidenceQuery
from lynchpin.context.reports import _render_period_report, build_period_report, summarize_evidence_bundle
from lynchpin.context.trust import SurfaceFreshness, TrustLevel
from lynchpin.periods import Period


def _query(query_id: str, rows: list[dict[str, object]]) -> EvidenceQuery:
    return EvidenceQuery(
        query_id=query_id,
        title=query_id,
        sql=f"SELECT * FROM {query_id}",
        params=[],
        rows=rows,
    )


def test_summarize_evidence_bundle_tracks_evidence_presence_and_focus_shape() -> None:
    bundle = EvidenceBundle(
        period=Period("month", "2026-03", date(2026, 3, 1), date(2026, 3, 31)),
        generated_at="2026-03-16T12:00:00Z",
        freshness=[
            SurfaceFreshness(
                surface="processed_delivery_telemetry",
                date_column="date",
                max_value="2026-03-01",
                row_count=1,
                days_stale=0,
                level=TrustLevel.fresh,
            ),
        ],
        queries=[
            _query(
                "delivery_telemetry",
                [
                    {
                        "date": "2026-03-01",
                        "active_hours": 5.5,
                        "total_commits": 2,
                        "command_count": 10,
                        "chat_sessions": 1,
                        "chat_engaged_minutes": 12.5,
                        "repos_json": '["sinity-lynchpin"]',
                        "ai_models_json": '["gpt-5"]',
                    }
                ],
            ),
            _query(
                "chat_activity",
                [
                    {
                        "date": "2026-03-01",
                        "provider": "codex",
                        "session_count": 1,
                        "total_messages": 5,
                        "total_words": 120,
                        "engaged_minutes": 12.5,
                        "dominant_work_kind": "implementation",
                        "projects_json": '["sinity-lynchpin"]',
                    }
                ],
            ),
            _query(
                "focus_spans",
                [
                    {
                        "date": "2026-03-01",
                        "project": "sinity-lynchpin",
                        "mode": "coding",
                        "duration_seconds": 600.0,
                    }
                ],
            ),
            _query(
                "focus_loops",
                [
                    {
                        "date": "2026-03-01",
                        "start": "2026-03-01T09:00:00Z",
                        "end_time": "2026-03-01T09:15:00Z",
                        "dominant_project": "sinity-lynchpin",
                        "dominant_mode": "coding",
                        "duration_minutes": 15.0,
                    }
                ],
            ),
            _query(
                "git_daily",
                [
                    {
                        "date": "2026-03-01",
                        "repo": "/realm/project/sinity-lynchpin",
                        "commit_count": 2,
                        "churn": 40,
                        "net_loc": 20,
                    }
                ],
            ),
            _query(
                "git_file_facts",
                [
                    {
                        "date": "2026-03-01",
                        "path_root": "lynchpin/context",
                        "lines_changed": 40,
                    }
                ],
            ),
            _query(
                "circadian",
                [
                    {
                        "date": "2026-03-01",
                        "hour": 10,
                        "active_minutes": 30.0,
                        "recovery_minutes": 15.0,
                        "dominant_mode": "coding",
                        "dominant_project": "sinity-lynchpin",
                    }
                ],
            ),
            _query(
                "polylogue_sessions",
                [
                    {
                        "created_at": "2026-03-01T12:00:00Z",
                        "last_message_at": "2026-03-01T13:00:00Z",
                        "title": "March architecture pass",
                        "canonical_projects_json": '["sinity-lynchpin"]',
                    }
                ],
            ),
        ],
        notes=[],
        bundle_ref="artefacts/context/evidence/2026/2026-03",
    )

    summary = summarize_evidence_bundle(bundle)

    assert summary["evidence"]["days_with_evidence"] == 1
    assert summary["evidence"]["period_days"] == 31
    assert summary["evidence"]["query_rows"]["focus_spans"] == 1
    assert "focus_spans" in summary["evidence"]["surfaces_present"]
    assert summary["delivery"]["active_hours"] == 5.5
    assert summary["chat"]["projects"] == [("sinity-lynchpin", 1)]
    assert summary["focus"]["top_modes"] == [("coding", 10)]
    assert summary["focus"]["top_projects"] == [("sinity-lynchpin", 10)]
    assert summary["circadian"]["recovery_minutes_total"] == 15.0
    assert summary["circadian"]["dominant_projects"] == [("sinity-lynchpin", 30)]
    assert summary["patterns"]["episode_count"] == 0
    assert summary["patterns"]["anomaly_count"] == 0
    assert summary["patterns"]["recent_focus_loops"][0]["dominant_project"] == "sinity-lynchpin"


def test_render_period_report_includes_evidence_section() -> None:
    bundle = EvidenceBundle(
        period=Period("month", "2026-03", date(2026, 3, 1), date(2026, 3, 31)),
        generated_at="2026-03-16T12:00:00Z",
        freshness=[],
        queries=[],
        notes=[],
        bundle_ref="artefacts/context/evidence/2026/2026-03",
    )
    markdown = _render_period_report(
        bundle,
        {
            "evidence": {
                "days_with_evidence": 2,
                "period_days": 31,
                "surfaces_present": ["delivery_telemetry"],
                "query_rows": {"delivery_telemetry": 2},
            },
            "delivery": {"active_hours": 1.0, "total_commits": 0, "command_count": 0, "chat_sessions": 0, "chat_engaged_minutes": 0.0, "top_repos": [], "top_models": []},
            "attention": {"avg_entropy": None, "avg_rotation_speed": None, "top_projects": []},
            "chat": {"providers": [], "work_kinds": [], "total_messages": 0, "total_words": 0, "engaged_minutes": 0.0, "total_cost_usd": 0.0, "top_session_titles": [], "top_session_projects": []},
            "git": {"repos": [], "churn": [], "net_loc": [], "top_paths": []},
            "focus": {"top_spans": [], "top_loops": [], "top_modes": [], "top_projects": [], "total_switches": 0, "project_switches": 0, "mode_switches": 0, "avg_focus_minutes": None, "longest_focus_minutes": None, "avg_fragmentation": None},
            "patterns": {"episode_count": 1, "episode_labels": ["coding sprint"], "anomaly_count": 2, "anomaly_kinds": ["mode_shift", "project_attention_shift"], "recent_focus_loops": [{"dominant_project": "sinity-lynchpin", "duration_minutes": 25.0, "start": "2026-03-01T10:00:00+00:00"}]},
            "circadian": {"active_minutes": [], "recovery_minutes_total": 0.0, "dominant_modes": [], "dominant_projects": []},
        },
    )

    assert "## Evidence" in markdown
    assert "Days with evidence: 2 / 31" in markdown
    assert "Surface rows: delivery_telemetry=2" in markdown
    assert "## Patterns" in markdown
    assert "Episodes: 1 (coding sprint)" in markdown


def test_build_period_report_does_not_persist_evidence_when_write_files_disabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_bundle(scale, key, *, write):
        captured["write"] = write
        return EvidenceBundle(
            period=Period("day", "2026-03-01", date(2026, 3, 1), date(2026, 3, 1)),
            generated_at="2026-03-16T12:00:00Z",
            freshness=[],
            queries=[],
            notes=[],
            bundle_ref=None,
        )

    monkeypatch.setattr("lynchpin.context.reports.build_period_evidence_bundle", _fake_bundle)

    report = build_period_report("day", "2026-03-01", write_files=False)

    assert captured["write"] is False
    assert report.output_path is None
    assert report.bundle_ref is None if hasattr(report, "bundle_ref") else True
