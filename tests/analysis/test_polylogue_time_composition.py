from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from typer.testing import CliRunner

from lynchpin.analysis.cli import build_app


def test_build_polylogue_time_composition_summarizes_rows(monkeypatch):
    import lynchpin.analysis.ecosystem.polylogue_time_composition as mod
    from lynchpin.sources.polylogue_timeline_models import PolylogueSessionComposition

    start_dt = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        mod,
        "archive_readiness",
        lambda: SimpleNamespace(
            status="ready",
            reason="ok",
            db_path="/tmp/polylogue.db",
            session_profile_count=1,
            work_event_count=1,
        ),
    )
    monkeypatch.setattr(
        mod,
        "session_compositions",
        lambda **kwargs: [
            PolylogueSessionComposition(
                session_id="s1",
                provider="codex",
                title="timeline",
                start=start_dt,
                end=start_dt + timedelta(seconds=5),
                status="ok",
                reason=None,
                message_count=2,
                wall_seconds=5.0,
                engaged_seconds=4.0,
                span_count=2,
                overlap_count=1,
                seconds_by_lane={"message_gap": 5.0},
                seconds_by_kind={"assistant_response_wait": 5.0},
                cross_source_seconds={"activitywatch.focus_timeline": 3.0},
            )
        ],
    )

    payload = mod.build_polylogue_time_composition(
        start=date(2026, 6, 1),
        end=date(2026, 6, 6),
    )

    assert payload["summary"]["session_count"] == 1
    assert payload["summary"]["seconds_by_lane"]["message_gap"] == 5.0
    assert payload["sessions"][0]["session_id"] == "s1"


def test_run_polylogue_time_composition_writes_json(monkeypatch, tmp_path):
    import lynchpin.analysis.ecosystem.polylogue_time_composition as mod

    payload = {
        "kind": "polylogue_time_composition",
        "summary": {"session_count": 0},
        "sessions": [],
    }
    monkeypatch.setattr(mod, "build_polylogue_time_composition", lambda **_kwargs: payload)

    out = tmp_path / "polylogue-time-composition.json"
    result = mod.run_polylogue_time_composition(
        out,
        start=date(2026, 6, 1),
        end=date(2026, 6, 6),
    )

    assert result == payload
    assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_polylogue_time_composition_command_is_registered():
    result = CliRunner().invoke(build_app(), ["--help"])

    assert result.exit_code == 0
    assert "polylogue-time-composition" in result.output
