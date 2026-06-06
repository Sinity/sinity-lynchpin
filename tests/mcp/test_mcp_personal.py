"""Tests for personal.py MCP tools."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.mcp.conftest import reload_config


def test_operator_rhythm_returns_rendered_summary(monkeypatch: pytest.MonkeyPatch):
    from lynchpin.mcp.tools.personal import operator_rhythm

    source_calls = []
    substrate_calls = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class _FocusRow:
        date = date(2026, 5, 25)
        hour = 14
        active_min = 30.0

    ts = datetime(2026, 5, 25, 14, tzinfo=UTC)

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"status": "ready"}

    def fake_ensure_materialized(name, *, window=None):
        source_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"status": "ready"}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.sources.activitywatch.circadian", lambda *_args, **_kwargs: [_FocusRow()])
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: _Conn())
    monkeypatch.setattr("lynchpin.mcp.tools.personal.best_materialized_refresh_id", lambda *_args, **_kwargs: "r1")
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_commit_timestamps_in_range",
        lambda *_args, **_kwargs: [ts],
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_ai_work_event_timestamps_in_range",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_ai_session_timestamps_in_range",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_pressure_timestamps_in_range",
        lambda *_args, **_kwargs: [],
    )

    result = operator_rhythm(start="2026-05-25", end="2026-05-25", project="sinex")

    assert result["summary"].startswith("Window 2026-05-25")
    assert "project sinex" in result["summary"]
    assert result["peak_focus_hour"] == [0, 14]
    assert source_calls == [("activitywatch", (date(2026, 5, 25), date(2026, 5, 26)))]
    assert substrate_calls == [("operator_rhythm", (date(2026, 5, 25), date(2026, 5, 26)))]


def test_spotify_daily_materializes_source_and_substrate_for_default_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_calls = []
    substrate_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"status": "ready"}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name, *, window=None):
        source_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"status": "ready"}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: _Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.require_best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.load_spotify_daily_rows",
        lambda *_args, **_kwargs: [
            (date(2026, 5, 1), 2, 7.5, 2, 2, ["a"], ["t"]),
        ],
    )

    from lynchpin.mcp.tools.personal import spotify_daily

    rows = spotify_daily(start="2026-05-01", end="2026-05-03")

    assert source_calls == [("spotify_daily", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert substrate_calls == [("spotify_daily", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert rows[0]["date"] == "2026-05-01"


def test_personal_daily_signals_materializes_source_and_substrate_for_default_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_calls = []
    substrate_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"status": "ready"}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name, *, window=None):
        source_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"status": "ready"}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: _Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.personal.require_best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.load_personal_daily_signals",
        lambda *_args, **_kwargs: [
            ("spotify", date(2026, 5, 1), "minutes_played", 7.5, {"artist": "a"}),
        ],
    )

    from lynchpin.mcp.tools.personal import personal_daily_signals

    rows = personal_daily_signals(start="2026-05-01", end="2026-05-03")

    assert source_calls == [
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 4)))
    ]
    assert substrate_calls == [
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 4)))
    ]
    assert rows == [
        {
            "source": "spotify",
            "date": "2026-05-01",
            "metric": "minutes_played",
            "value": 7.5,
            "dimensions": {"artist": "a"},
        }
    ]


def test_web_daily_buckets_visits_by_logical_day(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources.web_models import WebHistoryVisit

    ensure_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"status": "ready"}

    def fake_ensure_materialized(name, *, window=None):
        ensure_calls.append((name, window))
        return Result()

    visit = WebHistoryVisit(
        timestamp=datetime(2026, 1, 2, 1, tzinfo=UTC),
        url="https://example.com/a",
        title="A",
        source="fixture",
    )

    read_calls = []

    def fake_iter_all_visits(*, start, end, ensure=True):
        read_calls.append((start, end, ensure))
        return iter([visit])

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.sources.web._iter_all_visits", fake_iter_all_visits)

    from lynchpin.mcp.tools.personal import web_daily

    rows = web_daily(start="2026-01-01", end="2026-01-01")

    assert ensure_calls == [("webhistory", (date(2026, 1, 1), date(2026, 1, 2)))]
    assert read_calls == [(date(2026, 1, 1), date(2026, 1, 1), False)]
    assert rows == [
        {
            "date": "2026-01-01",
            "total_visits": 1,
            "unique_domains": 1,
            "top_5_domains": [{"domain": "example.com", "visits": 1, "share": 1.0}],
            "classification_basis": "weak_host_path",
            "weak_search_query_count": 0,
            "weak_github_visits": 0,
            "weak_docs_visits": 0,
            "weak_social_visits": 0,
            "weak_video_visits": 0,
        }
    ]


def test_analysis_artifact_status_materializes_before_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    root = tmp_path / "analysis"
    root.mkdir()
    (root / "workflow_mechanics.json").write_text('{"invocation_count": 1}', encoding="utf-8")
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(root))
    reload_config(monkeypatch)
    calls: list[str] = []

    def fake_ensure_materialized(name: str, *, cfg):
        calls.append(name)
        return type("Result", (), {"to_json": lambda self: {"name": name, "status": "ready"}})()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    from lynchpin.mcp.tools.personal import analysis_artifact_status

    rows = analysis_artifact_status()

    assert calls == ["analysis_artifacts"]
    assert [row["name"] for row in rows] == ["workflow_mechanics.json"]


def test_terminal_daily_materializes_atuin_for_requested_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "atuin", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    @dataclass
    class Row:
        date: date
        command_count: int

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    read_calls = []

    def fake_daily_terminal_activity(*, start, end, ensure=True):
        read_calls.append({"start": start, "end": end, "ensure": ensure})
        return [Row(date=start, command_count=3)]

    monkeypatch.setattr(
        "lynchpin.sources.terminal.daily_terminal_activity",
        fake_daily_terminal_activity,
    )

    from lynchpin.mcp.tools.personal import terminal_daily

    rows = terminal_daily(start="2026-05-01", end="2026-05-03")

    assert calls == [("atuin", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert read_calls == [{"start": date(2026, 5, 1), "end": date(2026, 5, 3), "ensure": False}]
    assert rows == [{"date": "2026-05-01", "command_count": 3}]


def test_keylog_daily_materializes_keylog_for_requested_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    @dataclass
    class Row:
        date: date
        event_count: int
        keypress_count: int
        changed_keypress_count: int
        session_count: int
        first_ts: datetime | None
        last_ts: datetime | None

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.keylog.daily_activity",
        lambda *, start, end: [
            Row(
                date=start,
                event_count=4,
                keypress_count=3,
                changed_keypress_count=2,
                session_count=1,
                first_ts=datetime(2026, 5, 1, 8, tzinfo=UTC),
                last_ts=datetime(2026, 5, 1, 9, tzinfo=UTC),
            )
        ],
    )

    from lynchpin.mcp.tools.personal import keylog_daily

    rows = keylog_daily(start="2026-05-01", end="2026-05-03")

    assert calls == [("keylog", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert rows == [
        {
            "date": "2026-05-01",
            "event_count": 4,
            "keypress_count": 3,
            "changed_keypress_count": 2,
            "session_count": 1,
            "first_ts": "2026-05-01T08:00:00+00:00",
            "last_ts": "2026-05-01T09:00:00+00:00",
        }
    ]


def test_keybind_usage_materializes_keylog_and_filters_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.analysis.keylog import (
        HyprlandKeybind,
        KeybindFamilySummary,
        KeybindSummary,
        KeybindTemporalBucket,
        KeybindUse,
        KeylogAnalysis,
    )

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    analysis = KeylogAnalysis(
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
        source_event_count=10,
        keypress_count=8,
        matched_keybind_count=3,
        keybinds=(
            HyprlandKeybind(
                chord="SUPER+KEY_RETURN",
                modifiers=("SUPER",),
                key="KEY_RETURN",
                dispatcher="exec",
                argument="kitty",
                family="launch",
                source="fixture",
            ),
        ),
        keybind_usage=(
            KeybindUse(
                date=date(2026, 5, 1),
                chord="SUPER+KEY_RETURN",
                dispatcher="exec",
                argument="kitty",
                family="launch",
                count=3,
                confidence="fixture",
            ),
        ),
        keybind_summaries=(
            KeybindSummary(
                chord="SUPER+KEY_RETURN",
                dispatcher="exec",
                argument="kitty",
                family="launch",
                total_count=3,
                active_days=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
            ),
        ),
        keybind_family_summaries=(
            KeybindFamilySummary(
                family="launch",
                total_count=3,
                unique_chords=1,
                active_days=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
            ),
        ),
        keybind_temporal_buckets=(
            KeybindTemporalBucket(
                chord="SUPER+KEY_RETURN",
                dispatcher="exec",
                argument="kitty",
                family="launch",
                weekday=4,
                hour=11,
                count=3,
            ),
        ),
        text_shape_days=(),
        caveats=("keybind and text-shape metadata are separate from text-content analysis",),
    )

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: None)
    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog", lambda **_kwargs: analysis)

    from lynchpin.mcp.tools.personal import keybind_usage

    result = keybind_usage(start="2026-05-01", end="2026-05-03", family="launch")

    assert calls == [("keylog_analysis", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert result["filters"] == {"family": "launch", "chord": None}
    assert result["usage"] == [
        {
            "date": "2026-05-01",
            "chord": "SUPER+KEY_RETURN",
            "dispatcher": "exec",
            "argument": "kitty",
            "family": "launch",
            "count": 3,
            "confidence": "fixture",
        }
    ]
    assert "raw_text" not in result
    assert "typed_text" not in result


def test_keybind_usage_reuses_exact_window_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    payload = {
        "start": "2026-05-01",
        "end": "2026-05-03",
        "source_event_count": 10,
        "keypress_count": 8,
        "matched_keybind_count": 3,
        "keybinds": [{"chord": "SUPER+KEY_RETURN"}],
        "keybind_usage": [
            {
                "date": "2026-05-01",
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "count": 3,
                "confidence": "artifact",
            }
        ],
        "keybind_summaries": [
            {
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "total_count": 3,
                "active_days": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-01",
            }
        ],
        "keybind_family_summaries": [
            {
                "family": "launch",
                "total_count": 3,
                "unique_chords": 1,
                "active_days": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-01",
            }
        ],
        "keybind_temporal_buckets": [
            {
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "weekday": 4,
                "hour": 11,
                "count": 3,
            }
        ],
        "caveats": ["artifact caveat"],
    }

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: payload)
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: name)

    def fail_analysis(**_kwargs):
        raise AssertionError("artifact-covered exact request should not rescan keylog")

    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog", fail_analysis)

    from lynchpin.mcp.tools.personal import keybind_usage

    result = keybind_usage(start="2026-05-01", end="2026-05-03", family="launch")

    assert calls == [("keylog_analysis", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert result["source"] == "artifact"
    assert result["usage"][0]["confidence"] == "artifact"
    assert result["keybind_temporal_buckets"][0]["hour"] == 11


def test_keybind_usage_reuses_covering_artifact_with_window_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    payload = {
        "start": "2026-05-01",
        "end": "2026-05-10",
        "source_event_count": 1000,
        "keypress_count": 1000,
        "matched_keybind_count": 500,
        "keybinds": [
            {
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
            }
        ],
        "keybind_usage": [
            {
                "date": "2026-05-01",
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "count": 200,
                "confidence": "artifact",
            },
            {
                "date": "2026-05-03",
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "count": 3,
                "confidence": "artifact",
            },
            {
                "date": "2026-05-04",
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "count": 4,
                "confidence": "artifact",
            },
            {
                "date": "2026-05-10",
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "count": 300,
                "confidence": "artifact",
            },
        ],
        "keybind_temporal_buckets": [
            {
                "chord": "SUPER+KEY_RETURN",
                "dispatcher": "exec",
                "argument": "kitty",
                "family": "launch",
                "weekday": 4,
                "hour": 11,
                "count": 500,
            }
        ],
        "text_shape_days": [
            {"date": "2026-05-01", "keypress_count": 300},
            {"date": "2026-05-03", "keypress_count": 7},
            {"date": "2026-05-04", "keypress_count": 9},
            {"date": "2026-05-10", "keypress_count": 400},
        ],
        "caveats": ["artifact caveat"],
    }

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: payload)
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: name)

    def fail_analysis(**_kwargs):
        raise AssertionError("covering artifact should answer keybind usage without raw scan")

    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog", fail_analysis)

    from lynchpin.mcp.tools.personal import keybind_usage

    result = keybind_usage(start="2026-05-03", end="2026-05-04", family="launch", limit=1)

    assert calls == [("keylog_analysis", (date(2026, 5, 3), date(2026, 5, 5)))]
    assert result["source"] == "artifact"
    assert result["start"] == "2026-05-03"
    assert result["end"] == "2026-05-04"
    assert result["keypress_count"] == 16
    assert result["source_event_count"] == 16
    assert result["matched_keybind_count"] == 7
    assert [row["date"] for row in result["usage"]] == ["2026-05-03"]
    assert result["keybind_summaries"] == [
        {
            "chord": "SUPER+KEY_RETURN",
            "dispatcher": "exec",
            "argument": "kitty",
            "family": "launch",
            "total_count": 7,
            "active_days": 2,
            "first_date": "2026-05-03",
            "last_date": "2026-05-04",
        }
    ]
    assert result["keybind_family_summaries"][0]["total_count"] == 7
    assert result["keybind_temporal_buckets"] == []
    assert "temporal buckets omitted" in result["caveats"][-1]


def test_keylog_text_shape_returns_shape_counts_without_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.analysis.keylog import KeylogAnalysis, KeylogTextShapeDay

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    analysis = KeylogAnalysis(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        source_event_count=6,
        keypress_count=5,
        matched_keybind_count=0,
        keybinds=(),
        keybind_usage=(),
        keybind_summaries=(),
        keybind_family_summaries=(),
        keybind_temporal_buckets=(),
        text_shape_days=(
            KeylogTextShapeDay(
                date=date(2026, 5, 1),
                keypress_count=5,
                changed_keypress_count=4,
                commandish_keypress_count=1,
                backspace_count=1,
                enter_count=1,
                tab_count=0,
                space_count=2,
            ),
        ),
        caveats=("keybind and text-shape metadata are separate from text-content analysis",),
    )

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: None)
    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog", lambda **_kwargs: analysis)

    from lynchpin.mcp.tools.personal import keylog_text_shape

    result = keylog_text_shape(start="2026-05-01", end="2026-05-01")

    assert calls == [("keylog_analysis", (date(2026, 5, 1), date(2026, 5, 2)))]
    assert result["changed_keypress_count"] == 4
    assert result["commandish_keypress_count"] == 1
    assert result["days"][0]["space_count"] == 2
    assert "raw_text" not in result
    assert "typed_text" not in result


def test_keylog_text_shape_reuses_covering_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    payload = {
        "start": "2026-05-01",
        "end": "2026-05-03",
        "text_shape_days": [
            {
                "date": "2026-05-01",
                "keypress_count": 5,
                "changed_keypress_count": 4,
                "commandish_keypress_count": 1,
                "backspace_count": 1,
                "enter_count": 0,
                "tab_count": 0,
                "space_count": 2,
            },
            {
                "date": "2026-05-02",
                "keypress_count": 7,
                "changed_keypress_count": 6,
                "commandish_keypress_count": 1,
                "backspace_count": 0,
                "enter_count": 1,
                "tab_count": 0,
                "space_count": 3,
            },
        ],
        "caveats": ["artifact caveat"],
    }

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: payload)
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: name)

    def fail_analysis(**_kwargs):
        raise AssertionError("artifact-covered text-shape request should not rescan keylog")

    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog", fail_analysis)

    from lynchpin.mcp.tools.personal import keylog_text_shape

    result = keylog_text_shape(start="2026-05-02", end="2026-05-02")

    assert calls == [("keylog_analysis", (date(2026, 5, 2), date(2026, 5, 3)))]
    assert result["source"] == "artifact"
    assert result["keypress_count"] == 7
    assert [row["date"] for row in result["days"]] == ["2026-05-02"]


def test_keylog_text_content_materializes_keylog_and_returns_content_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.analysis.keylog import (
        KeylogTextContentAnalysis,
        KeylogTextContentDay,
        KeylogTextTerm,
    )

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    analysis = KeylogTextContentAnalysis(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        snapshot_count=2,
        char_count=42,
        word_count=6,
        line_count=2,
        days=(
            KeylogTextContentDay(
                date=date(2026, 5, 1),
                snapshot_count=2,
                char_count=42,
                word_count=6,
                line_count=2,
            ),
        ),
        top_terms=(KeylogTextTerm(term="lynchpin", count=2),),
        caveats=("text-content analysis only uses explicit snapshot text fields",),
    )

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog_text_content", lambda **_kwargs: analysis)

    from lynchpin.mcp.tools.personal import keylog_text_content

    result = keylog_text_content(start="2026-05-01", end="2026-05-01", limit=10)

    assert calls == [("keylog_analysis", (date(2026, 5, 1), date(2026, 5, 2)))]
    assert result["snapshot_count"] == 2
    assert result["top_terms"] == [{"term": "lynchpin", "count": 2}]
    assert result["days"][0]["date"] == "2026-05-01"


def test_keylog_text_content_reuses_exact_window_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    payload = {
        "start": "2026-05-01",
        "end": "2026-05-01",
        "text_content": {
            "start": "2026-05-01",
            "end": "2026-05-01",
            "snapshot_count": 3,
            "char_count": 120,
            "word_count": 18,
            "line_count": 4,
            "days": [
                {
                    "date": "2026-05-01",
                    "snapshot_count": 3,
                    "char_count": 120,
                    "word_count": 18,
                    "line_count": 4,
                }
            ],
            "top_terms": [
                {"term": "lynchpin", "count": 4},
                {"term": "materialized", "count": 2},
            ],
            "caveats": ["text-content analysis only uses explicit snapshot text fields"],
        },
    }

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "keylog", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    def fail_analysis(**_kwargs):
        raise AssertionError("exact-window artifact should answer text-content reads")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr("lynchpin.core.io.load_json_if_exists", lambda _path: payload)
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: name)
    monkeypatch.setattr("lynchpin.analysis.keylog.analyze_keylog_text_content", fail_analysis)

    from lynchpin.mcp.tools.personal import keylog_text_content

    result = keylog_text_content(start="2026-05-01", end="2026-05-01", limit=1)

    assert calls == [("keylog_analysis", (date(2026, 5, 1), date(2026, 5, 2)))]
    assert result["source"] == "artifact"
    assert result["snapshot_count"] == 3
    assert result["top_terms"] == [{"term": "lynchpin", "count": 4}]


def test_activity_content_daily_materializes_content_and_title_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_json(self) -> dict[str, object]:
            return {"name": self.name, "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result(name)

    @dataclass
    class Row:
        date: date
        focused_seconds: float

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    def fake_iter_activity_content_days(*, start=None, end=None, ensure=True):
        rows = [
            Row(date=date(2026, 5, 1), focused_seconds=12.0),
            Row(date=date(2026, 5, 4), focused_seconds=99.0),
        ]
        return iter(
            row
            for row in rows
            if (start is None or row.date >= start)
            and (end is None or row.date < end)
        )

    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        fake_iter_activity_content_days,
    )

    from lynchpin.mcp.tools.personal import activity_content_daily

    rows = activity_content_daily(start="2026-05-01", end="2026-05-03")

    assert calls == [
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 4))),
        ("title_metadata", None),
    ]
    assert rows == [{"date": "2026-05-01", "focused_seconds": 12.0}]


def test_google_takeout_events_include_end_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import dataclass
    from datetime import datetime

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "google_takeout", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    @dataclass
    class Row:
        timestamp: datetime
        product: str
        service: str | None
        title: str
        source_member: str

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.google_takeout_products.iter_events",
        lambda *, product=None, ensure=True: iter([
            Row(
                timestamp=datetime(2026, 5, 3, 12, 0),
                product="chrome",
                service=None,
                title="Boundary event",
                source_member="fixture",
            )
        ]),
    )

    from lynchpin.mcp.tools.personal import google_takeout_events

    rows = google_takeout_events(start="2026-05-03", end="2026-05-03")

    assert calls == [("google_takeout", (date(2026, 5, 3), date(2026, 5, 4)))]
    assert len(rows) == 1
    assert rows[0]["title"] == "Boundary event"


def test_communication_events_include_end_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import dataclass
    from datetime import datetime

    calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "communications", "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result()

    @dataclass
    class Row:
        timestamp: datetime
        source: str
        thread: str

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    read_calls = []

    def fake_iter_communication_events(*, ensure=True):
        read_calls.append(ensure)
        return iter([
            Row(timestamp=datetime(2026, 5, 3, 9, 0), source="messenger", thread="fixture")
        ])

    monkeypatch.setattr(
        "lynchpin.sources.communications.iter_communication_events",
        fake_iter_communication_events,
    )

    from lynchpin.mcp.tools.personal import communication_events

    rows = communication_events(start="2026-05-03", end="2026-05-03")

    assert calls == [("communications", (date(2026, 5, 3), date(2026, 5, 4)))]
    assert read_calls == [False]
    assert len(rows) == 1
    assert rows[0]["thread"] == "fixture"


def test_daily_personal_source_preconditions_use_half_open_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_json(self) -> dict[str, object]:
            return {"name": self.name, "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result(name)

    @dataclass
    class DailyRow:
        date: date
        count: int = 1

    @dataclass
    class TakeoutRow:
        date: date
        product: str = "chrome"
        event_count: int = 1

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.google_takeout_products.iter_daily_activity",
        lambda *, start=None, end=None, ensure=True: iter([TakeoutRow(date=start)]),
    )
    read_calls = []

    def fake_daily_bookmark_activity(*, start, end, ensure=True):
        read_calls.append(("browser_bookmarks", ensure))
        return [DailyRow(date=start)]

    def fake_daily_communication_activity(*, start, end, ensure=True):
        read_calls.append(("communications", ensure))
        return [DailyRow(date=start)]

    monkeypatch.setattr(
        "lynchpin.sources.bookmarks.daily_bookmark_activity",
        fake_daily_bookmark_activity,
    )
    monkeypatch.setattr(
        "lynchpin.sources.communications.daily_communication_activity",
        fake_daily_communication_activity,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.daily_activity",
        lambda *, start, end: [DailyRow(date=start)],
    )
    def fake_daily_arbtt_activity(*, start, end, ensure=True):
        read_calls.append(("arbtt", ensure))
        return [DailyRow(date=start)]

    monkeypatch.setattr(
        "lynchpin.sources.arbtt.daily_arbtt_activity",
        fake_daily_arbtt_activity,
    )

    from lynchpin.mcp.tools.personal import (
        arbtt_focus_daily,
        bookmark_daily,
        communication_daily,
        focus_daily,
        google_takeout_daily,
    )

    google_takeout_daily(start="2026-05-01", end="2026-05-03")
    bookmark_daily(start="2026-05-01", end="2026-05-03")
    communication_daily(start="2026-05-01", end="2026-05-03")
    focus_daily(start="2026-05-01", end="2026-05-03")
    arbtt_focus_daily(start="2026-05-01", end="2026-05-03")

    assert calls == [
        ("google_takeout", (date(2026, 5, 1), date(2026, 5, 4))),
        ("browser_bookmarks", (date(2026, 5, 1), date(2026, 5, 4))),
        ("communications", (date(2026, 5, 1), date(2026, 5, 4))),
        ("activitywatch", (date(2026, 5, 1), date(2026, 5, 4))),
        ("arbtt", (date(2026, 5, 1), date(2026, 5, 4))),
        ("arbtt", (date(2026, 5, 1), date(2026, 5, 4))),
    ]
    assert read_calls == [
        ("browser_bookmarks", False),
        ("communications", False),
        ("arbtt", False),
        ("arbtt", False),
    ]


def test_activity_content_tools_include_end_date(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_json(self) -> dict[str, object]:
            return {"name": self.name, "status": "ready", "changed": False}

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))
        return Result(name)

    @dataclass
    class DayRow:
        date: date
        focused_seconds: float
        matched_seconds: float = 10.0
        gpt_matched_seconds: float = 5.0

    @dataclass
    class TitleRow:
        title: str
        first_date: date
        last_date: date
        matched: bool
        focused_seconds: float

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda **_kwargs: iter([
            DayRow(date=date(2026, 5, 3), focused_seconds=20.0),
        ]),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_title_usage",
        lambda **_kwargs: iter([
            TitleRow(
                title="Boundary title",
                first_date=date(2026, 5, 3),
                last_date=date(2026, 5, 3),
                matched=False,
                focused_seconds=20.0,
            )
        ]),
    )

    from lynchpin.mcp.tools.personal import (
        activity_content_coverage,
        activity_content_daily,
        activity_title_usage,
    )

    daily = activity_content_daily(start="2026-05-03", end="2026-05-03")
    titles = activity_title_usage(start="2026-05-03", end="2026-05-03")
    coverage = activity_content_coverage(start="2026-05-03", end="2026-05-03")

    assert daily[0]["date"] == "2026-05-03"
    assert titles[0]["title"] == "Boundary title"
    assert coverage["days"] == 1
    assert ("activity_content", (date(2026, 5, 3), date(2026, 5, 4))) in calls


class TestActivitySemanticDaily:
    """Tests for activity_semantic_daily tool."""

    def test_activity_semantic_daily_shape(self):
        """Verify activity_semantic_daily returns correct shape for 3 days."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        # Mock the connection
        mock_rows = [
            (date(2026, 5, 20), "work", 7200.0),      # 120 min
            (date(2026, 5, 20), "social", 1800.0),    # 30 min
            (date(2026, 5, 21), "work", 10800.0),     # 180 min
            (date(2026, 5, 21), "health", 3600.0),    # 60 min
            (date(2026, 5, 22), "learning", 5400.0),  # 90 min
        ]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = mock_rows

            result = activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="topic_category",
            )

        # Verify shape: list of dicts with expected keys
        assert isinstance(result, list)
        assert len(result) == 5
        for row in result:
            assert set(row.keys()) == {
                "date",
                "dimension_value",
                "focused_seconds",
                "focused_minutes",
            }

        # Verify calculations: 7200 seconds = 120 minutes
        assert result[0]["focused_seconds"] == 7200.0
        assert result[0]["focused_minutes"] == 120.0
        assert result[0]["dimension_value"] == "work"
        assert result[0]["date"] == "2026-05-20"

    def test_activity_semantic_daily_invalid_dimension(self):
        """Verify invalid dimension raises ValueError."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        with pytest.raises(ValueError, match="dimension must be one of"):
            activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="invalid_dim",
            )

    def test_activity_semantic_daily_valid_dimensions(self):
        """Verify all valid dimensions are accepted."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        valid_dims = ["topic_category", "attention_level", "activity", "platform", "mode"]
        mock_rows = [(date(2026, 5, 20), "test", 3600.0)]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = mock_rows

            for dim in valid_dims:
                result = activity_semantic_daily(
                    start="2026-05-20",
                    end="2026-05-22",
                    dimension=dim,
                )
                assert isinstance(result, list)

    def test_activity_semantic_daily_empty_result(self):
        """Verify empty results are handled correctly."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = []

            result = activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="topic_category",
            )

        assert result == []
