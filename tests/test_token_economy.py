"""Tests for the token-economy composite (Arc L.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.composite.token_economy import (
    render_token_economy_summary,
    token_economy_summary,
)
from lynchpin.sources.polylogue import ConversationTranscript, SessionProfile

UTC = timezone.utc


def _profile(
    *,
    conversation_id: str,
    projects: tuple[str, ...] = (),
    kind: str | None = None,
    provider: str = "claude-code",
) -> SessionProfile:
    return SessionProfile(
        conversation_id=conversation_id,
        provider=provider,
        title="t",
        message_count=10,
        word_count=100,
        first_message_at=datetime(2026, 5, 7, 12, tzinfo=UTC),
        last_message_at=datetime(2026, 5, 7, 13, tzinfo=UTC),
        engaged_duration_ms=60_000,
        wall_duration_ms=3_600_000,
        work_event_kind=kind,
        work_event_projects=projects,
        total_cost_usd=0.0,
        canonical_session_date=date(2026, 5, 7),
        tool_use_count=0,
        thinking_count=0,
        auto_tags=(),
    )


def _transcript(
    *,
    conversation_id: str,
    user_prompt_tokens: int = 100,
    dialogue_tokens: int = 250,
    all_message_tokens: int = 320,
    provider: str = "claude-code",
    on_date: date = date(2026, 5, 7),
) -> ConversationTranscript:
    return ConversationTranscript(
        conversation_id=conversation_id,
        provider=provider,
        title="t",
        canonical_session_date=on_date,
        first_message_at=datetime(on_date.year, on_date.month, on_date.day, 12, tzinfo=UTC),
        last_message_at=datetime(on_date.year, on_date.month, on_date.day, 13, tzinfo=UTC),
        messages=(),
        user_prompt_count=2,
        user_prompt_tokens=user_prompt_tokens,
        dialogue_tokens=dialogue_tokens,
        all_message_tokens=all_message_tokens,
    )


def test_summary_aggregates_per_project_day_and_unique_totals():
    profiles = (
        _profile(conversation_id="c1", projects=("polylogue",), kind="implementation"),
        _profile(conversation_id="c2", projects=("sinity-lynchpin",), kind="research"),
    )
    transcripts = (
        _transcript(conversation_id="c1", user_prompt_tokens=100, dialogue_tokens=250, all_message_tokens=320),
        _transcript(conversation_id="c2", user_prompt_tokens=80, dialogue_tokens=180, all_message_tokens=240),
    )

    summary = token_economy_summary(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        transcripts=transcripts, profiles=profiles,
    )

    assert summary.unique_conversation_count == 2
    assert summary.unique_user_prompt_tokens == 180
    assert summary.unique_all_message_tokens == 560
    assert summary.window_session_count == 2  # one per (project, day) row
    assert {row.project for row in summary.rows} == {"polylogue", "sinity-lynchpin"}
    assert summary.kind_breakdown == {"implementation": 1, "research": 1}


def test_multi_project_session_multi_counts_in_rows_but_not_unique():
    """A conversation attributed to two projects creates two rows but counts once for unique totals."""
    profiles = (
        _profile(conversation_id="cX", projects=("polylogue", "sinity-lynchpin"), kind="implementation"),
    )
    transcripts = (
        _transcript(conversation_id="cX", user_prompt_tokens=100, dialogue_tokens=250, all_message_tokens=320),
    )

    summary = token_economy_summary(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        transcripts=transcripts, profiles=profiles,
    )
    assert summary.unique_conversation_count == 1
    assert summary.unique_all_message_tokens == 320
    # Two project rows, each carrying full token counts.
    assert len(summary.rows) == 2
    assert summary.window_all_message_tokens == 640


def test_unattributed_session_when_no_projects():
    profiles = (_profile(conversation_id="c1", projects=(), kind="conversation"),)
    transcripts = (_transcript(conversation_id="c1"),)

    summary = token_economy_summary(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        transcripts=transcripts, profiles=profiles,
    )
    assert len(summary.rows) == 1
    assert summary.rows[0].project is None
    assert summary.kind_breakdown == {"conversation": 1}


def test_empty_window_yields_empty_summary_no_caveat_explosion():
    summary = token_economy_summary(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        transcripts=(), profiles=(),
    )
    assert summary.rows == ()
    assert summary.unique_conversation_count == 0
    assert summary.providers == {}


def test_render_includes_no_dollars_caveat():
    profiles = (_profile(conversation_id="c1", projects=("polylogue",), kind="implementation"),)
    transcripts = (_transcript(conversation_id="c1"),)
    summary = token_economy_summary(
        start=date(2026, 5, 7), end=date(2026, 5, 7),
        transcripts=transcripts, profiles=profiles,
    )
    rendered = render_token_economy_summary(summary)
    assert "polylogue" in rendered
    # The header row + at least one data row.
    assert "User tokens" in rendered
    assert "subscription quota" in rendered.lower() or "not dollars" in rendered.lower()


def test_window_filters_out_of_range_dates():
    profiles = (_profile(conversation_id="c1", projects=("p",), kind="planning"),)
    transcripts = (
        _transcript(conversation_id="c1", on_date=date(2026, 4, 1)),  # before window
    )
    summary = token_economy_summary(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        transcripts=transcripts, profiles=profiles,
    )
    assert summary.rows == ()
    assert summary.unique_conversation_count == 0
