"""Integration contracts against the pinned owner's real public models."""

from types import SimpleNamespace

import pytest
from polylogue.analysis.readiness import InsightReadinessEntry, InsightReadinessReport

from lynchpin.sources import polylogue


def test_current_owner_readiness_does_not_require_retired_product(
    monkeypatch, tmp_path
):
    report = InsightReadinessReport(
        checked_at="2026-01-01T00:00:00Z",
        total_sessions=1,
        insights=tuple(
            InsightReadinessEntry(
                insight_name=name, display_name=name, row_count=1, expected_row_count=1
            )
            for name in ("session_profiles", "archive_coverage")
        ),
    )

    def readiness(query):
        assert query.insights == ("session_profiles", "archive_coverage")
        return report

    monkeypatch.setattr(
        polylogue, "_default_polylogue_db_path", lambda: tmp_path / "absent.db"
    )
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=readiness,
            list_session_profile_insights=lambda query: [],
            list_archive_coverage_insights=lambda query: [],
        ),
    )
    result = polylogue.archive_readiness()
    assert result.status == "ready", result.reason
    assert result.work_event_count is None


def test_retired_work_events_are_unavailable_not_empty(monkeypatch):
    monkeypatch.setattr(polylogue, "_work_events_from_sqlite", lambda **kwargs: None)
    with pytest.raises(polylogue.PolylogueMaterializationError, match="retired"):
        polylogue.work_events(start=None, end=None)


def test_current_owner_profile_has_no_retired_inference_fields():
    from polylogue.analysis.archive import SessionProfileInsight
    from polylogue.analysis.archive_models import (
        ArchiveInsightProvenance,
        SessionEvidencePayload,
        SessionInferencePayload,
    )

    insight = SessionProfileInsight(
        session_id="fixture",
        logical_session_id="fixture",
        origin="codex",
        provenance=ArchiveInsightProvenance(materializer_version=1),
        evidence=SessionEvidencePayload(message_count=3),
        inference=SessionInferencePayload(engaged_duration_ms=1000),
    )
    result = polylogue._session_profile_from_insight(insight)
    assert result.provider == "codex"
    assert result.message_count == 3
    assert result.engaged_duration_ms == 1000
    assert result.work_event_count is None
    assert result.phase_count is None


def test_current_owner_day_coverage_has_no_work_event_breakdown(monkeypatch):
    from polylogue.analysis.archive import ArchiveCoverageInsight

    row = ArchiveCoverageInsight(bucket="2026-01-01", session_count=2, message_count=5)
    monkeypatch.setattr(polylogue, "_require_materialized_products", lambda: None)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            list_archive_coverage_insights=lambda query: [row],
        ),
    )
    [result] = polylogue._day_summaries_from_facade()
    assert result.session_count == 2
    assert result.total_messages == 5
    assert result.work_event_breakdown == {}


def test_current_owner_thread_uses_current_method_and_origin_breakdown(monkeypatch):
    from datetime import date
    from polylogue.analysis.archive import ThreadInsight
    from polylogue.analysis.archive_models import (
        ArchiveInsightProvenance,
        ThreadPayload,
    )

    insight = ThreadInsight(
        thread_id="fixture",
        root_id="fixture",
        provenance=ArchiveInsightProvenance(materializer_version=1),
        thread=ThreadPayload(
            start_time="2026-01-01T00:00:00Z",
            session_count=1,
            origin_breakdown={"codex-session": 1},
            wall_duration_ms=60000,
        ),
    )
    monkeypatch.setattr(polylogue, "_require_materialized_products", lambda: None)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            list_thread_insights=lambda query: [insight],
        ),
    )
    [result] = polylogue.work_thread_activity(
        start=date(2026, 1, 1), end=date(2026, 1, 1)
    )
    assert result.provider == "codex"
    assert result.total_wall_minutes == 1
    assert result.dominant_work_kind is None


def test_pinned_owner_facade_imports_and_exposes_current_read_methods():
    from polylogue.api.sync import SyncPolylogue

    for method in (
        "insight_readiness_report",
        "list_session_profile_insights",
        "list_archive_coverage_insights",
        "list_thread_insights",
    ):
        assert callable(getattr(SyncPolylogue, method))
    assert not hasattr(SyncPolylogue, "list_session_work_event_insights")


def test_retired_product_promotion_records_unavailable(monkeypatch):
    from datetime import date
    from lynchpin.analysis.active import substrate_promote_ai
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_AI_WORK_EVENTS,
        SourceSelection,
    )

    monkeypatch.setattr(
        polylogue, "archive_readiness", lambda: SimpleNamespace(status="ready")
    )

    def retired(**kwargs):
        raise polylogue.PolylogueMaterializationError("retired work-event product")

    monkeypatch.setattr(polylogue, "work_events", retired)
    statuses = []
    monkeypatch.setattr(
        substrate_promote_ai,
        "record_source_status",
        lambda *args, **kwargs: statuses.append(kwargs),
    )
    substrate_promote_ai.promote_ai_sources(
        object(),
        refresh_id="fixture",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 2),
        counts={},
        selection=SourceSelection.from_collection({SOURCE_AI_WORK_EVENTS}),
    )
    assert statuses[-1]["status"] == "unavailable"
    assert "retired" in statuses[-1]["reason"]


def test_unknown_work_event_coverage_remains_visible(monkeypatch, tmp_path):
    from lynchpin.graph import source_readiness

    monkeypatch.setattr(
        source_readiness,
        "archive_readiness",
        lambda **kwargs: SimpleNamespace(
            status="ready",
            reason="current products populated",
            db_path=tmp_path / "index.db",
            conversation_count=1,
            derives_profiles_from_base_tables=False,
            derives_day_summaries_from_profiles=False,
            work_event_count=None,
        ),
    )
    result = source_readiness._polylogue_source(include_product_counts=False)
    assert result.status == "available"
    assert any(
        "work-event products are unavailable" in caveat for caveat in result.caveats
    )
