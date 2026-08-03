"""Tests for adversarial validation of intra-night sleep-record gaps."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from lynchpin.analysis import sleep_gap_evidence as sge

D = date(2026, 1, 1)
NIGHT = datetime(2026, 1, 1, 23, 0)


class _Episode:
    def __init__(self, segments):
        self.segments = tuple(segments)
        self.start = segments[0][0]
        self.end = segments[-1][1]
        self.asleep_minutes = sum(
            (e - s).total_seconds() / 60.0 for s, e in segments
        )


class _Composite:
    def __init__(self, segments, secondary=()):
        self.date = D
        self.main = _Episode(segments)
        self.secondary = tuple(secondary)


def _minutes(lo, count, value):
    return {lo + timedelta(minutes=i): value for i in range(count)}


def _patch(monkeypatch, *, composite, heart=None, movement=None,
           presses=None, stages=None):
    monkeypatch.setattr(
        "lynchpin.sources.sleep_composite.night_composites",
        lambda **kw: [composite],
    )
    series = {"heart": heart or {}, "movement": movement or {}}
    monkeypatch.setattr(
        sge, "_minute_series",
        lambda filename, key, s, e: series["heart"] if "heart" in filename else series["movement"],
    )
    monkeypatch.setattr(sge, "_keypress_minutes", lambda s, e: set(presses or ()))
    monkeypatch.setattr(sge, "_stage_minutes", lambda s, e: set(stages or ()))


# A night split into two segments with a 40-minute gap at 01:00-01:40.
SEGMENTS = [
    (NIGHT, NIGHT + timedelta(hours=2)),
    (NIGHT + timedelta(hours=2, minutes=40), NIGHT + timedelta(hours=7)),
]
GAP_START = NIGHT + timedelta(hours=2)


def test_absent_heart_rate_reads_as_detection_artifact(monkeypatch):
    # HR covers the segments but not the gap
    heart = _minutes(NIGHT, 120, 55.0)
    _patch(monkeypatch, composite=_Composite(SEGMENTS), heart=heart)
    report = sge.classify_night_gaps(start=D, end=D)
    (gap,) = report.gaps
    assert gap.verdict == sge.SENSOR_ABSENT
    assert gap.real_transition is False
    assert gap.intra_episode is True


def test_keystrokes_in_gap_are_definitive_wakefulness(monkeypatch):
    heart = _minutes(NIGHT, 420, 55.0)
    _patch(
        monkeypatch, composite=_Composite(SEGMENTS), heart=heart,
        presses=[GAP_START + timedelta(minutes=5)],
    )
    report = sge.classify_night_gaps(start=D, end=D)
    (gap,) = report.gaps
    assert gap.verdict == sge.CONFIRMED_WAKE_PC
    assert gap.real_transition is True


def test_elevated_heart_rate_reads_as_genuine_wake(monkeypatch):
    heart = _minutes(NIGHT, 120, 50.0)
    heart.update(_minutes(GAP_START, 40, 75.0))      # 1.5x baseline
    heart.update(_minutes(GAP_START + timedelta(minutes=40), 260, 50.0))
    _patch(monkeypatch, composite=_Composite(SEGMENTS), heart=heart)
    report = sge.classify_night_gaps(start=D, end=D)
    (gap,) = report.gaps
    assert gap.verdict == sge.GENUINE_WAKE
    assert gap.real_transition is True
    assert gap.hr_ratio is not None and gap.hr_ratio > 1.15


def test_sleeplike_heart_rate_reads_as_missed_sleep(monkeypatch):
    heart = _minutes(NIGHT, 420, 52.0)  # flat, including through the gap
    _patch(monkeypatch, composite=_Composite(SEGMENTS), heart=heart)
    report = sge.classify_night_gaps(start=D, end=D)
    (gap,) = report.gaps
    assert gap.verdict == sge.MISSED_SLEEP
    assert gap.real_transition is False


def test_stage_stream_covering_the_gap_overrides_arousal(monkeypatch):
    """Samsung scored stages through the gap: the record boundary is the artifact."""
    heart = _minutes(NIGHT, 120, 50.0)
    heart.update(_minutes(GAP_START, 40, 80.0))
    heart.update(_minutes(GAP_START + timedelta(minutes=40), 260, 50.0))
    stages = [GAP_START + timedelta(minutes=i) for i in range(40)]
    _patch(monkeypatch, composite=_Composite(SEGMENTS), heart=heart, stages=stages)
    report = sge.classify_night_gaps(start=D, end=D)
    (gap,) = report.gaps
    assert gap.verdict == sge.STAGE_CONTINUOUS
    assert gap.real_transition is False


def test_artifact_gaps_are_filled_but_inter_episode_gaps_never_are(monkeypatch):
    secondary = _Episode([
        (NIGHT + timedelta(hours=14), NIGHT + timedelta(hours=15)),
    ])
    composite = _Composite(SEGMENTS, secondary=[secondary])
    heart = _minutes(NIGHT, 900, 52.0)  # sleep-like everywhere, including both gaps
    _patch(monkeypatch, composite=composite, heart=heart)

    report = sge.classify_night_gaps(start=D, end=D)
    intra = [g for g in report.gaps if g.intra_episode]
    inter = [g for g in report.gaps if not g.intra_episode]
    assert len(intra) == 1 and len(inter) == 1
    assert all(g.verdict == sge.MISSED_SLEEP for g in report.gaps)

    intervals = sge.evidence_validated_intervals(start=D, end=D)
    # the intra gap is healed into one contiguous block...
    assert len(intervals) == 1
    span_start, span_end, night = intervals[0]
    assert night == D
    assert span_start == SEGMENTS[0][0]
    assert span_end == SEGMENTS[1][1]
    # ...and the 7-hour inter-episode gap was NOT bridged
    assert (span_end - span_start).total_seconds() / 3600 < 8


def test_report_counts_split_real_from_artifact(monkeypatch):
    heart = _minutes(NIGHT, 420, 52.0)
    _patch(monkeypatch, composite=_Composite(SEGMENTS), heart=heart)
    report = sge.classify_night_gaps(start=D, end=D)
    assert report.real_transitions == 0
    assert report.artifact_gaps == 1
    assert report.counts[sge.MISSED_SLEEP] == 1
    assert report.intra_counts[sge.MISSED_SLEEP] == 1
    assert any("not ground truth" in c for c in report.caveats)
