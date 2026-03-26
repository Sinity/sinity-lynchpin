from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from lynchpin.core import config as core_config
from lynchpin.metrics import health as health_metrics
from lynchpin.sources.processed import (
    _activitywatch as activitywatch_helper,
    app_sessions as app_sessions_module,
    chat_activity,
    circadian as circadian_module,
    context_switches as context_switches_module,
    deep_work as deep_work_module,
    delivery_telemetry,
    focus_spans as focus_spans_module,
    focus_loops as focus_loops_module,
    git_commit_facts as git_commit_facts_module,
    project_attention,
    shell_sessions as shell_sessions_module,
    sleep_correlation,
)
from lynchpin.sources.processed import git_activity


def test_iter_chat_daily_uses_canonical_session_date_and_engaged_duration(monkeypatch) -> None:
    profile = SimpleNamespace(
        canonical_session_date=date(2026, 1, 2),
        provider="claude-code",
        message_count=3,
        word_count=40,
        engaged_duration_ms=3_500,
        wall_duration_ms=5_000,
        work_events=(SimpleNamespace(kind=SimpleNamespace(value="planning")),),
        canonical_projects=("polylogue",),
    )

    monkeypatch.setattr(chat_activity, "iter_session_profiles", lambda *, start, end: iter([profile]))

    rows = list(chat_activity.iter_chat_daily(start=date(2026, 1, 2), end=date(2026, 1, 2)))

    assert len(rows) == 1
    assert rows[0].date == date(2026, 1, 2)
    assert rows[0].engaged_minutes == 3_500 / 60_000.0
    assert rows[0].projects == ("polylogue",)


def test_window_spans_trim_to_active_time_and_split_midnight(monkeypatch) -> None:
    raw_signal = SimpleNamespace(
        source="activitywatch.window",
        app="kitty",
        start=datetime(2026, 1, 1, 23, 55),
        end=datetime(2026, 1, 2, 0, 5),
        title="sinex",
    )
    attributed = SimpleNamespace(
        app="kitty",
        title="sinex",
        start=raw_signal.start,
        end=raw_signal.end,
        mode="coding",
        project="sinex",
    )
    monkeypatch.setattr(activitywatch_helper, "_window_signals", lambda start, end: [raw_signal])
    monkeypatch.setattr(activitywatch_helper, "classify_signal", lambda signal: attributed)
    monkeypatch.setattr(
        activitywatch_helper,
        "load_active_intervals",
        lambda *, start, end: [(datetime(2026, 1, 1, 23, 58), datetime(2026, 1, 2, 0, 2))],
    )

    spans = list(
        activitywatch_helper.iter_attributed_window_spans(
            start=datetime(2026, 1, 1, 23, 50),
            end=datetime(2026, 1, 2, 0, 10),
        )
    )

    assert [(span.start, span.end) for span in spans] == [
        (datetime(2026, 1, 1, 23, 58), datetime(2026, 1, 2, 0, 0)),
        (datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 0, 2)),
    ]
    assert all(span.project == "sinex" for span in spans)


