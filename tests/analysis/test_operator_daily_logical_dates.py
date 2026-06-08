from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from lynchpin.analysis import operator_daily as od


def _ctx() -> od._FillContext:
    return od._FillContext(
        rows={
            date(2026, 6, 3): od.OperatorDay(date(2026, 6, 3)),
            date(2026, 6, 4): od.OperatorDay(date(2026, 6, 4)),
        },
        present={date(2026, 6, 3): set(), date(2026, 6, 4): set()},
        bounds={},
        start=date(2026, 6, 3),
        end=date(2026, 6, 4),
        source="test",
    )


def test_no_overlap_treats_requested_end_as_exclusive() -> None:
    data_start = date(2022, 1, 10)
    data_end = date(2022, 1, 20)

    assert od._no_overlap(date(2022, 1, 1), date(2022, 1, 10), data_start, data_end)
    assert not od._no_overlap(date(2022, 1, 1), date(2022, 1, 11), data_start, data_end)
    assert not od._no_overlap(date(2022, 1, 20), date(2022, 1, 21), data_start, data_end)
    assert od._no_overlap(date(2022, 1, 21), date(2022, 1, 22), data_start, data_end)


def test_fill_aw_reads_preconverged_activitywatch_derived() -> None:
    ctx = _ctx()
    ensure_calls = []
    calls = []
    daily = [
        SimpleNamespace(
            date=date(2026, 6, 3),
            active_hours=4.0,
            deep_work_min=90.0,
            fragmentation_score=0.25,
            project_count=2,
            dominant_mode="coding",
            dominant_project="lynchpin",
            outage_hours=0.5,
            presence_active_hours=4.5,
            presence_typing_hours=2.0,
            presence_data_gap_hours=0.25,
        ),
        SimpleNamespace(
            date=date(2026, 6, 5),
            active_hours=9.0,
            deep_work_min=300.0,
            fragmentation_score=0.1,
            project_count=1,
            dominant_mode="ignored",
            dominant_project="ignored",
            outage_hours=0.0,
            presence_active_hours=9.0,
            presence_typing_hours=9.0,
            presence_data_gap_hours=0.0,
        ),
    ]

    def fake_iter_derived_daily_activity(**kwargs):
        calls.append(kwargs)
        return iter(daily)

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch(
            "lynchpin.sources.activitywatch_derived.iter_derived_daily_activity",
            fake_iter_derived_daily_activity,
        ),
    ):
        od._fill_aw(ctx)

    assert ensure_calls == [("activitywatch_derived", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 4), "ensure": False}]
    assert ctx.rows[date(2026, 6, 3)].aw_active_hours == 4.0
    assert ctx.rows[date(2026, 6, 3)].aw_deep_work_min == 90.0
    assert ctx.rows[date(2026, 6, 3)].aw_fragmentation == 0.25
    assert ctx.rows[date(2026, 6, 3)].aw_dominant_project == "lynchpin"
    assert ctx.rows[date(2026, 6, 3)].aw_outage_hours == 0.5
    assert ctx.rows[date(2026, 6, 3)].aw_presence_typing_hours == 2.0
    assert date(2026, 6, 5) not in ctx.rows


def test_fill_svn_reads_requested_window() -> None:
    ctx = od._FillContext(
        rows={
            date(2022, 9, 21): od.OperatorDay(date(2022, 9, 21)),
            date(2022, 9, 22): od.OperatorDay(date(2022, 9, 22)),
        },
        present={date(2022, 9, 21): set(), date(2022, 9, 22): set()},
        bounds={},
        start=date(2022, 9, 21),
        end=date(2022, 9, 22),
        source="test",
    )
    calls = []
    daily = [
        SimpleNamespace(
            date=date(2022, 9, 21),
            commit_count=2,
            files_changed=7,
        ),
        SimpleNamespace(
            date=date(2022, 9, 23),
            commit_count=9,
            files_changed=99,
        ),
    ]

    def fake_daily_activity(**kwargs):
        calls.append(kwargs)
        return daily

    with patch("lynchpin.sources.svn.daily_activity", fake_daily_activity):
        od._fill_svn(ctx)

    assert calls == [{"start": date(2022, 9, 21), "end": date(2022, 9, 22)}]
    assert ctx.rows[date(2022, 9, 21)].svn_commits == 2
    assert ctx.rows[date(2022, 9, 21)].svn_files_changed == 7
    assert date(2022, 9, 23) not in ctx.rows


