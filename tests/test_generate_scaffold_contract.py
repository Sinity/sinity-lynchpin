"""Focused tests for narrative scaffold rollup contracts."""

from datetime import date
from types import SimpleNamespace

from lynchpin.scripts.generate_scaffold import _summarize_ai, _summarize_health, _summarize_sleep, generate_hierarchy


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


def test_generate_hierarchy_dry_run_returns_success(tmp_path):
    assert generate_hierarchy(
        date(2026, 3, 20),
        date(2026, 3, 20),
        tmp_path,
        dry_run=True,
    ) is True
