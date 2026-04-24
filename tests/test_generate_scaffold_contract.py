"""Focused tests for narrative scaffold rollup contracts."""

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.scripts.generate_scaffold import (
    DateSpan,
    _build_ai_activity_payload,
    _build_clipboard_payload,
    _build_commit_payload,
    _capture_days_with_data,
    _clip_dates,
    _summarize_ai,
    _summarize_health,
    _summarize_sleep,
    generate_hierarchy,
)


def test_summarize_health_keeps_expanded_recovery_fields():
    summary = _summarize_health([
        SimpleNamespace(
            steps=1000,
            stress_avg=20,
            heart_rate_avg=70,
            heart_rate_resting=55,
            hrv_rmssd_avg=40,
            spo2_avg=98,
            respiratory_avg=12,
            floors=3,
            skin_temp_avg=34.5,
            vitality_score=80,
            calories=2200,
            snoring_duration_s=120,
        )
    ])

    assert summary["avg_steps"] == 1000
    assert summary["avg_hrv_rmssd"] == 40
    assert summary["avg_respiratory_rate"] == 12
    assert summary["total_snoring_min"] == 2.0
    assert summary["days_with_signal"]["heart_rate_avg"] == 1


def test_summarize_sleep_includes_stage_architecture():
    summary = _summarize_sleep(
        [
            SimpleNamespace(
                bed_duration_min=540,
                sleep_duration_min=480,
                sleep_score=82,
                source="watch+aw",
            )
        ],
        [
            SimpleNamespace(
                awake_min=40,
                light_min=260,
                deep_min=90,
                rem_min=90,
                stage_transitions=18,
            )
        ],
    )

    assert summary["avg_bed_hours"] == 9.0
    assert summary["avg_sleep_hours"] == 8.0
    assert summary["avg_deep_min"] == 90
    assert summary["avg_rem_min"] == 90


def test_summarize_sleep_includes_inference_evidence():
    summary = _summarize_sleep([
        SimpleNamespace(
            bed_duration_min=480,
            sleep_duration_min=450,
            source="watch_only",
            confidence=0.4,
            evidence=("watch_sleep", "keypresses_during_watch_sleep"),
            keypress_count=120,
            aw_active_overlap_pct=80,
        )
    ])

    assert summary["avg_confidence"] == 0.4
    assert summary["low_confidence_records"] == 1
    assert summary["evidence"]["keypresses_during_watch_sleep"] == 1
    assert summary["sleep_window_keypresses"] == 120
    assert summary["avg_aw_active_overlap_pct"] == 80


def test_clip_dates_pads_requested_start_not_coverage_start():
    span = DateSpan(date(2020, 1, 1), date(2030, 1, 1), 1)
    assert _clip_dates(date(2026, 3, 18), date(2026, 3, 18), span, pad_start_days=1) == (
        date(2026, 3, 17),
        date(2026, 3, 18),
    )


def test_capture_days_with_data_uses_capture_sources(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.sources.clipboard.entries_in_range",
        lambda **_kw: [SimpleNamespace(date=date(2026, 4, 18))],
    )
    monkeypatch.setattr(
        "lynchpin.sources.irc.conversations_in_range",
        lambda **_kw: [SimpleNamespace(start=SimpleNamespace(date=lambda: date(2026, 4, 19)))],
    )
    monkeypatch.setattr(
        "lynchpin.sources.raw_log.entries_in_range",
        lambda **_kw: [SimpleNamespace(date=date(2026, 4, 20))],
    )

    coverage = {
        "clipboard": DateSpan(date(2026, 4, 18), date(2026, 4, 18), 1),
        "irc": DateSpan(date(2026, 4, 19), date(2026, 4, 19), 1),
        "raw_log": DateSpan(date(2026, 4, 20), date(2026, 4, 20), 1),
    }

    assert _capture_days_with_data(coverage, date(2026, 4, 1), date(2026, 4, 30)) == {
        "clipboard": {date(2026, 4, 18)},
        "irc": {date(2026, 4, 19)},
        "raw_log": {date(2026, 4, 20)},
    }


def test_summarize_ai_uses_repos_active_and_event_paths():
    summary = _summarize_ai(
        [
            SimpleNamespace(
                session_count=2,
                total_cost_usd=0.1,
                total_messages=20,
                total_words=200,
                providers={"codex": 2},
                repos_active=("sinity-lynchpin",),
                work_event_breakdown={"implementation": 1},
            )
        ],
        [
            SimpleNamespace(
                kind="debugging",
                file_paths=("/realm/project/polylogue/polylogue/facade.py",),
            )
        ],
    )

    assert summary["providers"] == {"codex": 2}
    assert summary["repos_active"]["sinity-lynchpin"] == 1
    assert summary["repos_active"]["polylogue"] == 1
    assert summary["work_event_breakdown"]["debugging"] == 1