def test_fill_irc_converges_product_daily_rollup() -> None:
    ctx = _ctx()
    ensure_calls = []
    calls = []
    daily = [
        SimpleNamespace(date=date(2026, 6, 3), conversation_count=1, total_messages=7),
        SimpleNamespace(date=date(2026, 6, 5), conversation_count=9, total_messages=99),
    ]

    def fake_daily_irc_activity(**kwargs):
        calls.append(kwargs)
        return daily

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.sources.irc_raw.daily_irc_activity", fake_daily_irc_activity),
    ):
        od._fill_irc(ctx)

    assert ensure_calls == [("irc", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 4), "ensure": False}]
    assert ctx.rows[date(2026, 6, 3)].irc_conversations == 1
    assert ctx.rows[date(2026, 6, 3)].irc_lines == 7
    assert ctx.rows[date(2026, 6, 4)].irc_conversations == 0


def test_fill_samsung_binning_uses_logical_day_before_boundary() -> None:
    ctx = _ctx()
    stamp = datetime(2026, 6, 4, 3, 30, tzinfo=timezone.utc)
    end_day_stamp = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    exclusive_end_stamp = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    calls = []

    def fake_iter_stress_bins(*, start=None, end=None):
        calls.append(("stress", start, end))
        return [
            SimpleNamespace(ts=stamp),
            SimpleNamespace(ts=end_day_stamp),
            SimpleNamespace(ts=exclusive_end_stamp),
        ]

    def fake_iter_hrv_bins(*, start=None, end=None):
        calls.append(("hrv", start, end))
        return [
            SimpleNamespace(ts=stamp),
            SimpleNamespace(ts=end_day_stamp),
            SimpleNamespace(ts=exclusive_end_stamp),
        ]

    with (
        patch(
            "lynchpin.sources.samsung_binning.iter_stress_bins",
            fake_iter_stress_bins,
        ),
        patch(
            "lynchpin.sources.samsung_binning.iter_hrv_bins",
            fake_iter_hrv_bins,
        ),
    ):
        od._fill_samsung_binning(ctx)

    assert calls == [
        ("stress", datetime(2026, 6, 3, 6, 0), datetime(2026, 6, 5, 6, 0)),
        ("hrv", datetime(2026, 6, 3, 6, 0), datetime(2026, 6, 5, 6, 0)),
    ]
    assert ctx.rows[date(2026, 6, 3)].samsung_stress_bins == 1
    assert ctx.rows[date(2026, 6, 3)].samsung_hrv_bins == 1
    assert ctx.rows[date(2026, 6, 4)].samsung_stress_bins == 1
    assert ctx.rows[date(2026, 6, 4)].samsung_hrv_bins == 1


