from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_promote_polylogue_timeline_round_trip(tmp_path):
    from lynchpin.sources.polylogue_timeline_models import (
        PolylogueCrossSourceOverlap,
        PolylogueSessionComposition,
        PolylogueTimelineSpan,
    )
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.polylogue_timeline import (
        load_polylogue_session_compositions,
        promote_polylogue_cross_source_overlaps,
        promote_polylogue_session_compositions,
        promote_polylogue_timeline_spans,
    )

    start = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)
    span = PolylogueTimelineSpan(
        span_id="s1:message_gap:0",
        session_id="s1",
        provider="codex",
        lane="message_gap",
        kind="assistant_response_wait",
        start=start,
        end=start + timedelta(seconds=5),
        source="polylogue.message_transition",
    )
    comp = PolylogueSessionComposition(
        session_id="s1",
        provider="codex",
        title="timeline",
        start=start,
        end=start + timedelta(seconds=5),
        status="ok",
        reason=None,
        message_count=2,
        wall_seconds=5.0,
        engaged_seconds=5.0,
        span_count=1,
        overlap_count=1,
        seconds_by_lane={"message_gap": 5.0},
        seconds_by_kind={"assistant_response_wait": 5.0},
        cross_source_seconds={"activitywatch.focus_timeline": 3.0},
        projects=("sinity-lynchpin",),
        tags=("test",),
    )
    overlap = PolylogueCrossSourceOverlap(
        session_id="s1",
        primary_span_id="s1:message_gap:0",
        other_span_id="s1:aw:0",
        source="activitywatch.focus_timeline",
        lane="activitywatch",
        kind="focused",
        start=start,
        end=start + timedelta(seconds=3),
        duration_s=3.0,
        project="sinity-lynchpin",
    )
    with connect(tmp_path / "substrate.duckdb") as conn:
        apply_schema(conn)
        assert promote_polylogue_timeline_spans(conn, refresh_id="r1", rows=[span]) == 1
        assert promote_polylogue_session_compositions(conn, refresh_id="r1", rows=[comp]) == 1
        assert promote_polylogue_cross_source_overlaps(conn, refresh_id="r1", rows=[overlap]) == 1
        loaded = load_polylogue_session_compositions(conn, refresh_id="r1")

    assert loaded[0]["session_id"] == "s1"
    assert loaded[0]["seconds_by_kind"]["assistant_response_wait"] == 5.0
    assert loaded[0]["cross_source_seconds"]["activitywatch.focus_timeline"] == 3.0