def test_summarize_ai_marks_estimated_zero_cost_as_unknown():
    summary = _summarize_ai(
        [],
        [],
        sessions=[
            SimpleNamespace(
                provider="claude-code",
                message_count=20,
                word_count=200,
                total_cost_usd=0.0,
                cost_is_estimated=True,
                work_event_projects=("polylogue",),
                work_event_kind="implementation",
            )
        ],
        transcripts=[
            SimpleNamespace(
                user_prompt_tokens=12,
                dialogue_tokens=34,
                all_message_tokens=40,
                messages=(
                    SimpleNamespace(role="user"),
                    SimpleNamespace(role="assistant"),
                ),
            )
        ],
    )

    assert summary["cost"]["status"] == "estimated_zero"
    assert "total_cost_usd" not in summary
    assert summary["token_estimates"]["dialogue"] == 34


def test_build_ai_activity_payload_filters_protocol_noise_from_prompts():
    payload = _build_ai_activity_payload(
        poly_events=[],
        poly_summaries=[],
        sessions=[
            SimpleNamespace(
                conversation_id="conv-1",
                provider="claude-code",
                title="session",
                canonical_session_date="2026-04-21",
                first_message_at="2026-04-21T00:00:00+02:00",
                last_message_at="2026-04-21T00:30:00+02:00",
                message_count=4,
                substantive_count=1,
                attachment_count=0,
                work_event_count=0,
                phase_count=1,
                word_count=100,
                tool_use_count=0,
                thinking_count=0,
                work_event_kind=None,
                work_event_projects=(),
                auto_tags=(),
                total_cost_usd=0.0,
                cost_is_estimated=True,
            )
        ],
        transcripts=[
            SimpleNamespace(
                conversation_id="conv-1",
                provider="claude-code",
                title="session",
                first_message_at="2026-04-21T00:00:00+02:00",
                last_message_at="2026-04-21T00:30:00+02:00",
                user_prompt_tokens=15,
                dialogue_tokens=40,
                all_message_tokens=90,
                messages=(
                    SimpleNamespace(
                        ordinal=0,
                        role="user",
                        kind="control",
                        text="<command-name>/clear</command-name>",
                        approx_tokens=5,
                        has_tool_use=False,
                        has_thinking=False,
                    ),
                    SimpleNamespace(
                        ordinal=1,
                        role="user",
                        kind="caveat",
                        text="<local-command-caveat>...</local-command-caveat>",
                        approx_tokens=10,
                        has_tool_use=False,
                        has_thinking=False,
                    ),
                    SimpleNamespace(
                        ordinal=2,
                        role="user",
                        kind="prompt",
                        text="actual prompt",
                        approx_tokens=15,
                        has_tool_use=False,
                        has_thinking=False,
                    ),
                    SimpleNamespace(
                        ordinal=3,
                        role="assistant",
                        kind="assistant",
                        text="actual answer",
                        approx_tokens=25,
                        has_tool_use=False,
                        has_thinking=False,
                    ),
                ),
            )
        ],
    )

    assert payload["summary"]["message_kinds"]["prompt"] == 1
    assert payload["summary"]["message_kinds"]["control"] == 1
    assert payload["summary"]["message_kinds"]["caveat"] == 1
    assert payload["prompt_texts"][0]["text"] == "actual prompt"
    assert payload["user_prompts"][0]["prompt_count"] == 1
    assert payload["user_prompts"][0]["prompts"][0]["prompt_text_id"] == "pt0001"
    assert payload["dialogues"][0]["messages"][0]["prompt_id"] == "conv-1:u2"
    assert "text" not in payload["dialogues"][0]["messages"][0]
    assert payload["dialogues"][0]["messages"][1]["text"] == "actual answer"
    assert payload["sessions"][0]["cost_status"] == "estimated_zero"
    assert payload["sessions"][0]["recorded_cost_usd"] is None
    assert payload["sessions"][0]["estimated_cost_usd"] is None


