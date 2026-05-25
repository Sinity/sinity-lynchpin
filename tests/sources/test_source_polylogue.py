"""Tests for the lynchpin Polylogue adapter contract."""

from datetime import date
from types import SimpleNamespace

from lynchpin.core.parse import parse_datetime
from lynchpin.sources import polylogue
from lynchpin.sources.polylogue import DaySessionSummary


def _readiness_entry(
    name: str, rows: int, verdict: str = "ready", expected: int | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        insight_name=name,
        row_count=rows,
        verdict=verdict,
        expected_row_count=expected,
    )


def _readiness_report(
    *entries: SimpleNamespace, total_conversations: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(insights=entries, total_conversations=total_conversations)


def _ready_client(
    *entries: SimpleNamespace, total_conversations: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        insight_readiness_report=lambda query: _readiness_report(
            *entries,
            total_conversations=total_conversations,
        ),
        list_session_profile_insights=lambda query: [object()],
        list_archive_coverage_insights=lambda query: [object()],
        list_session_work_event_insights=lambda query: [object()],
    )


def test_session_profile_maps_workflow_shape_and_terminal_state() -> None:
    insight = SimpleNamespace(
        conversation_id="conv-1",
        source_name="claude-code",
        title="Implement reader",
        evidence=SimpleNamespace(
            first_message_at=None,
            last_message_at=None,
            canonical_session_date="2026-05-25",
            repo_paths=(),
            cwd_paths=(),
            message_count=3,
            word_count=20,
            total_cost_usd=0.0,
            cost_is_estimated=False,
            tool_use_count=1,
            thinking_count=0,
            substantive_count=3,
            attachment_count=0,
            wall_duration_ms=1000,
        ),
        inference=SimpleNamespace(
            work_events=(
                {"heuristic_label": "implementation"},
                {"heuristic_label": "implementation"},
                {"heuristic_label": "review"},
            ),
            repo_names=(),
            auto_tags=(),
            engaged_duration_ms=1000,
            work_event_count=0,
            phase_count=1,
            workflow_shape="agentic_loop",
            workflow_shape_confidence=0.86,
            terminal_state="tool_left",
            terminal_state_confidence=0.72,
        ),
    )

    profile = polylogue._session_profile_from_insight(insight)

    assert profile.workflow_shape == "agentic_loop"
    assert profile.workflow_shape_confidence == 0.86
    assert profile.terminal_state == "tool_left"
    assert profile.terminal_state_confidence == 0.72
    assert profile.work_event_kind == "implementation"


def test_daily_activity_uses_day_summaries_without_profile_query(monkeypatch):
    def fake_summaries(*, start=None, end=None):
        return [
            DaySessionSummary(
                date=date(2026, 4, 22),
                session_count=3,
                total_cost_usd=0.0,
                total_messages=30,
                total_words=300,
                work_event_breakdown={"implementation": 2, "review": 1},
                repos_active=("sinity-lynchpin", "polylogue"),
                providers={"claude-code": 3},
            )
        ]

    def fail_profiles():
        raise AssertionError("daily_activity should prefer day summaries")

    monkeypatch.setattr(polylogue, "day_session_summaries", fake_summaries)
    monkeypatch.setattr(polylogue, "iter_session_profiles", fail_profiles)

    result = polylogue.daily_activity(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].provider == "claude-code"
    assert result[0].session_count == 3
    assert result[0].dominant_work_kind == "implementation"
    assert result[0].projects == ("sinity-lynchpin", "polylogue")


def test_daily_activity_includes_codex_sessions_with_null_timestamps(monkeypatch):
    """Regression test: codex sessions have canonical_session_date but null timestamps.

    This ensures daily_activity includes codex sessions even when first/last_message_at
    are NULL.
    """
    def fake_profiles():
        return [
            polylogue.SessionProfile(
                conversation_id="conv-codex-1",
                provider="codex",
                title="Codex session",
                message_count=5,
                word_count=100,
                first_message_at=None,  # codex has no timestamps
                last_message_at=None,
                engaged_duration_ms=1000,
                wall_duration_ms=2000,
                work_event_kind="implementation",
                work_event_projects=("sinity-lynchpin",),
                total_cost_usd=0.0,
                canonical_session_date=date(2026, 5, 20),
                tool_use_count=0,
                thinking_count=0,
                auto_tags=(),
                substantive_count=5,
                attachment_count=0,
                work_event_count=1,
                phase_count=1,
                cost_is_estimated=False,
                workflow_shape=None,
                workflow_shape_confidence=0.0,
                terminal_state=None,
                terminal_state_confidence=0.0,
            ),
            polylogue.SessionProfile(
                conversation_id="conv-claude-1",
                provider="claude-code",
                title="Claude session",
                message_count=10,
                word_count=200,
                first_message_at=parse_datetime("2026-05-20T10:00:00Z"),
                last_message_at=parse_datetime("2026-05-20T11:00:00Z"),
                engaged_duration_ms=2000,
                wall_duration_ms=3000,
                work_event_kind="review",
                work_event_projects=("polylogue",),
                total_cost_usd=0.0,
                canonical_session_date=date(2026, 5, 20),
                tool_use_count=1,
                thinking_count=0,
                auto_tags=(),
                substantive_count=10,
                attachment_count=0,
                work_event_count=1,
                phase_count=1,
                cost_is_estimated=False,
                workflow_shape=None,
                workflow_shape_confidence=0.0,
                terminal_state=None,
                terminal_state_confidence=0.0,
            ),
        ]

    def fake_summaries(*, start=None, end=None):
        # Return empty to force fallback to iter_session_profiles
        return []

    monkeypatch.setattr(polylogue, "iter_session_profiles", fake_profiles)
    monkeypatch.setattr(polylogue, "day_session_summaries", fake_summaries)

    result = polylogue.daily_activity(start=date(2026, 5, 20), end=date(2026, 5, 20))

    # Both codex and claude sessions should appear
    assert len(result) == 2
    providers = {entry.provider for entry in result}
    assert "codex" in providers
    assert "claude-code" in providers

    # Codex session should be properly bucketed
    codex_entry = next(e for e in result if e.provider == "codex")
    assert codex_entry.date == date(2026, 5, 20)
    assert codex_entry.session_count == 1
    assert codex_entry.dominant_work_kind == "implementation"
    assert "sinity-lynchpin" in codex_entry.projects


def test_work_pattern_uses_repos_active_from_day_summaries(monkeypatch):
    monkeypatch.setattr(
        polylogue,
        "day_session_summaries",
        lambda *, start=None, end=None: [
            DaySessionSummary(
                date=date(2026, 4, 22),
                session_count=1,
                total_cost_usd=0.0,
                total_messages=10,
                total_words=100,
                work_event_breakdown={"debugging": 2},
                repos_active=("sinity-lynchpin",),
                providers={"codex": 1},
            )
        ],
    )

    result = polylogue.work_pattern(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].work_kind == "debugging"
    assert result[0].session_count == 2
    assert result[0].top_projects == ("sinity-lynchpin",)


def test_archive_readiness_reports_product_degradation(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=lambda query: _readiness_report(
                _readiness_entry("session_profiles", 0, "empty", 1),
                _readiness_entry("archive_coverage", 0, "empty", 1),
                _readiness_entry("session_work_events", 0, "empty", 1),
                total_conversations=1,
            )
        ),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "degraded"
    assert readiness.conversation_count == 1
    assert readiness.message_count is None
    assert readiness.conversation_stats_count == 1
    assert readiness.session_profile_count == 0
    assert readiness.derives_profiles_from_base_tables is False
    assert readiness.derives_day_summaries_from_profiles is False
    assert "session_profiles" in readiness.reason


def test_archive_readiness_reports_facade_failure(monkeypatch, tmp_path):
    db = tmp_path / "missing.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)

    def fail(query):
        raise RuntimeError("schema mismatch")

    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(insight_readiness_report=fail),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "unavailable"
    assert "schema mismatch" in readiness.reason


def test_archive_readiness_reports_populated_products(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: _ready_client(
            _readiness_entry("session_profiles", 3, "ready", 3),
            _readiness_entry("archive_coverage", 2, "ready", 2),
            _readiness_entry("session_work_events", 7, "ready", 7),
            total_conversations=3,
        ),
    )

    readiness = polylogue.archive_readiness(include_heavy_counts=True)

    assert readiness.status == "ready"
    assert readiness.conversation_count == 3
    assert readiness.session_profile_count == 3
    assert readiness.day_summary_count == 2
    assert readiness.work_event_count == 7
    assert readiness.message_count is None
    assert readiness.provider_event_count is None


def test_archive_readiness_treats_complete_stale_counts_as_ready(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: _ready_client(
            _readiness_entry("session_profiles", 3, "stale", 3),
            _readiness_entry("archive_coverage", 2, "ready", 2),
            _readiness_entry("session_work_events", 7, "stale", 7),
            total_conversations=3,
        ),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "ready"
    assert (
        readiness.reason
        == "materialized profile, archive-coverage, and work-event products are populated"
    )


def test_archive_readiness_degrades_when_required_read_fails(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(polylogue.time, "sleep", lambda seconds: None)

    def fail_profile(query):
        raise RuntimeError("profile_rows_ready is false")

    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=lambda query: _readiness_report(
                _readiness_entry("session_profiles", 3, "ready", 3),
                _readiness_entry("archive_coverage", 2, "ready", 2),
                _readiness_entry("session_work_events", 7, "ready", 7),
                total_conversations=3,
            ),
            list_session_profile_insights=fail_profile,
            list_archive_coverage_insights=lambda query: [object()],
            list_session_work_event_insights=lambda query: [object()],
        ),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "degraded"
    assert "profile_rows_ready is false" in readiness.reason


def test_archive_readiness_retries_transient_required_read_failure(
    monkeypatch, tmp_path
):
    db = tmp_path / "polylogue.db"
    calls = 0
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)
    monkeypatch.setattr(polylogue.time, "sleep", lambda seconds: None)

    def flaky_profile(query):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary live convergence window")
        return [object()]

    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(
            insight_readiness_report=lambda query: _readiness_report(
                _readiness_entry("session_profiles", 3, "ready", 3),
                _readiness_entry("archive_coverage", 2, "ready", 2),
                _readiness_entry("session_work_events", 7, "ready", 7),
                total_conversations=3,
            ),
            list_session_profile_insights=flaky_profile,
            list_archive_coverage_insights=lambda query: [object()],
            list_session_work_event_insights=lambda query: [object()],
        ),
    )

    readiness = polylogue.archive_readiness()

    assert readiness.status == "ready"
    assert calls == 2