def test_iter_focus_spans_emits_afk_override_and_keylog_state(monkeypatch) -> None:
    monkeypatch.setattr(
        focus_spans_module,
        "load_active_intervals",
        lambda *, start, end: [(datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 10, 30))],
    )
    monkeypatch.setattr(
        focus_spans_module,
        "load_afk_intervals",
        lambda *, start, end: [(datetime(2026, 1, 1, 10, 10), datetime(2026, 1, 1, 10, 20))],
    )
    monkeypatch.setattr(
        focus_spans_module,
        "iter_attributed_window_spans",
        lambda *, start, end, min_duration_seconds=10.0: iter(
            [
                activitywatch_helper.WindowSpan(
                    start=datetime(2026, 1, 1, 10, 0),
                    end=datetime(2026, 1, 1, 10, 30),
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        focus_spans_module,
        "iter_key_press_samples",
        lambda *, start, end: iter(
            [
                (int(datetime(2026, 1, 1, 10, 5).timestamp() * 1_000_000), True),
                (int(datetime(2026, 1, 1, 10, 25).timestamp() * 1_000_000), False),
            ]
        ),
    )
    monkeypatch.setattr(
        focus_spans_module,
        "keylog_coverage_by_date",
        lambda *, start, end: {date(2026, 1, 1): True},
    )

    spans = list(
        focus_spans_module.iter_focus_spans(
            start=datetime(2026, 1, 1, 10, 0),
            end=datetime(2026, 1, 1, 10, 30),
        )
    )

    assert [(span.span_kind, span.start.hour, span.start.minute, span.end.hour, span.end.minute) for span in spans] == [
        ("focused", 10, 0, 10, 10),
        ("afk", 10, 10, 10, 20),
        ("focused", 10, 20, 10, 30),
    ]
    assert spans[0].keypress_count == 1
    assert spans[0].keylog_state == "keyboard_active"
    assert spans[1].keylog_state == "keyboard_silent"
    assert spans[2].keypress_count == 1


def test_iter_focus_spans_can_skip_keyboard_enrichment(monkeypatch) -> None:
    monkeypatch.setattr(
        focus_spans_module,
        "load_active_intervals",
        lambda *, start, end: [(datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 10, 10))],
    )
    monkeypatch.setattr(focus_spans_module, "load_afk_intervals", lambda *, start, end: [])
    monkeypatch.setattr(
        focus_spans_module,
        "iter_attributed_window_spans",
        lambda *, start, end, min_duration_seconds=10.0: iter(
            [
                activitywatch_helper.WindowSpan(
                    start=datetime(2026, 1, 1, 10, 0),
                    end=datetime(2026, 1, 1, 10, 10),
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        focus_spans_module,
        "iter_key_press_samples",
        lambda *, start, end: (_ for _ in ()).throw(AssertionError("keyboard events should not load")),
    )
    monkeypatch.setattr(
        focus_spans_module,
        "keylog_coverage_by_date",
        lambda *, start, end: (_ for _ in ()).throw(AssertionError("keylog coverage should not load")),
    )

    spans = list(
        focus_spans_module.iter_focus_spans(
            start=datetime(2026, 1, 1, 10, 0),
            end=datetime(2026, 1, 1, 10, 10),
            include_keyboard=False,
        )
    )

    assert len(spans) == 1
    assert spans[0].keylog_state == "not_requested"
    assert spans[0].keypress_count == 0


def test_iter_app_sessions_merges_brief_interruptions(monkeypatch) -> None:
    monkeypatch.setattr(
        app_sessions_module,
        "iter_focus_spans",
        lambda *, start, end, min_duration_seconds=10.0, include_keyboard=False: iter(
            [
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 0),
                    end=datetime(2026, 1, 1, 10, 10),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="unobserved",
                ),
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 10),
                    end=datetime(2026, 1, 1, 10, 10, 20),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="google-chrome",
                    title="search",
                    mode="research",
                    project=None,
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="unobserved",
                ),
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 10, 20),
                    end=datetime(2026, 1, 1, 10, 20),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="unobserved",
                ),
            ]
        ),
    )

    sessions = list(
        app_sessions_module.iter_app_sessions(
            start=datetime(2026, 1, 1, 9, 0),
            end=datetime(2026, 1, 1, 11, 0),
        )
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session.app == "kitty"
    assert session.interruptions == 1
    assert round(session.duration_seconds) == 1200
    assert session.project == "sinex"
    assert session.mode == "coding"


def test_iter_focus_loops_detects_alternating_window_pattern(monkeypatch) -> None:
    monkeypatch.setattr(
        focus_loops_module,
        "iter_focus_spans",
        lambda *, start, end, min_duration_seconds=60.0, include_keyboard=False: iter(
            [
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 0),
                    end=datetime(2026, 1, 1, 10, 4),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex/src/lib.rs",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                ),
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 4, 30),
                    end=datetime(2026, 1, 1, 10, 8),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="firefox",
                    title="docs.rs tokio",
                    mode="research",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                ),
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 8, 20),
                    end=datetime(2026, 1, 1, 10, 12),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex/src/lib.rs",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                ),
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 10, 12, 30),
                    end=datetime(2026, 1, 1, 10, 16),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="firefox",
                    title="docs.rs tokio",
                    mode="research",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                ),
            ]
        ),
    )

    loops = list(
        focus_loops_module.iter_focus_loops(
            start=datetime(2026, 1, 1, 9, 0),
            end=datetime(2026, 1, 1, 12, 0),
        )
    )

    assert len(loops) == 1
    loop = loops[0]
    assert loop.switch_count == 3
    assert loop.cycle_count == 2
    assert loop.context_a_app == "kitty"
    assert loop.context_b_app == "firefox"
    assert loop.dominant_project == "sinex"