def test_fill_keylog_adds_keybind_usage_counts() -> None:
    ctx = _ctx()
    ensure_calls = []
    analysis = SimpleNamespace(
        keybind_usage=[
            SimpleNamespace(date=date(2026, 6, 3), chord="SUPER+KEY_ENTER", family="launch", count=2),
            SimpleNamespace(date=date(2026, 6, 3), chord="SUPER+KEY_H", family="navigation", count=1),
        ]
    )

    with (
        patch(
            "lynchpin.analysis.operator_daily._fill_keylog_daily",
            lambda inner_ctx: (
                setattr(inner_ctx.rows[date(2026, 6, 3)], "keylog_keypresses", 3),
                setattr(inner_ctx.rows[date(2026, 6, 3)], "keylog_sessions", 1),
                inner_ctx.mark(date(2026, 6, 3)),
            ),
        ),
        patch("lynchpin.core.io.load_json_if_exists", return_value=None),
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.analysis.keylog.analyze_keylog", return_value=analysis),
    ):
        od._fill_keylog(ctx)

    assert ensure_calls == [("keylog_analysis", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert ctx.rows[date(2026, 6, 3)].keylog_keypresses == 3
    assert ctx.rows[date(2026, 6, 3)].keylog_sessions == 1
    assert ctx.rows[date(2026, 6, 3)].keylog_keybind_uses == 3
    assert ctx.rows[date(2026, 6, 3)].keylog_unique_keybinds == 2
    assert ctx.rows[date(2026, 6, 3)].keylog_keybind_families == 2
    assert ctx.rows[date(2026, 6, 3)].keylog_top_keybind_family == "launch"
    assert ctx.rows[date(2026, 6, 3)].keylog_top_keybind_family_uses == 2


def test_fill_keylog_reuses_covering_keylog_analysis_artifact() -> None:
    ctx = _ctx()
    ensure_calls = []
    payload = {
        "start": "2026-06-01",
        "end": "2026-06-05",
        "keybind_usage": [
            {"date": "2026-06-03", "chord": "SUPER+KEY_ENTER", "family": "launch", "count": 2},
            {"date": "2026-06-03", "chord": "SUPER+KEY_H", "family": "navigation", "count": 1},
            {"date": "2026-06-04", "chord": "SUPER+KEY_ENTER", "family": "launch", "count": 4},
            {"date": "2026-06-05", "chord": "SUPER+KEY_X", "family": "ignored", "count": 9},
        ],
    }

    def fail_analysis(**_kwargs):
        raise AssertionError("covering keylog_analysis artifact should avoid live keylog scan")

    with (
        patch("lynchpin.analysis.operator_daily._fill_keylog_daily", lambda inner_ctx: None),
        patch("lynchpin.core.io.load_json_if_exists", return_value=payload),
        patch("lynchpin.core.io.resolve_analysis_path", lambda name: name),
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.analysis.keylog.analyze_keylog", fail_analysis),
    ):
        od._fill_keylog(ctx)

    assert ensure_calls == [("keylog_analysis", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert ctx.rows[date(2026, 6, 3)].keylog_keybind_uses == 3
    assert ctx.rows[date(2026, 6, 3)].keylog_unique_keybinds == 2
    assert ctx.rows[date(2026, 6, 3)].keylog_top_keybind_family == "launch"
    assert ctx.rows[date(2026, 6, 4)].keylog_keybind_uses == 4
    assert ctx.rows[date(2026, 6, 4)].keylog_top_keybind_family_uses == 4


def test_fill_keylog_daily_converges_personal_daily_signals_product() -> None:
    ctx = _ctx()
    ensure_calls = []
    rows = [
        SimpleNamespace(source="keylog", date=date(2026, 6, 3), metric="keypress_count", value=8.0),
        SimpleNamespace(source="keylog", date=date(2026, 6, 3), metric="session_count", value=2.0),
        SimpleNamespace(source="keylog", date=date(2026, 6, 4), metric="keypress_count", value=0.0),
        SimpleNamespace(source="keylog", date=date(2026, 6, 4), metric="session_count", value=0.0),
        SimpleNamespace(source="webhistory", date=date(2026, 6, 3), metric="visit_count", value=99.0),
        SimpleNamespace(source="keylog", date=date(2026, 6, 5), metric="keypress_count", value=99.0),
    ]
    calls = []

    def fake_iter_personal_daily_signals(**kwargs):
        calls.append(kwargs)
        return iter(rows)

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.sources.personal_signals.iter_personal_daily_signals", fake_iter_personal_daily_signals),
    ):
        od._fill_keylog_daily(ctx)

    assert ensure_calls == [("personal_daily_signals", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 5), "ensure": False}]
    assert ctx.rows[date(2026, 6, 3)].keylog_keypresses == 8
    assert ctx.rows[date(2026, 6, 3)].keylog_sessions == 2
    assert ctx.rows[date(2026, 6, 4)].keylog_keypresses == 0
    assert ctx.present[date(2026, 6, 3)] == {"test"}
    assert ctx.present[date(2026, 6, 4)] == {"test"}


def test_fill_web_converges_webhistory_product() -> None:
    ctx = _ctx()
    ensure_calls = []
    calls = []
    daily = [
        SimpleNamespace(date=date(2026, 6, 4), visit_count=42, unique_domains=7),
        SimpleNamespace(date=date(2026, 6, 5), visit_count=99, unique_domains=9),
    ]

    def fake_daily_browsing(**kwargs):
        calls.append(kwargs)
        return daily

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.sources.web.daily_browsing", fake_daily_browsing),
    ):
        od._fill_web(ctx)

    assert ensure_calls == [("webhistory", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 4), "ensure": False}]
    assert ctx.rows[date(2026, 6, 4)].web_visits == 42
    assert ctx.rows[date(2026, 6, 4)].web_unique_domains == 7
    assert date(2026, 6, 5) not in ctx.rows