def test_daily_activity_gracefully_degrades_on_missing_products(monkeypatch, caplog):
    """Test that daily_activity returns empty list when Polylogue products are missing."""
    def fail_summaries(*, start=None, end=None):
        # Return empty to trigger fallback to profiles
        return []

    def fail_profiles():
        raise polylogue.PolylogueMaterializationError(
            "Polylogue insight products are not materialized: missing or empty products"
        )

    monkeypatch.setattr(polylogue, "day_session_summaries", fail_summaries)
    monkeypatch.setattr(polylogue, "iter_session_profiles", fail_profiles)

    result = polylogue.daily_activity(start=date(2026, 4, 22), end=date(2026, 4, 22))

    # Should return empty list instead of raising
    assert result == []
    # Should have logged a warning
    assert "polylogue daily activity profiles unavailable" in caplog.text


def test_day_session_summaries_gracefully_degrades_on_missing_products(monkeypatch, caplog):
    """Test that day_session_summaries returns empty list when products are missing."""
    def fail_facade():
        raise polylogue.PolylogueMaterializationError(
            "Polylogue insight products are not materialized"
        )

    monkeypatch.setattr(polylogue, "_day_summaries_from_facade", fail_facade)
    # Clear the cache to ensure fresh load
    polylogue._cached_day_summaries = None

    result = polylogue.day_session_summaries(start=date(2026, 4, 22), end=date(2026, 4, 22))

    # Should return empty list instead of raising
    assert result == []
    # Should have logged a warning
    assert "polylogue day summaries unavailable" in caplog.text


