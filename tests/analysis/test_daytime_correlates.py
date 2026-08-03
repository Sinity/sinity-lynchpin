"""Production-route tests for the daytime cross-source correlates module.

Anti-vacuity statement: these tests run ``daytime_correlates.analyze`` — the
production route including pair construction, the autocorr-corrected Pearson
p (``core.analytics.autocorr_corrected_pearson``), the family-wide BH FDR
pass, power gating, and the computed coverage negatives. Only the I/O loaders
are swapped for in-memory series (the module's documented override hooks).
Mutations that fail these tests include: dropping the FDR pass (noise test),
dropping the power gate (underpowered family test), breaking the
in-coverage-zeros substance semantics (planted-signal test relies on zero
days), and breaking the no-overlap negative computation.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

import lynchpin.analysis.daytime_correlates as dc
from lynchpin.analysis.daytime_correlates import FDR_TARGET, MIN_PAIRS, analyze


START = date(2026, 2, 15)
N_DAYS = 140


def _days(n: int = N_DAYS, start: date = START) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _patch_loaders(
    monkeypatch: pytest.MonkeyPatch,
    *,
    aw: dict[str, dict[date, float]] | None = None,
    physio: dict[str, dict[date, float]] | None = None,
    keypress: dict[date, float] | None = None,
    substance: dict[str, dict[date, float]] | None = None,
    machine: dict[str, dict[date, float]] | None = None,
    git: dict[date, float] | None = None,
    sleep_min: dict[date, float] | None = None,
    spotify: dict[date, float] | None = None,
    typing: dict[str, dict[date, float]] | None = None,
    ytmusic: dict[date, float] | None = None,
    ai_load: dict[str, dict[date, float]] | None = None,
) -> None:
    empty_aw = {"deep_work_min": {}, "active_hours": {}, "fragmentation": {}}
    monkeypatch.setattr(dc, "_aw_loader", lambda: aw if aw is not None else empty_aw)
    monkeypatch.setattr(dc, "_physio_loader", lambda: physio or {})
    monkeypatch.setattr(dc, "_keypress_loader", lambda: keypress or {})
    monkeypatch.setattr(dc, "_substance_loader", lambda: substance or {})
    monkeypatch.setattr(dc, "_machine_loader", lambda: machine or {})
    monkeypatch.setattr(dc, "_git_loader", lambda s, e: git or {})
    monkeypatch.setattr(dc, "_sleep_loader", lambda: sleep_min or {})
    monkeypatch.setattr(dc, "_spotify_loader", lambda: spotify or {})
    monkeypatch.setattr(dc, "_typing_loader", lambda s, e: typing or {})
    monkeypatch.setattr(dc, "_ytmusic_loader", lambda s, e: ytmusic or {})
    monkeypatch.setattr(dc, "_ai_load_loader", lambda: ai_load or {})


def test_planted_substance_focus_signal_survives_fdr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strong dose→deep-work link (including genuine zero-dose days) is found."""
    rng = random.Random(42)
    days = _days()
    total_mg: dict[date, float] = {}
    deep: dict[date, float] = {}
    frag: dict[date, float] = {}
    active: dict[date, float] = {}
    for i, d in enumerate(days):
        mg = 0.0 if i % 3 == 0 else float(rng.choice([20, 40, 60]))
        total_mg[d] = mg
        deep[d] = 30.0 + 2.5 * mg + rng.uniform(-15, 15)  # planted link
        frag[d] = rng.uniform(0, 1)
        active[d] = rng.uniform(2, 12)
    _patch_loaders(
        monkeypatch,
        aw={"deep_work_min": deep, "fragmentation": frag, "active_hours": active},
        substance={"total_mg": total_mg, "dose_count": {d: 1.0 for d in days}},
    )

    report = analyze(days[0], days[-1])

    sig = [c for c in report.correlations if c.significant]
    assert any(
        c.family == "substance_focus"
        and c.predictor == "total_mg"
        and c.outcome == "deep_work_min"
        and c.lag_days == 0
        for c in sig
    ), f"planted dose→deep-work link should survive FDR; got {sig}"
    fam = next(f for f in report.families if f.name == "substance_focus")
    assert fam.status == "computed"
    assert fam.n_days >= MIN_PAIRS
    assert 0.0 < fam.min_detectable_r < 1.0
    # Zero-dose days participated: n covers the full overlap, not use-days only.
    planted = next(
        c for c in sig if c.predictor == "total_mg" and c.outcome == "deep_work_min"
    )
    assert planted.n == len(days)
    assert "not causation" in report.summary.lower()


def test_pure_noise_yields_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(1234)
    days = _days()
    _patch_loaders(
        monkeypatch,
        aw={
            "deep_work_min": {d: rng.uniform(0, 240) for d in days},
            "fragmentation": {d: rng.uniform(0, 1) for d in days},
            "active_hours": {d: rng.uniform(0, 14) for d in days},
        },
        physio={
            "stress": {d: rng.uniform(20, 80) for d in days},
            "hr_mean": {d: rng.uniform(60, 90) for d in days},
        },
        keypress={d: rng.uniform(0, 30000) for d in days},
        machine={
            "io_psi_mean": {d: rng.uniform(0, 5) for d in days},
            "kill_events": {d: float(rng.randint(0, 3)) for d in days},
        },
        git={d: float(rng.randint(0, 20)) for d in days},
    )

    report = analyze(days[0], days[-1])

    assert report.n_tests > 0
    assert not any(c.significant for c in report.correlations)
    for c in report.correlations:
        assert c.n >= MIN_PAIRS
        assert 0.0 <= c.p_value <= 1.0
        assert 0.0 <= c.q_value <= 1.0
        assert c.significant == (c.q_value < FDR_TARGET)
        assert c.n_eff <= c.n