def test_build_clipboard_payload_interns_repeated_values():
    payload = _build_clipboard_payload(
        [
            SimpleNamespace(
                recorded_at="2026-04-21T12:00:00+02:00",
                value="same text",
                source="/tmp/clipboard.json",
                file_path=None,
                pinned=False,
                kind="text",
            ),
            SimpleNamespace(
                recorded_at="2026-04-21T12:01:00+02:00",
                value="same text",
                source="/tmp/clipboard.json",
                file_path=None,
                pinned=False,
                kind="text",
            ),
        ]
    )

    assert payload["summary"]["entry_count"] == 2
    assert payload["summary"]["unique_value_count"] == 1
    assert payload["values"][0]["value"] == "same text"
    assert payload["entries"][0]["value_id"] == payload["entries"][1]["value_id"]
    assert "value" not in payload["entries"][0]


def test_build_commit_payload_extracts_subject_refs():
    payload = _build_commit_payload(
        facts=[
            SimpleNamespace(
                repo="polylogue",
                commit="abc1234",
                authored_at="2026-04-21T00:00:00+02:00",
                author="Sinity",
                subject="fix(cli): stabilize export (#42) closes #17",
                lines_added=10,
                lines_deleted=2,
                lines_changed=12,
                files_changed=2,
                paths=("polylogue/cli.py", "tests/test_cli.py"),
                path_roots=("polylogue", "tests"),
            )
        ],
        sessions=[],
        daily=[],
    )

    assert payload["facts"][0]["subject_prefix"] == "fix"
    assert payload["facts"][0]["refs"] == {"prs": [42], "issues": [17]}


def test_generate_day_batch_uses_preloaded_polylogue(monkeypatch, tmp_path):
    from lynchpin.scripts.generate_scaffold import generate_day

    d = date(2026, 4, 21)

    def fail(*_args, **_kwargs):
        raise AssertionError("per-day polylogue query should not run in batch mode")

    monkeypatch.setattr("lynchpin.sources.polylogue.session_profiles_for_date", fail)
    monkeypatch.setattr("lynchpin.sources.polylogue.conversation_transcripts", fail)
    monkeypatch.setattr("lynchpin.sources.activitywatch.focus_timeline", lambda **_kw: [])

    batch = SimpleNamespace(
        aw_active={},
        _frag_by_date={},
        _attn_by_date={},
        _circ_by_date={},
        _git_daily_by_date={},
        _dw_by_date={},
        _sf_by_date={},
        _focus_by_date={},
        _git_facts_by_date={},
        _git_sessions_by_date={},
        _poly_events_by_date={},
        _poly_summaries_by_date={},
        _shells_by_date={},
        _sleep_by_date={},
        _steps_by_date={},
        _health_by_date={},
        _hr_by_date={},
        _stress_by_date={},
        _browsing_by_date={},
        _messenger_by_date={},
        _raindrop_by_date={},
        _work_sessions_by_date={},
        _substance_by_date={},
        _sleep_stages_by_date={},
        _sleep_architecture_by_date={},
        _calories_by_date={},
        _naps_by_date={},
        _activity_summary_by_date={},
        _movement_by_date={},
        _ecg_by_date={},
        _segments_by_date={},
        _poly_sessions_by_date={
            d: [
                SimpleNamespace(
                    conversation_id="conv-1",
                    provider="claude-code",
                    title="session",
                    canonical_session_date=d.isoformat(),
                    first_message_at="2026-04-21T00:00:00+02:00",
                    last_message_at="2026-04-21T00:30:00+02:00",
                    message_count=1,
                    substantive_count=1,
                    attachment_count=0,
                    work_event_count=0,
                    phase_count=1,
                    word_count=10,
                    tool_use_count=0,
                    thinking_count=0,
                    work_event_kind=None,
                    work_event_projects=(),
                    auto_tags=(),
                    total_cost_usd=0.0,
                    cost_is_estimated=True,
                )
            ]
        },
        _poly_transcripts_by_date={
            d: [
                SimpleNamespace(
                    conversation_id="conv-1",
                    provider="claude-code",
                    title="session",
                    first_message_at="2026-04-21T00:00:00+02:00",
                    last_message_at="2026-04-21T00:30:00+02:00",
                    user_prompt_tokens=0,
                    dialogue_tokens=0,
                    all_message_tokens=0,
                    messages=(),
                )
            ]
        },
    )

    assert generate_day(d, tmp_path, force=True, all_features=[], batch=batch) is True

    ai_payload = json.loads(next(tmp_path.rglob("ai_activity.json")).read_text())
    assert ai_payload["summary"]["session_count"] == 1
    assert ai_payload["sessions"][0]["conversation_id"] == "conv-1"


def test_generate_hierarchy_dry_run_returns_success(tmp_path):
    assert generate_hierarchy(
        date(2026, 3, 20),
        date(2026, 3, 20),
        tmp_path,
        dry_run=True,
    ) is True
