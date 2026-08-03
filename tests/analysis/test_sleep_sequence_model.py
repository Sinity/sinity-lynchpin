"""Tests for block prediction and the HMM sequence model."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np

from lynchpin.analysis import sleep_stage_model as ssm


class _Stage:
    def __init__(self, start, end, stage):
        self.start, self.end, self.stage = start, end, stage


BASE = datetime(2026, 1, 1, 23, 0)
D = date(2026, 1, 1)


def _separable_world(nights: int = 30, asleep_minutes: int = 180,
                     awake_minutes: int = 60):
    movement, heart, stages = {}, {}, []
    for n in range(nights):
        start = BASE + timedelta(days=n)
        for i in range(asleep_minutes + awake_minutes):
            minute = start + timedelta(minutes=i)
            asleep = i < asleep_minutes
            movement[minute] = 0.1 if asleep else 6.0
            heart[minute] = 52.0 if asleep else 88.0
        stages.append(_Stage(start, start + timedelta(minutes=asleep_minutes), "deep"))
        stages.append(
            _Stage(start + timedelta(minutes=asleep_minutes),
                   start + timedelta(minutes=asleep_minutes + awake_minutes), "awake")
        )
    return movement, heart, stages


def _patch(monkeypatch, movement, heart, stages):
    monkeypatch.setattr(
        ssm, "_load_minute_series",
        lambda filename, key, s, e: movement if "movement" in filename else heart,
    )
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_stages", lambda **kw: stages)


def test_forward_backward_respects_state_persistence():
    # one noisy minute inside a confident asleep run must not flip the posterior
    emissions = np.array([[0.1, 0.9]] * 20 + [[0.9, 0.1]] + [[0.1, 0.9]] * 20)
    sticky = [[0.99, 0.01], [0.01, 0.99]]
    loose = [[0.5, 0.5], [0.5, 0.5]]
    initial = [0.5, 0.5]
    smoothed = ssm._forward_backward(emissions, sticky, initial)
    unsmoothed = ssm._forward_backward(emissions, loose, initial)
    assert smoothed[20] > 0.5        # persistence rescues the noisy minute
    assert unsmoothed[20] < 0.5      # without persistence it flips
    assert smoothed[0] > 0.9 and smoothed[-1] > 0.9


def test_transition_matrix_recovers_stickiness():
    seqs = [[1] * 100 + [0] * 20 for _ in range(5)]
    matrix, initial = ssm._fit_transitions(seqs)
    assert matrix[1][1] > 0.95      # stay-asleep is high
    assert matrix[0][0] > 0.9       # stay-awake is high
    assert initial[1] > initial[0]  # nights start asleep here
    for row in matrix:
        assert abs(sum(row) - 1.0) < 1e-9


def test_sequence_model_beats_or_matches_independent_on_separable_data(monkeypatch):
    movement, heart, stages = _separable_world()
    _patch(monkeypatch, movement, heart, stages)
    fitted, report = ssm.train_sequence_model(start=D, end=D + timedelta(days=31))
    assert fitted is not None
    assert set(fitted) == {"classifier", "transitions", "initial"}
    assert report.sequence_auc >= report.independent_auc - 1e-6
    assert report.median_longest_block_minutes > 60
    assert 0.0 < report.transition_stay_asleep < 1.0
    assert any("temporal persistence" in c for c in report.caveats)


def test_sequence_report_carries_the_majority_baseline(monkeypatch):
    movement, heart, stages = _separable_world()
    _patch(monkeypatch, movement, heart, stages)
    _, report = ssm.train_sequence_model(start=D, end=D + timedelta(days=31))
    assert 0.5 < report.baseline_majority < 1.0
    assert report.n_nights == 30


def test_sequence_model_degrades_gracefully_without_data(monkeypatch):
    _patch(monkeypatch, {}, {}, [])
    fitted, report = ssm.train_sequence_model(start=D, end=D)
    assert fitted is None
    assert math.isnan(report.sequence_auc)


def test_predicted_blocks_need_sustained_confidence(monkeypatch):
    movement, heart, stages = _separable_world()
    _patch(monkeypatch, movement, heart, stages)
    model, _ = ssm.train_sleep_wake_model(start=D, end=D + timedelta(days=31))
    assert model is not None

    blocks = ssm.predict_sleep_blocks(
        start=D, end=D + timedelta(days=31), model=model,
        min_probability=0.5, min_block_minutes=60,
    )
    assert blocks
    assert all(b.minutes >= 60 for b in blocks)
    # every block on this synthetic world is already Samsung-labelled
    assert all(b.labelled_fraction > 0.9 for b in blocks)
    assert all(not b.is_recovered for b in blocks)

    # an unreachable threshold must yield nothing rather than weak claims
    assert ssm.predict_sleep_blocks(
        start=D, end=D + timedelta(days=31), model=model,
        min_probability=1.0, min_block_minutes=60,
    ) == []

    # and an unreachable *duration* likewise drops the blocks entirely
    assert ssm.predict_sleep_blocks(
        start=D, end=D + timedelta(days=31), model=model,
        min_probability=0.5, min_block_minutes=10_000,
    ) == []


def test_threshold_for_precision_is_monotone_in_target(monkeypatch):
    movement, heart, stages = _separable_world()
    _patch(monkeypatch, movement, heart, stages)
    model, _ = ssm.train_sleep_wake_model(start=D, end=D + timedelta(days=31))
    lenient = ssm.threshold_for_precision(
        start=D, end=D + timedelta(days=31), model=model, target_precision=0.6
    )
    strict = ssm.threshold_for_precision(
        start=D, end=D + timedelta(days=31), model=model, target_precision=0.95
    )
    assert lenient is not None and strict is not None
    assert strict >= lenient