def test_underpowered_family_reported_not_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family with fewer than MIN_PAIRS overlapping days must be surfaced
    as underpowered instead of silently vanishing."""
    rng = random.Random(5)
    days = _days(MIN_PAIRS - 5)  # below the gate
    _patch_loaders(
        monkeypatch,
        aw={
            "deep_work_min": {d: rng.uniform(0, 240) for d in days},
            "fragmentation": {},
            "active_hours": {},
        },
        machine={"io_psi_mean": {d: rng.uniform(0, 5) for d in days}},
    )

    report = analyze(days[0], days[-1] + timedelta(days=30))

    fam = next(f for f in report.families if f.name == "machine_pressure_focus")
    assert fam.status == "underpowered"
    assert fam.n_days == 0
    assert not [c for c in report.correlations if c.family == "machine_pressure_focus"]
    assert "MIN_PAIRS" in fam.note


def test_no_overlap_negative_is_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spotify ends before AW begins → the music_focus negative reports
    zero overlap with an explicit note (a documented negative, not silence)."""
    rng = random.Random(9)
    aw_days = _days(60, start=date(2026, 2, 15))
    spotify_days = _days(60, start=date(2025, 10, 1))  # disjoint, earlier
    _patch_loaders(
        monkeypatch,
        aw={
            "deep_work_min": {d: rng.uniform(0, 240) for d in aw_days},
            "fragmentation": {},
            "active_hours": {},
        },
        spotify={d: rng.uniform(0, 300) for d in spotify_days},
    )

    report = analyze(date(2025, 10, 1), aw_days[-1])

    neg = next(f for f in report.families if f.name.startswith("music_focus"))
    assert neg.status == "no_overlap"
    assert neg.n_days == 0
    assert "No overlap" in neg.note


def test_keystroke_physiology_bidirectional_lags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rng = random.Random(21)
    days = _days()
    keypress = {d: rng.uniform(0, 30000) for d in days}
    stress = {d: rng.uniform(20, 80) for d in days}
    _patch_loaders(monkeypatch, keypress=keypress, physio={"stress": stress})

    report = analyze(days[0], days[-1])

    pairs = {
        (c.predictor, c.outcome, c.lag_days)
        for c in report.correlations
        if c.family == "keystroke_physiology"
    }
    assert ("keypress", "stress", 0) in pairs
    assert ("keypress", "stress", 1) in pairs
    assert ("stress", "keypress", 1) in pairs


def test_min_detectable_r_monotone() -> None:
    assert dc.min_detectable_r(10) > dc.min_detectable_r(50) > dc.min_detectable_r(500)
    assert dc.min_detectable_r(3) == 1.0


def test_ytmusic_and_ai_load_families(monkeypatch: pytest.MonkeyPatch) -> None:
    """The revived music family and the AI-load family run through the same
    production route (pair construction, corrected p, one FDR family)."""
    rng = random.Random(77)
    days = _days()
    deep = {d: rng.uniform(0, 240) for d in days}
    plays = {}
    ai_sessions = {}
    for i, d in enumerate(days):
        plays[d] = float(rng.randint(0, 120))
        # planted: heavy AI-delegation days have low fragmentation
        ai_sessions[d] = float(rng.randint(0, 90))
    frag = {d: max(0.0, 1.0 - ai_sessions[d] / 100.0 + rng.uniform(-0.05, 0.05)) for d in days}
    _patch_loaders(
        monkeypatch,
        aw={"deep_work_min": deep, "fragmentation": frag, "active_hours": {}},
        ytmusic=plays,
        ai_load={"ai_sessions": ai_sessions, "ai_user_words": {}},
    )

    report = analyze(days[0], days[-1])

    fams = {f.name: f for f in report.families}
    assert fams["ytmusic_focus"].status == "computed"
    assert fams["ai_load"].status == "computed"
    planted = [
        c for c in report.correlations
        if c.family == "ai_load" and c.predictor == "ai_sessions"
        and c.outcome == "fragmentation" and c.significant
    ]
    assert planted and planted[0].r < -0.5


def test_typing_dynamics_family_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(31)
    days = _days()
    iki = {d: rng.uniform(120, 260) for d in days}
    stress = {d: rng.uniform(20, 80) for d in days}
    _patch_loaders(
        monkeypatch,
        typing={"iki_median_ms": iki, "typing_minutes": {d: rng.uniform(5, 90) for d in days}},
        physio={"stress": stress},
        substance={"total_mg": {d: float(rng.choice([0, 20, 40])) for d in days}},
    )

    report = analyze(days[0], days[-1])

    pairs = {
        (c.predictor, c.outcome)
        for c in report.correlations
        if c.family == "typing_dynamics"
    }
    assert ("iki_median_ms", "stress") in pairs
    assert ("total_mg", "iki_median_ms") in pairs