def test_iter_delivery_telemetry_combines_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        delivery_telemetry,
        "active_seconds_by_date",
        lambda *, start, end: {date(2026, 2, 8): 14_400.0},
    )
    monkeypatch.setattr(
        git_activity,
        "iter_git_daily",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    date=date(2026, 2, 8),
                    repo="sinex",
                    commit_count=10,
                    ai_coauthored=6,
                    authors=("Claude Sonnet 4.6",),
                ),
                SimpleNamespace(
                    date=date(2026, 2, 8),
                    repo="polylogue",
                    commit_count=2,
                    ai_coauthored=1,
                    authors=("GPT-5.4",),
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        shell_sessions_module,
        "iter_shell_sessions",
        lambda *, start, end: iter(
            [
                SimpleNamespace(start=datetime(2026, 2, 8, 9, 0), command_count=30),
                SimpleNamespace(start=datetime(2026, 2, 8, 13, 0), command_count=10),
            ]
        ),
    )
    monkeypatch.setattr(
        chat_activity,
        "iter_chat_daily",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    date=date(2026, 2, 8),
                    session_count=2,
                    engaged_minutes=90.0,
                )
            ]
        ),
    )

    metrics = list(delivery_telemetry.iter_delivery_telemetry(start=date(2026, 2, 8), end=date(2026, 2, 8)))

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.active_hours == 4.0
    assert metric.total_commits == 12
    assert metric.ai_commits == 7
    assert metric.commit_density_per_active_hour == 3.0
    assert metric.command_count == 40
    assert metric.command_density_per_active_hour == 10.0
    assert metric.chat_minutes_per_active_hour == 22.5
    assert metric.repos == ("polylogue", "sinex")
    assert metric.ai_models_used == ("Claude Sonnet 4.6", "GPT-5.4")


def test_iter_circadian_uses_git_diff_intensity(monkeypatch) -> None:
    monkeypatch.setattr(
        circadian_module,
        "iter_focus_spans",
        lambda *, start, end, min_duration_seconds=30, include_keyboard=False: iter(
            [
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 1, 9, 0),
                    end=datetime(2026, 1, 1, 10, 0),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        circadian_module,
        "iter_commands",
        lambda *, start, end: iter([SimpleNamespace(timestamp=datetime(2026, 1, 1, 9, 15))]),
    )
    monkeypatch.setattr(
        circadian_module,
        "iter_git_commit_facts",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    authored_at=datetime(2026, 1, 1, 9, 30),
                    lines_changed=20,
                    files_changed=2,
                )
            ]
        ),
    )

    profiles = list(circadian_module.iter_circadian(start=date(2026, 1, 1), end=date(2026, 1, 1)))

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.hour == 9
    assert profile.git_lines_changed == 20
    assert profile.git_files_changed == 2
    assert profile.command_count == 1