def test_session_profiles_for_date_gracefully_degrades(monkeypatch, caplog):
    """Test that session_profiles_for_date returns empty list when products are missing."""
    def fail_facade(*, start=None, end=None):
        raise polylogue.PolylogueMaterializationError(
            "Polylogue bounded session profile product read failed"
        )

    monkeypatch.setattr(polylogue, "_session_profiles_from_facade", fail_facade)

    result = polylogue.session_profiles_for_date(
        start=date(2026, 4, 22), end=date(2026, 4, 22)
    )

    # Should return empty list instead of raising
    assert result == []
    # Should have logged a warning
    assert "polylogue session profiles unavailable for date range" in caplog.text


def test_work_thread_activity_gracefully_degrades_on_missing_products(monkeypatch, caplog):
    """Test that work_thread_activity returns empty list when products are missing."""
    def fail_require():
        raise polylogue.PolylogueMaterializationError(
            "Polylogue insight products are not materialized"
        )

    monkeypatch.setattr(polylogue, "_require_materialized_products", fail_require)

    result = polylogue.work_thread_activity(start=date(2026, 4, 22), end=date(2026, 4, 22))

    # Should return empty list instead of raising
    assert result == []
    # Should have logged a warning
    assert "polylogue work-thread activity unavailable" in caplog.text