def test_fill_web_category_reads_preconverged_webhistory() -> None:
    ctx = _ctx()
    calls = []

    def fake_daily_web_categories(**kwargs):
        calls.append(kwargs)
        return [
            SimpleNamespace(
                date=date(2026, 6, 3),
                nsfw_visit_share=0.25,
                distraction_ratio=0.5,
                minutes_by_category={"dev": 10.0},
            )
        ]

    with patch("lynchpin.analysis.web_category_daily.daily_web_categories", fake_daily_web_categories):
        od._fill_web_category(ctx)

    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 4), "ensure": False}]
    assert ctx.rows[date(2026, 6, 3)].web_nsfw_share == 0.25
    assert ctx.rows[date(2026, 6, 3)].web_distraction_ratio == 0.5
    assert ctx.rows[date(2026, 6, 3)].web_top_category == "dev"


def test_fill_spotify_queries_product_half_open_end_for_public_inclusive_window() -> None:
    ctx = _ctx()
    ensure_calls = []
    calls = []
    daily = [
        SimpleNamespace(date=date(2026, 6, 4), minutes_played=90.0),
        SimpleNamespace(date=date(2026, 6, 5), minutes_played=540.0),
    ]

    def fake_iter_spotify_daily_signals(**kwargs):
        calls.append(kwargs)
        return daily

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch(
            "lynchpin.sources.personal_signals.iter_spotify_daily_signals",
            fake_iter_spotify_daily_signals,
        ),
    ):
        od._fill_spotify(ctx)

    assert ensure_calls == [("spotify_daily", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 5), "ensure": False}]
    assert ctx.rows[date(2026, 6, 4)].spotify_hours == 1.5
    assert date(2026, 6, 5) not in ctx.rows


def test_fill_terminal_converges_atuin_product() -> None:
    ctx = _ctx()
    ensure_calls = []
    calls = []
    daily = [
        SimpleNamespace(date=date(2026, 6, 4), command_count=11),
        SimpleNamespace(date=date(2026, 6, 5), command_count=99),
    ]

    def fake_daily_terminal_activity(**kwargs):
        calls.append(kwargs)
        return daily

    with (
        patch(
            "lynchpin.materialization.ensure_materialized",
            lambda name, *, window: ensure_calls.append((name, window)),
        ),
        patch("lynchpin.sources.terminal.daily_terminal_activity", fake_daily_terminal_activity),
    ):
        od._fill_terminal(ctx)

    assert ensure_calls == [("atuin", (date(2026, 6, 3), date(2026, 6, 5)))]
    assert calls == [{"start": date(2026, 6, 3), "end": date(2026, 6, 4), "ensure": False}]
    assert ctx.rows[date(2026, 6, 4)].shell_commands == 11
    assert date(2026, 6, 5) not in ctx.rows


def test_operator_daily_skip_slow_still_runs_product_backed_daily_sources(monkeypatch) -> None:
    called: list[str] = []

    for name in (
        "_fill_aw",
        "_fill_git",
        "_fill_svn",
        "_fill_health",
        "_fill_sleep",
        "_fill_substance",
        "_fill_wykop",
        "_fill_reddit",
        "_fill_sms",
        "_fill_messenger",
        "_fill_outlook",
    ):
        monkeypatch.setattr(od, name, lambda ctx: None)

    monkeypatch.setattr(od, "_load_coverage_bounds", lambda: {})
    monkeypatch.setattr(od, "_fill_web", lambda ctx: called.append("web"))
    monkeypatch.setattr(od, "_fill_terminal", lambda ctx: called.append("terminal"))
    monkeypatch.setattr(od, "_fill_polylogue", lambda ctx: called.append("polylogue"))
    monkeypatch.setattr(od, "_fill_irc", lambda ctx: called.append("irc"))
    monkeypatch.setattr(od, "_fill_spotify", lambda ctx: called.append("spotify"))
    monkeypatch.setattr(od, "_fill_keylog_daily", lambda ctx: called.append("keylog_daily"))
    for name in (
        "_fill_keylog_keybinds",
        "_fill_clipboard",
        "_fill_raw_log",
        "_fill_samsung_binning",
    ):
        monkeypatch.setattr(
            od,
            name,
            lambda ctx, source=name: (_ for _ in ()).throw(
                AssertionError(f"{source} should stay behind skip_slow")
            ),
        )

    od.operator_daily_matrix(date(2026, 6, 3), date(2026, 6, 4), skip_slow=True)

    assert called == ["web", "terminal", "polylogue", "irc", "spotify", "keylog_daily"]
