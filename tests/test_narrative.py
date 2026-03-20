"""Tests for retrospective narrative prompt builders.

Covers: build_day_prompt, build_week_prompt, build_episode_prompt,
build_quarter_prompt, build_contrast_prompt, build_month_prompt, _log_narrative.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lynchpin.retrospective.narrative import (
    Narrative,
    NarrativeBackend,
    NarrativeKind,
    _enrich_prompt_for_sdk,
    _generate_via_codex_exec,
    generate_date_range_narrative,
    generate_narrative,
    _log_narrative,
    _resolve_backend,
    build_contrast_prompt,
    build_day_prompt,
    build_episode_prompt,
    build_month_prompt,
    build_quarter_prompt,
    build_week_prompt,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_day(**kwargs):
    defaults = dict(
        date=date(2026, 3, 10),
        active_seconds=14400.0,   # 4h
        recovery_seconds=3600.0,  # 1h
        chain_count=5,
        signal_count=80,
        command_count=30,
        transcript_count=4,
        commit_count=3,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic="rust",
        top_modes=[("coding", 10000.0), ("review", 4000.0)],
        top_projects=[("sinex", 10000.0), ("lynchpin", 4000.0)],
        top_topics=[("rust", 9000.0), ("nix", 3000.0)],
        projects=[],
        signal_coverage=None,
        highlights=["Implemented batch ingest", "Fixed replay logic"],
        anomalies=[],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_week(**kwargs):
    defaults = dict(
        iso_week="2026-W11",
        start_date=date(2026, 3, 9),
        end_date=date(2026, 3, 15),
        days=7,
        active_seconds=50400.0,   # 14h
        recovery_seconds=7200.0,
        chain_count=20,
        signal_count=300,
        command_count=120,
        transcript_count=10,
        commit_count=12,
        top_modes=(("coding", 36000.0), ("review", 14400.0)),
        top_projects=(("sinex", 25000.0), ("lynchpin", 11000.0)),
        top_topics=(("rust", 20000.0), ("nix", 8000.0)),
        day_pattern="uniform",
        busiest_day=date(2026, 3, 11),
        quietest_day=date(2026, 3, 15),
        active_delta_vs_prior=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_episode(**kwargs):
    defaults = dict(
        label="sinex-sprint",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 10),
        days=10,
        active_seconds=72000.0,  # 20h
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic="rust",
        mode_distribution={"coding": 50000.0, "review": 22000.0},
        project_distribution={"sinex": 60000.0, "lynchpin": 12000.0},
        trigger="project_shift",
        confidence=0.85,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_quarter(**kwargs):
    defaults = dict(
        quarter="2026-Q1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
        total_days=90,
        active_days=60,
        active_seconds=432000.0,  # 120h
        recovery_seconds=72000.0,
        chain_count=200,
        signal_count=3000,
        command_count=1000,
        transcript_count=100,
        commit_count=120,
        top_modes=(("coding", 300000.0),),
        top_projects=(("sinex", 200000.0),),
        top_topics=(("rust", 180000.0),),
        chat_session_count=50,
        chat_cost_usd=12.5,
        episode_count=3,
        month_active_trend=(144000.0, 158400.0, 129600.0),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# build_day_prompt
# ---------------------------------------------------------------------------

class TestBuildDayPrompt:
    def test_contains_date(self):
        day = _make_day()
        prompt = build_day_prompt(day)
        assert "2026-03-10" in prompt

    def test_contains_active_hours(self):
        day = _make_day(active_seconds=18000.0)
        prompt = build_day_prompt(day)
        assert "5.0h" in prompt

    def test_contains_dominant_fields(self):
        day = _make_day()
        prompt = build_day_prompt(day)
        assert "coding" in prompt
        assert "sinex" in prompt
        assert "rust" in prompt

    def test_contains_commit_count(self):
        day = _make_day(commit_count=7)
        prompt = build_day_prompt(day)
        assert "7" in prompt

    def test_highlights_included(self):
        day = _make_day(highlights=["Shipped parser", "Fixed replay"])
        prompt = build_day_prompt(day)
        assert "Shipped parser" in prompt

    def test_anomalies_none_shows_none(self):
        day = _make_day(anomalies=[])
        prompt = build_day_prompt(day)
        assert "none" in prompt.lower()

    def test_anomalies_listed(self):
        day = _make_day(anomalies=["high_entropy_day"])
        prompt = build_day_prompt(day)
        assert "high_entropy_day" in prompt


# ---------------------------------------------------------------------------
# build_week_prompt
# ---------------------------------------------------------------------------

class TestBuildWeekPrompt:
    def test_contains_iso_week(self):
        week = _make_week()
        prompt = build_week_prompt(week)
        assert "2026-W11" in prompt

    def test_contains_active_hours(self):
        week = _make_week(active_seconds=72000.0)  # 20h
        prompt = build_week_prompt(week)
        assert "20.0h" in prompt

    def test_contains_day_pattern(self):
        week = _make_week(day_pattern="front_loaded")
        prompt = build_week_prompt(week)
        assert "front_loaded" in prompt

    def test_contains_projects(self):
        week = _make_week()
        prompt = build_week_prompt(week)
        assert "sinex" in prompt

    def test_active_delta_shown_when_present(self):
        week = _make_week(active_delta_vs_prior=3600.0)
        prompt = build_week_prompt(week)
        assert "+1.0h" in prompt

    def test_active_delta_not_shown_when_absent(self):
        week = _make_week(active_delta_vs_prior=None)
        prompt = build_week_prompt(week)
        assert "delta" not in prompt.lower()

    def test_day_by_day_included_when_days_passed(self):
        week = _make_week()
        day = _make_day()
        prompt = build_week_prompt(week, days=[day])
        assert "Day-by-day" in prompt
        assert "2026-03-10" in prompt

    def test_no_day_by_day_when_no_days(self):
        week = _make_week()
        prompt = build_week_prompt(week)
        assert "Day-by-day" not in prompt


# ---------------------------------------------------------------------------
# build_episode_prompt
# ---------------------------------------------------------------------------

class TestBuildEpisodePrompt:
    def test_contains_label(self):
        ep = _make_episode()
        prompt = build_episode_prompt(ep)
        assert "sinex-sprint" in prompt

    def test_contains_trigger(self):
        ep = _make_episode(trigger="mode_shift")
        prompt = build_episode_prompt(ep)
        assert "mode_shift" in prompt

    def test_contains_active_hours(self):
        ep = _make_episode(active_seconds=36000.0)  # 10h
        prompt = build_episode_prompt(ep)
        assert "10.0h" in prompt

    def test_contains_confidence(self):
        ep = _make_episode(confidence=0.92)
        prompt = build_episode_prompt(ep)
        assert "0.92" in prompt

    def test_mode_distribution_included(self):
        ep = _make_episode()
        prompt = build_episode_prompt(ep)
        assert "coding" in prompt

    def test_project_distribution_included(self):
        ep = _make_episode()
        prompt = build_episode_prompt(ep)
        assert "lynchpin" in prompt

    def test_day_by_day_when_days_passed(self):
        ep = _make_episode()
        day = _make_day()
        prompt = build_episode_prompt(ep, days=[day])
        assert "Day-by-day" in prompt

    def test_empty_distributions_dont_crash(self):
        ep = _make_episode(mode_distribution={}, project_distribution={})
        prompt = build_episode_prompt(ep)
        assert "sinex-sprint" in prompt


# ---------------------------------------------------------------------------
# build_quarter_prompt
# ---------------------------------------------------------------------------

class TestBuildQuarterPrompt:
    def test_contains_quarter_key(self):
        q = _make_quarter()
        prompt = build_quarter_prompt(q)
        assert "2026-Q1" in prompt

    def test_contains_active_hours(self):
        q = _make_quarter(active_seconds=360000.0)  # 100h
        prompt = build_quarter_prompt(q)
        assert "100.0h" in prompt

    def test_contains_chat_cost(self):
        q = _make_quarter(chat_cost_usd=12.5)
        prompt = build_quarter_prompt(q)
        assert "12.50" in prompt

    def test_monthly_trend_included(self):
        q = _make_quarter(month_active_trend=(36000.0, 72000.0, 54000.0))
        prompt = build_quarter_prompt(q)
        assert "10h" in prompt   # 36000/3600
        assert "20h" in prompt   # 72000/3600

    def test_episode_count_included(self):
        q = _make_quarter(episode_count=5)
        prompt = build_quarter_prompt(q)
        assert "5" in prompt


# ---------------------------------------------------------------------------
# build_contrast_prompt
# ---------------------------------------------------------------------------

class TestBuildContrastPrompt:
    def test_contains_both_period_keys(self):
        prior = _make_week(iso_week="2026-W10")
        current = _make_week(iso_week="2026-W11")
        prompt = build_contrast_prompt(current, prior, "week")
        assert "2026-W10" in prompt
        assert "2026-W11" in prompt

    def test_contains_scale(self):
        prior = _make_week()
        current = _make_week()
        prompt = build_contrast_prompt(current, prior, "week")
        assert "week" in prompt

    def test_works_for_month_scale(self):
        prior = SimpleNamespace(
            month="2026-02",
            active_seconds=72000.0,
            top_modes=(("coding", 50000.0),),
            top_projects=(("sinex", 40000.0),),
        )
        current = SimpleNamespace(
            month="2026-03",
            active_seconds=86400.0,
            top_modes=(("coding", 60000.0),),
            top_projects=(("sinex", 50000.0),),
        )
        prompt = build_contrast_prompt(current, prior, "month")
        assert "2026-02" in prompt
        assert "2026-03" in prompt

    def test_year_scale_uses_year_attribute(self):
        prior = SimpleNamespace(
            year="2025",
            active_seconds=500000.0,
            top_modes=(("coding", 300000.0),),
            top_projects=(("sinex", 200000.0),),
        )
        current = SimpleNamespace(
            year="2026",
            active_seconds=600000.0,
            top_modes=(("coding", 400000.0),),
            top_projects=(("sinex", 250000.0),),
        )
        prompt = build_contrast_prompt(current, prior, "year")
        assert "2025" in prompt
        assert "2026" in prompt


# ---------------------------------------------------------------------------
# build_month_prompt (via LifeMonthTrajectorySummary-like namespace)
# ---------------------------------------------------------------------------

class TestBuildMonthPrompt:
    def _make_month_summary(self, **kwargs):
        defaults = dict(
            start_date="2026-03-01",
            end_date="2026-03-31",
            days=31,
            active_hours=110.0,
            recovery_hours=18.0,
            chain_count=150,
            signal_count=2500,
            commit_count=80,
            dominant_modes=[("coding", 70.0), ("review", 20.0)],
            dominant_projects=[("sinex", 60.0), ("lynchpin", 30.0)],
            dominant_topics=[("rust", 50.0), ("nix", 20.0)],
            highlights=["Major ingest refactor", "Sprint 5 complete"],
            chat_session_count=0,
            chat_work_events={},
            chat_cost_usd=0.0,
            episode_count=0,
            episode_labels=[],
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_contains_month_key(self):
        summary = self._make_month_summary()
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "2026-03" in prompt

    def test_contains_active_hours(self):
        summary = self._make_month_summary(active_hours=95.5)
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "95.5h" in prompt

    def test_contains_projects(self):
        summary = self._make_month_summary()
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "sinex" in prompt

    def test_highlights_included(self):
        summary = self._make_month_summary(highlights=["Sprint 5 done", "KG export added"])
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "Sprint 5 done" in prompt

    def test_chat_stats_shown_when_present(self):
        summary = self._make_month_summary(
            chat_session_count=30,
            chat_work_events={"implementation": 10, "review": 5},
            chat_cost_usd=4.25,
        )
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "30" in prompt
        assert "4.25" in prompt
        assert "implementation" in prompt

    def test_episode_info_shown_when_present(self):
        summary = self._make_month_summary(
            episode_count=2,
            episode_labels=["sinex-sprint", "nix-overhaul"],
        )
        prompt = build_month_prompt(summary, month_key="2026-03")
        assert "sinex-sprint" in prompt
        assert "Episodes (2)" in prompt


# ---------------------------------------------------------------------------
# _log_narrative
# ---------------------------------------------------------------------------

class TestLogNarrative:
    def test_log_creates_jsonl_entry(self, tmp_path):
        narrative = Narrative(
            kind="week",
            key="2026-W11",
            text="A productive week on sinex.",
            generated_at="2026-03-16T10:00:00Z",
            model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.002,
        )
        import lynchpin.retrospective.narrative as nar_module
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", tmp_path):
            _log_narrative(narrative)

        log_file = tmp_path / "narrative_2026-03-16.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["kind"] == "week"
        assert entry["key"] == "2026-W11"
        assert entry["text"] == "A productive week on sinex."
        assert entry["input_tokens"] == 100

    def test_log_appends_multiple_entries(self, tmp_path):
        n1 = Narrative("day", "2026-03-10", "Day one.", "2026-03-10T09:00:00Z", "m", 10, 5, 0.001)
        n2 = Narrative("day", "2026-03-10", "Day two.", "2026-03-10T10:00:00Z", "m", 12, 6, 0.001)

        import lynchpin.retrospective.narrative as nar_module
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", tmp_path):
            _log_narrative(n1)
            _log_narrative(n2)

        log_file = tmp_path / "narrative_2026-03-10.jsonl"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_log_ioerror_does_not_raise(self, tmp_path):
        """OSError during logging should not propagate — just log a warning."""
        n = Narrative("week", "2026-W11", "text", "2026-03-16T10:00:00Z", "m", 10, 5, 0.0)
        import lynchpin.retrospective.narrative as nar_module
        # Point to a path that cannot be created (parent is a file)
        fake_dir = tmp_path / "not_a_dir.txt"
        fake_dir.write_text("block")
        with patch.object(nar_module, "_NARRATIVE_LOG_DIR", fake_dir / "subdir"):
            _log_narrative(n)  # must not raise


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------


class TestNarrativeBackends:
    def test_resolve_backend_defaults_to_codex_exec(self):
        assert _resolve_backend(None) is NarrativeBackend.codex_exec

    def test_resolve_backend_accepts_enum(self):
        assert _resolve_backend(NarrativeBackend.codex_exec) is NarrativeBackend.codex_exec

    def test_resolve_backend_rejects_unknown(self):
        with pytest.raises(ValueError, match="Unknown narrative backend"):
            _resolve_backend("nope")

    def test_generate_via_codex_exec_uses_shared_helper(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(prompt: str, *, model=None, system_prompt=None, output_schema=None, cwd=None, codex_command="codex"):
            captured["prompt"] = prompt
            captured["model"] = model
            captured["system_prompt"] = system_prompt
            captured["output_schema"] = output_schema
            captured["cwd"] = cwd
            captured["codex_command"] = codex_command
            return SimpleNamespace(model="codex-config-default", text="codex narrative")

        monkeypatch.setattr("lynchpin.retrospective.narrative.run_codex_exec", fake_run)
        monkeypatch.setattr("lynchpin.retrospective.narrative.NARRATIVE_SYSTEM_PROMPT", "system prompt")

        model, text, input_tokens, output_tokens, cost_usd = _generate_via_codex_exec("write a summary", None)

        assert model == "codex-config-default"
        assert text == "codex narrative"
        assert input_tokens == 0
        assert output_tokens == 0
        assert cost_usd == 0.0
        assert captured["prompt"] == "write a summary"
        assert captured["system_prompt"] == "system prompt"

    def test_enrich_prompt_for_sdk_includes_prequeried_context(self, monkeypatch):
        monkeypatch.setattr(
            "lynchpin.retrospective.narrative._query_git_commits",
            lambda start, end: "### sinex\nabc123 ship feature",
        )
        monkeypatch.setattr(
            "lynchpin.retrospective.narrative._query_duckdb_context",
            lambda start, end: "### Episodes\nfocus-sprint",
        )

        enriched = _enrich_prompt_for_sdk("base prompt", NarrativeKind.week, "2026-W11")

        assert enriched.startswith("base prompt")
        assert "## Git commits" in enriched
        assert "abc123 ship feature" in enriched
        assert "## Warehouse data" in enriched
        assert "focus-sprint" in enriched

    def test_generate_narrative_uses_claude_backend_enrichment(self, monkeypatch):
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "lynchpin.retrospective.narrative._enrich_prompt_for_sdk",
            lambda prompt, kind, key: f"enriched::{kind.value}::{key}::{prompt}",
        )

        async def fake_generate(prompt: str, model: str | None):
            captured["prompt"] = prompt
            captured["model"] = model
            return "claude-sonnet", "claude narrative", 11, 7, 0.0

        monkeypatch.setattr(
            "lynchpin.retrospective.narrative._generate_via_claude_agent_sdk",
            fake_generate,
        )
        monkeypatch.setattr("lynchpin.retrospective.narrative._log_narrative", lambda narrative: None)

        result = asyncio.run(
            generate_narrative(
                "write a summary",
                NarrativeKind.week,
                "2026-W11",
                backend="claude-agent-sdk",
                model="claude-sonnet",
            )
        )

        assert captured["prompt"] == "enriched::week::2026-W11::write a summary"
        assert captured["model"] == "claude-sonnet"
        assert result.backend == "claude-agent-sdk"
        assert result.model == "claude-sonnet"
        assert result.text == "claude narrative"

    def test_generate_date_range_narrative_uses_window_and_range_kind(self, monkeypatch):
        fake_day = _make_day(date=date(2026, 3, 7))
        captured: dict[str, object] = {}

        async def fake_generate(prompt, kind, key, *, backend=None, model=None):
            captured["prompt"] = prompt
            captured["kind"] = kind
            captured["key"] = key
            captured["backend"] = backend
            captured["model"] = model
            return Narrative(
                kind=kind.value,
                key=key,
                text="narrative",
                generated_at="2026-03-10T10:00:00Z",
                model="codex-config-default",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                backend="codex-exec",
            )

        monkeypatch.setattr(
            "lynchpin.retrospective.narrative.load_date_window",
            lambda start, end: SimpleNamespace(days=[fake_day]),
        )
        monkeypatch.setattr("lynchpin.retrospective.narrative.generate_narrative", fake_generate)

        result = asyncio.run(
            generate_date_range_narrative(
                date(2026, 3, 7),
                date(2026, 3, 7),
                mode="reflective",
                backend="codex-exec",
                model="test-model",
            )
        )

        assert result.text == "narrative"
        assert captured["kind"] == NarrativeKind.range
        assert captured["backend"] == "codex-exec"
        assert captured["model"] == "test-model"
        assert "2026-03-07" in captured["prompt"]