def test_project_attention_uses_app_sessions(monkeypatch) -> None:
    monkeypatch.setattr(
        project_attention,
        "iter_focus_spans",
        lambda *, start, end, min_duration_seconds=60, include_keyboard=False: iter(
            [
                SimpleNamespace(
                    start=datetime(2026, 1, 1, 10, 0),
                    duration_seconds=7_200.0,
                    span_kind="focused",
                    project="sinex",
                ),
                SimpleNamespace(
                    start=datetime(2026, 1, 1, 12, 0),
                    duration_seconds=1_800.0,
                    span_kind="focused",
                    project="polylogue",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        project_attention,
        "active_seconds_by_date",
        lambda *, start, end: {date(2026, 1, 1): 10_800.0},
    )

    metrics = list(project_attention.iter_project_attention(start=date(2026, 1, 1), end=date(2026, 1, 1)))

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.top_project == "sinex"
    assert round(metric.top_project_share, 3) == 0.8
    assert metric.project_count == 2
    assert round(metric.rotation_speed, 3) == 0.667


def test_iter_deep_work_uses_git_diff_surface(monkeypatch) -> None:
    monkeypatch.setattr(
        deep_work_module,
        "iter_app_sessions",
        lambda *, start, end, min_duration_seconds=60: iter(
            [
                SimpleNamespace(
                    app="kitty",
                    start=datetime(2026, 1, 1, 9, 0),
                    end=datetime(2026, 1, 1, 10, 0),
                    duration_seconds=3_600.0,
                    mode="coding",
                    project="sinex",
                ),
                SimpleNamespace(
                    app="kitty",
                    start=datetime(2026, 1, 1, 10, 5),
                    end=datetime(2026, 1, 1, 11, 0),
                    duration_seconds=3_300.0,
                    mode="coding",
                    project="sinex",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        deep_work_module,
        "iter_shell_sessions",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    start=datetime(2026, 1, 1, 9, 30),
                    end=datetime(2026, 1, 1, 9, 45),
                    command_count=5,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        deep_work_module,
        "iter_git_commit_facts",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    authored_at=datetime(2026, 1, 1, 9, 40),
                    lines_changed=120,
                    files_changed=4,
                )
            ]
        ),
    )

    blocks = list(
        deep_work_module.iter_deep_work(
            start=datetime(2026, 1, 1, 9, 0),
            end=datetime(2026, 1, 1, 12, 0),
        )
    )

    assert len(blocks) == 1
    block = blocks[0]
    assert block.git_lines_changed == 120
    assert block.git_files_changed == 4
    assert block.command_count == 5


def test_iter_git_commit_facts_parses_paths_and_stats(monkeypatch, tmp_path) -> None:
    repo_path = tmp_path / "sinex"
    (repo_path / ".git").mkdir(parents=True)
    git_log = "\n".join(
        [
            "COMMIT|abc123|2026-02-11T10:00:00+01:00|Sinity|refactor: parser",
            "12\t5\tsrc/parser/core.py",
            "1\t0\ttests/parser/test_core.py",
            "COMMIT|def456|2026-02-11T12:00:00+01:00|Sinity|docs: notes",
            "-\t-\tdocs/notes.md",
            "",
        ]
    )

    class _FakeProc:
        def __init__(self, stdout_text: str) -> None:
            self.stdout = io.StringIO(stdout_text)

        def communicate(self):
            return ("", "")

    monkeypatch.setattr(
        git_commit_facts_module.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(git_log),
    )

    facts = list(
        git_commit_facts_module.iter_git_commit_facts(
            start=date(2026, 2, 11),
            end=date(2026, 2, 11),
            repos=[repo_path],
        )
    )

    assert len(facts) == 2
    assert facts[0].repo == "sinex"
    assert facts[0].lines_changed == 18
    assert facts[0].files_changed == 2
    assert facts[0].path_roots == ("parser",)
    assert facts[1].path_roots == ("docs",)
    assert facts[1].lines_changed == 0


def test_iter_git_file_change_facts_parses_path_level_stats(monkeypatch, tmp_path) -> None:
    repo_path = tmp_path / "sinex"
    (repo_path / ".git").mkdir(parents=True)
    git_log = "\n".join(
        [
            "COMMIT|abc123|2026-02-11T10:00:00+01:00|Sinity|refactor: parser",
            "12\t5\tsrc/parser/core.py",
            "1\t0\ttests/parser/test_core.py",
            "",
        ]
    )

    class _FakeProc:
        def __init__(self, stdout_text: str) -> None:
            self.stdout = io.StringIO(stdout_text)

        def communicate(self):
            return ("", "")

    monkeypatch.setattr(
        git_commit_facts_module.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProc(git_log),
    )

    facts = list(
        git_commit_facts_module.iter_git_file_change_facts(
            start=date(2026, 2, 11),
            end=date(2026, 2, 11),
            repos=[repo_path],
        )
    )

    assert len(facts) == 2
    assert facts[0].path == "src/parser/core.py"
    assert facts[0].path_root == "parser"
    assert facts[0].lines_changed == 17
    assert facts[1].path == "tests/parser/test_core.py"
    assert facts[1].path_root == "parser"
    assert facts[1].lines_changed == 1


def test_sleep_correlations_use_processed_surfaces(monkeypatch) -> None:
    sleep_day = date(2026, 1, 2)
    monkeypatch.setattr(
        sleep_correlation,
        "active_seconds_by_date",
        lambda *, start, end: {sleep_day: 6.5 * 3600},
    )
    monkeypatch.setattr(
        sleep_correlation,
        "iter_focus_spans",
        lambda *, start, end, min_duration_seconds=60, include_keyboard=False: iter(
            [
                focus_spans_module.FocusSpan(
                    start=datetime(2026, 1, 2, 9, 0),
                    end=datetime(2026, 1, 2, 11, 0),
                    span_kind="focused",
                    source_kind="activitywatch.window",
                    app="kitty",
                    title="sinex",
                    mode="coding",
                    project="sinex",
                    keypress_count=0,
                    changed_keypress_count=0,
                    keylog_state="not_requested",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        sleep_correlation,
        "iter_git_commit_facts",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    date=sleep_day,
                    lines_changed=18,
                    files_changed=2,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        sleep_correlation,
        "iter_deep_work",
        lambda *, start, end: iter(
            [
                SimpleNamespace(
                    start=datetime(2026, 1, 2, 9, 0),
                    duration_minutes=30.0,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        sleep_correlation,
        "iter_sleep",
        lambda: iter(
            [
                SimpleNamespace(
                    date=sleep_day.isoformat(),
                    total_minutes=480,
                    avg_score=82.0,
                    segments=[object(), object()],
                )
            ]
        ),
    )
    monkeypatch.setattr(
        health_metrics,
        "sleep_summary",
        lambda session: SimpleNamespace(quality_label="good"),
    )
    monkeypatch.setattr(
        core_config,
        "get_config",
        lambda: SimpleNamespace(exports_root=Path("/tmp")),
    )

    results = list(sleep_correlation.iter_sleep_correlations(start=sleep_day, end=sleep_day))

    assert len(results) == 1
    result = results[0]
    assert result.segment_count == 2
    assert result.workday_active_hours == 6.5
    assert result.workday_lines_changed == 18
    assert result.workday_files_changed == 2
    assert result.workday_dominant_mode == "coding"
    assert result.workday_deep_work_minutes == 30.0


def test_context_switch_metrics_include_alternation_summary(monkeypatch) -> None:
    test_day = date(2026, 1, 3)
    monkeypatch.setattr(
        context_switches_module,
        "iter_app_sessions",
        lambda *, start, end, min_duration_seconds=60: iter(
            [
                app_sessions_module.AppSession(
                    app="kitty",
                    start=datetime(2026, 1, 3, 9, 0),
                    end=datetime(2026, 1, 3, 9, 15),
                    duration_seconds=900,
                    title_dominant="sinex/src/lib.rs",
                    title_count=1,
                    titles=("sinex/src/lib.rs",),
                    mode="coding",
                    project="sinex",
                    interruptions=0,
                ),
                app_sessions_module.AppSession(
                    app="firefox",
                    start=datetime(2026, 1, 3, 9, 15),
                    end=datetime(2026, 1, 3, 9, 30),
                    duration_seconds=900,
                    title_dominant="docs.rs tokio",
                    title_count=1,
                    titles=("docs.rs tokio",),
                    mode="research",
                    project="sinex",
                    interruptions=0,
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        context_switches_module,
        "iter_focus_loops",
        lambda *, start, end: iter(
            [
                focus_loops_module.FocusLoop(
                    date=test_day,
                    start=datetime(2026, 1, 3, 9, 0),
                    end=datetime(2026, 1, 3, 9, 30),
                    duration_minutes=30.0,
                    span_count=4,
                    switch_count=3,
                    cycle_count=2,
                    context_a_app="kitty",
                    context_a_title="sinex/src/lib.rs",
                    context_b_app="firefox",
                    context_b_title="docs.rs tokio",
                    dominant_project="sinex",
                    dominant_mode="coding",
                )
            ]
        ),
    )

    metrics = list(
        context_switches_module.iter_context_switch_metrics(
            start=test_day,
            end=test_day,
        )
    )

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.alternation_loop_count == 1
    assert metric.alternation_switches == 3
    assert metric.alternation_minutes == 30.0
    assert metric.alternation_share == 1.0
