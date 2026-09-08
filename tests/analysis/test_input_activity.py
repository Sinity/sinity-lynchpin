from datetime import UTC, datetime, timedelta

import pytest

from lynchpin.analysis.input_activity import ActivityPolicy, ContextSpan, foreground_contexts, reconstruct_activity
from lynchpin.sources.activitywatch_models import AWEvent
from lynchpin.sources.keylog import KeylogEvent, KeylogInputTrace


BASE = datetime(2026, 4, 2, 10, tzinfo=UTC)


def event(second, key="KEY_A", **kwargs):
    return KeylogEvent(BASE + timedelta(seconds=second), "press", "keyboard", None, key, True, **kwargs)


def context(start=0, end=600, name="a"):
    return ContextSpan(name, BASE + timedelta(seconds=start), BASE + timedelta(seconds=end), "editor", name, name)


def trace(events):
    return KeylogInputTrace(BASE, BASE + timedelta(seconds=600), tuple(events), (), {})


def test_single_press_has_no_duration_or_invented_reading():
    result = reconstruct_activity(trace([event(1)]), [context()])
    assert result["totals"]["input_count"] == 1
    assert result["totals"]["bout_seconds"] == 0
    assert result["totals"]["between_input_seconds"] == 0
    assert result["totals"]["unmeasured_context_seconds"] == 600
    assert "text_fragments" not in result


def test_bracketed_gap_is_separate_from_input_bouts():
    result = reconstruct_activity(trace([event(t) for t in (1, 2, 100, 101)]), [context()])
    assert result["totals"]["bout_seconds"] == 2
    assert result["totals"]["within_burst_seconds"] == 2
    assert result["totals"]["between_input_seconds"] == 98
    assert result["totals"]["unmeasured_context_seconds"] == 500
    assert result["gap_candidates"][0]["interpretation"] == "same_context_between_inputs"


def test_focus_change_blocks_bridging_even_when_title_returns():
    result = reconstruct_activity(trace([event(1), event(99)]), [context(0, 40), context(40, 60, "b"), context(60, 600, "a2")])
    assert result["totals"]["between_input_seconds"] == 0
    assert result["totals"]["bout_seconds"] == 0


def test_long_silence_capture_change_and_missing_context_do_not_bridge():
    changed = KeylogEvent(BASE + timedelta(seconds=9), "press", "restarted", None, "KEY_A", True)
    result = reconstruct_activity(trace([event(1), changed, event(400)]), [context()], policy=ActivityPolicy(reading_gap_s=200))
    assert result["totals"]["between_input_seconds"] == 0
    assert result["totals"]["bout_seconds"] == 0
    missing = reconstruct_activity(trace([event(1), event(2)]), [])
    assert missing["totals"]["unattributed_input_count"] == 2
    assert missing["totals"]["bout_seconds"] == 0


def test_overlap_is_rejected_instead_of_counted_twice():
    with pytest.raises(ValueError, match="overlap"):
        reconstruct_activity(trace([event(1)]), [context(), context(5, 15, "b")])


def test_reported_periods_survive_silence_without_double_counting():
    result = reconstruct_activity(trace([event(1), event(2)]), [context()], reported_periods=[
        {"start": BASE.isoformat(), "end": (BASE + timedelta(seconds=300)).isoformat(), "source_ref": "statement-a"},
        {"start": (BASE + timedelta(seconds=200)).isoformat(), "end": (BASE + timedelta(seconds=400)).isoformat(), "source_ref": "statement-b"},
    ])
    assert result["reported_periods"]["union_seconds"] == 400
    assert result["reported_periods"]["bout_seconds_within"] == 1
    assert result["totals"]["bout_seconds"] == 1


def test_wheel_and_pointer_motion_are_not_interchangeable():
    wheel = KeylogEvent(BASE, "pointer_rel", "mouse", None, None, None, code="REL_WHEEL", value=1)
    move = KeylogEvent(BASE + timedelta(seconds=1), "pointer_rel", "mouse", None, None, None, code="REL_X", value=1)
    result = reconstruct_activity(trace([wheel, move]), [context()])
    assert result["totals"]["input_count"] == 1
    assert result["totals"]["counts"]["wheel"] == 1


def test_foreground_heartbeats_merge_but_zero_duration_changes_are_barriers():
    def window(a, b, title="draft", bucket="host"):
        return AWEvent(bucket, BASE + timedelta(seconds=a), BASE + timedelta(seconds=b), {"app": "editor", "title": title})

    spans = foreground_contexts([window(0, 60), window(60, 120)], start=BASE, end=BASE + timedelta(seconds=200))
    assert len(spans) == 1
    assert spans[0].end == BASE + timedelta(seconds=120)
    interrupted = foreground_contexts([window(0, 120), window(50, 50, "other"), window(60, 120)], start=BASE, end=BASE + timedelta(seconds=200))
    assert len(interrupted) == 2
    assert interrupted[0].end == BASE + timedelta(seconds=50)
    with pytest.raises(ValueError, match="multiple"):
        foreground_contexts([window(0, 60), window(60, 120, bucket="second")], start=BASE, end=BASE + timedelta(seconds=200))
