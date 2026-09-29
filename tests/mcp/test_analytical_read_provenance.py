"""Snapshot grain, coverage states, and selection identity of analytical reads."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tests.mcp.conftest import dt, setup_substrate


def _record_refresh(
    conn, refresh_id: str, *, finished, window: tuple[date, date], status: str = "ok"
) -> None:
    conn.execute(
        "INSERT INTO substrate_promotion_run (refresh_id, status, started_at, finished_at) "
        "VALUES (?, 'ok', ?, ?)",
        [refresh_id, finished, finished],
    )
    conn.execute(
        "INSERT INTO substrate_source_status (refresh_id, source, kind, status, reason, row_count, "
        "window_start, window_end, recorded_at) VALUES (?, 'personal_daily_signal', 'stage', ?, NULL, 0, ?, ?, ?)",
        [refresh_id, status, window[0], window[1], finished],
    )


def _stub_personal_convergence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal._ensure_source_materialized_for_read",
        lambda *_args, **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.substrate.ensure_substrate_materialized_for_read",
        lambda **_kwargs: {"status": "ready"},
    )


def _seed_revised_keylog_day(db_path: Path) -> None:
    """Two refreshes of one day: all-zero first, then real counts; one key removed."""
    from lynchpin.substrate.personal import promote_personal_daily_signals

    day = date(2026, 8, 25)
    with duckdb.connect(str(db_path)) as conn:
        promote_personal_daily_signals(
            conn,
            refresh_id="morning",
            rows=[
                ("keylog", day, "keypress_count", 0.0, {}),
                ("keylog", day, "event_count", 0.0, {}),
                ("keylog", day, "session_count", 0.0, {}),
                ("spotify", date(2026, 8, 24), "minutes_played", 5.0, {}),
            ],
        )
        _record_refresh(conn, "morning", finished=dt(2026, 8, 25, 7), window=(date(2026, 8, 1), date(2026, 8, 26)))
        promote_personal_daily_signals(
            conn,
            refresh_id="evening",
            rows=[
                ("keylog", day, "keypress_count", 2846.0, {}),
                ("keylog", day, "event_count", 487923.0, {}),
            ],
            previous_refresh_id="morning",
            replacement_start=day,
            replacement_end=date(2026, 8, 26),
        )
        _record_refresh(conn, "evening", finished=dt(2026, 8, 25, 19), window=(day, date(2026, 8, 26)))
        conn.execute(
            "INSERT INTO substrate_source_status (refresh_id, source, kind, status, reason, row_count, "
            "window_start, window_end, recorded_at) VALUES ('evening', 'commits', 'stage', 'error', "
            "'fixture failure', 0, NULL, NULL, ?)",
            [dt(2026, 8, 25, 19)],
        )


def test_current_read_resolves_revision_and_deletion_while_raw_sql_keeps_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    _seed_revised_keylog_day(db_path)
    _stub_personal_convergence(monkeypatch)

    from lynchpin.mcp.tools.personal import personal_daily_signals
    from lynchpin.mcp.tools.substrate import query_substrate

    current = personal_daily_signals(start="2026-08-24", end="2026-08-25")

    # The bounded evening partition is the current product even though the
    # morning partition holds more physical rows.
    assert current["serving"] == {"kind": "canonical", "refresh_id": "evening", "publication_id": None}
    assert sorted((row["source"], row["metric"], row["value"]) for row in current["rows"]) == [
        ("keylog", "event_count", 487923.0),
        ("keylog", "keypress_count", 2846.0),
        ("spotify", "minutes_played", 5.0),
    ]
    assert current["coverage"]["sources"]["keylog"]["state"] == "observed"
    assert current["coverage"]["sources"]["keylog"]["row_count"] == 2
    assert current["coverage"]["projection"]["status"] == "ok"

    raw = query_substrate(
        "SELECT refresh_id, metric, value FROM personal_daily_signal "
        "WHERE source = 'keylog' ORDER BY refresh_id, metric"
    )
    # Both same-day revisions stay as evidence; the grain says so.
    assert raw["rows"] == [
        ["evening", "event_count", 487923.0],
        ["evening", "keypress_count", 2846.0],
        ["morning", "event_count", 0.0],
        ["morning", "keypress_count", 0.0],
        ["morning", "session_count", 0.0],
    ]
    assert raw["grain"]["personal_daily_signal"]["history"] == "lineage_partitions"
    assert raw["serving"]["refresh_id"] == "evening"
    freshness = raw["freshness"]
    assert [row["source"] for row in freshness["relevant_source_status"]] == ["personal_daily_signal"]
    assert freshness["other_source_status_counts"] == {"error": 1}
    assert "serving_source_status" not in freshness

    detailed = query_substrate("SELECT 1 AS value", detail=True)
    assert detailed["grain"] == {}
    assert detailed["freshness"]["relevant_source_status"] == []
    assert detailed["freshness"]["other_source_status_counts"] == {"error": 1, "ok": 1}
    assert {row["source"] for row in detailed["freshness"]["serving_source_status"]} == {
        "commits", "personal_daily_signal",
    }


def _dataset(name: str, status: str, first: date | None, last: date | None):
    from lynchpin.materialization import MaterializedDataset

    return MaterializedDataset(
        name=name, status=status, authority="fixture", query_surface="fixture",
        materialized_paths=(), raw_roots=(), row_count=None, first_date=first,
        last_date=last, materialization_hint="", reason=f"{name} fixture",
    )


def test_daily_signal_absence_states_stay_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    from lynchpin.substrate.personal import promote_personal_daily_signals

    early = date(2026, 8, 1)
    with duckdb.connect(str(db_path)) as conn:
        promote_personal_daily_signals(
            conn,
            refresh_id="only",
            rows=[(source, early, "count", 1.0, {}) for source in ("keylog", "spotify", "webhistory", "arbtt")],
        )
        _record_refresh(conn, "only", finished=dt(2026, 8, 20), window=(early, date(2026, 8, 20)))
    _stub_personal_convergence(monkeypatch)
    audits = {
        "keylog": _dataset("keylog", "ready", early, date(2026, 8, 31)),
        "spotify": _dataset("spotify", "ready", date(2026, 7, 1), date(2026, 8, 5)),
        "webhistory": _dataset("webhistory", "missing", None, None),
    }
    monkeypatch.setattr("lynchpin.materialization.audit_dataset", lambda name, **_kwargs: audits.get(name))

    from lynchpin.mcp.tools.personal import personal_daily_signals

    result = personal_daily_signals(start="2026-08-10", end="2026-08-11")
    states = {name: entry["state"] for name, entry in result["coverage"]["sources"].items()}

    assert result["rows"] == []
    assert states == {
        "keylog": "observed_empty",
        "spotify": "outside_input_coverage",
        "webhistory": "input_unavailable",
        "arbtt": "input_status_unknown",
    }
    ghost = personal_daily_signals(start="2026-08-10", end="2026-08-11", source="ghost")
    assert ghost["coverage"]["sources"] == {"ghost": {"state": "not_in_projection", "row_count": 0, "input": None}}


def test_activity_coverage_bounds_categories_by_unknown_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources.activity_content import ActivityContentDay

    def day(value: date, focused: float, topics: dict[str, float]) -> ActivityContentDay:
        return ActivityContentDay(
            date=value, focused_seconds=focused, matched_seconds=sum(topics.values()),
            gpt_matched_seconds=0.0, unmatched_seconds=focused - sum(topics.values()),
            matched_ratio=0.0, gpt_matched_ratio=0.0, activity_seconds={},
            content_type_seconds={}, attention_seconds={}, topic_seconds=topics,
            platform_seconds={}, source_counts={},
        )

    rows = [day(date(2026, 1, 1), 1000.0, {"work": 100.0}), day(date(2026, 1, 3), 1000.0, {"work": 300.0, "social": 100.0})]
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal._ensure_source_materialized_for_read",
        lambda *_args, **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda **_kwargs: iter(rows),
    )
    from lynchpin.mcp.tools.personal import activity_content_coverage

    result = activity_content_coverage(start="2026-01-01", end="2026-01-03")

    assert result["classification"] == {
        "dimension": "topic_category",
        "numerator": "seconds classified along dimension",
        "denominator": "focused_seconds",
        "classified_seconds": 500.0,
        "focused_seconds": 2000.0,
        "unknown_seconds": 1500.0,
        "classified_ratio": 0.25,
        "unknown_ratio": 0.75,
    }
    assert result["categories"][0] == {
        "category": "work",
        "classified_seconds": 400.0,
        "share_of_classified": 0.8,
        "focused_seconds_bounds": [400.0, 1900.0],
    }
    assert result["day_coverage"] == {
        "requested_days": 3, "observed_days": 2, "missing_days": 1,
        "first_observed": "2026-01-01", "last_observed": "2026-01-03", "partial": True,
    }

    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_content_days", lambda **_kwargs: iter(()))
    empty = activity_content_coverage(start="2026-01-01", end="2026-01-01")
    assert empty["matched_ratio"] is None
    assert empty["classification"]["classified_ratio"] is None
    assert empty["day_coverage"]["missing_days"] == 1


# ── selected-source identity ─────────────────────────────────────────────────


@pytest.fixture
def serving_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "serving" / "substrate.duckdb"
    target.parent.mkdir()
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: target)
    return target


def _write_value(path: Path, value: int) -> None:
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE OR REPLACE TABLE x AS SELECT ? AS v", [value])


def test_explicit_database_never_answers_from_the_serving_snapshot(serving_path: Path, tmp_path: Path) -> None:
    from lynchpin.substrate.connection import connect, serving_generation, update_read_snapshot

    _write_value(serving_path, 1)
    update_read_snapshot()
    explicit = tmp_path / "other" / "analysis.duckdb"
    explicit.parent.mkdir()
    _write_value(explicit, 2)

    writer = duckdb.connect(str(explicit))
    try:
        with pytest.raises((duckdb.IOException, duckdb.ConnectionException)):
            with connect(explicit, read_only=True):
                pass
    finally:
        writer.close()

    update_read_snapshot(explicit)
    _write_value(explicit, 3)
    writer = duckdb.connect(str(explicit))
    try:
        with serving_generation(explicit) as generation:
            assert generation.connection.execute("SELECT v FROM x").fetchone() == (2,)
            assert generation.database_path == explicit.with_suffix(".read-snapshot.duckdb")
    finally:
        writer.close()


def test_path_aliases_resolve_to_the_guarded_serving_resource(serving_path: Path, tmp_path: Path) -> None:
    from lynchpin.substrate.connection import CandidateGenerationRejected, connect

    _write_value(serving_path, 1)
    dotted = serving_path.parent / ".." / serving_path.parent.name / serving_path.name
    linked = tmp_path / "linked.duckdb"
    linked.symlink_to(serving_path)

    for alias in (dotted, linked):
        with pytest.raises(CandidateGenerationRejected, match="direct canonical"):
            with connect(alias):
                pass


# ── project selection ────────────────────────────────────────────────────────


def test_project_selection_keeps_omitted_empty_and_unknown_distinct() -> None:
    from lynchpin.core.projects import UnknownProjectError, resolve_project_selection
    from lynchpin.graph.evidence_projects import selected_projects

    assert resolve_project_selection(None) is None
    assert resolve_project_selection([]) == ()
    assert resolve_project_selection(["lynchpin", "sinex"]) == ("sinex", "sinity-lynchpin")
    with pytest.raises(UnknownProjectError) as raised:
        resolve_project_selection(["sinex", "not-a-project"])
    assert raised.value.values == ("not-a-project",)
    with pytest.raises(UnknownProjectError):
        selected_projects(["not-a-project"])


def _commit_node(conn, *, refresh_id: str, project: str, day: date, sha: str) -> None:
    conn.execute(
        "INSERT INTO evidence_node (refresh_id, id, kind, source, date, project, summary, payload, caveats) "
        "VALUES (?, ?, 'commit', 'git', ?, ?, 'commit', ?, '[]')",
        [refresh_id, f"git:{project}:{sha}", day, project, json.dumps({"commit": sha})],
    )


def _symbol(conn, *, refresh_id: str, project: str, day: date, name: str) -> None:
    conn.execute(
        "INSERT INTO symbol_change (sha, project, date, path, change_type, qualified_name, symbol_kind, "
        "exported, breaking_candidate, refresh_id, materialized_at) "
        "VALUES ('s', ?, ?, 'src/lib.rs', 'ADDED', ?, 'function', FALSE, FALSE, ?, ?)",
        [project, day, name, refresh_id, dt(2026, 5, 10)],
    )


def test_filtered_symbol_velocity_excludes_unrelated_rows_on_both_join_sides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    rid = "symbols"
    with duckdb.connect(str(db_path)) as conn:
        _commit_node(conn, refresh_id=rid, project="sinex", day=date(2026, 5, 1), sha="a")
        _commit_node(conn, refresh_id=rid, project="polylogue", day=date(2026, 5, 2), sha="b")
        _symbol(conn, refresh_id=rid, project="sinex", day=date(2026, 5, 3), name="sinex::f")
        _symbol(conn, refresh_id=rid, project="polylogue", day=date(2026, 5, 4), name="polylogue.g")

    from lynchpin.mcp.tools.velocity import symbol_velocity

    rows = symbol_velocity(projects=["sinex"], refresh_id=rid)
    assert [(row["project"], row["date"], row["commit_count"], row["symbols_total"]) for row in rows] == [
        ("sinex", "2026-05-01", 1, 0),
        ("sinex", "2026-05-03", 0, 1),
    ]
    assert symbol_velocity(projects=[], refresh_id=rid) == []
    assert {row["project"] for row in symbol_velocity(refresh_id=rid)} == {"sinex", "polylogue"}


# ── public options reach readers ─────────────────────────────────────────────


@pytest.mark.parametrize("dimension", ["activity", "attention_level", "content_type", "platform", "topic_category"])
def test_semantic_dimensions_reach_the_substrate_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dimension: str
) -> None:
    db_path = setup_substrate(tmp_path, monkeypatch)
    from lynchpin.sources.activity_content import ActivityTitleUsage
    from lynchpin.substrate.personal import promote_activity_title_usage

    with duckdb.connect(str(db_path)) as conn:
        promote_activity_title_usage(
            conn,
            refresh_id="titles",
            rows=[ActivityTitleUsage(
                title_hash="h", app="app", normalized_title="t", example_title="t",
                focused_seconds=600.0, span_count=1, first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 2), matched=True, classification_source="fixture",
                confidence=1.0, activity="reading", content_type="article",
                attention_level="deep", topic_category="work", platform="web",
            )],
        )

    from lynchpin.mcp.tools.personal import activity_semantic_daily

    rows = activity_semantic_daily(start="2026-05-01", end="2026-05-03", dimension=dimension)
    assert len(rows) == 1 and rows[0]["focused_seconds"] == 600.0
    assert rows[0]["dimension_value"] != "unknown"


def test_semantic_view_rejects_the_unbacked_mode_dimension() -> None:
    from lynchpin.mcp.tools.personal import activity_semantic_daily

    with pytest.raises(ValueError, match="dimension must be one of"):
        activity_semantic_daily(start="2026-05-01", end="2026-05-02", dimension="mode")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "reports", "view": "anomaly", "project": "sinex"},
        {"action": "operator", "view": "readiness", "start": "2026-01-01", "end": "2026-01-02", "project": "sinex"},
        {"action": "activity", "view": "coverage", "limit": 5},
        {"action": "activity", "view": "focus", "limit": 5},
        {"action": "communications", "view": "daily", "limit": 5},
        {"action": "reports", "view": "ai_efficiency", "project": " "},
    ],
)
def test_personal_router_rejects_options_no_reader_consumes(kwargs: dict[str, object]) -> None:
    from lynchpin.mcp.tools.public import lynchpin_personal

    result = lynchpin_personal(**kwargs)
    assert result["ok"] is False
    assert result["error_code"] == "invalid_argument"


def test_project_velocity_requires_a_project_instead_of_querying_a_placeholder() -> None:
    from lynchpin.mcp.tools.public import lynchpin_project

    result = lynchpin_project(action="velocity", view="daily", start="2026-01-01", end="2026-01-02")
    assert result["ok"] is False
    assert result["error_code"] == "missing_argument"
