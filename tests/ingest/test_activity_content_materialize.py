from __future__ import annotations

import json
from datetime import date


def test_materialize_activity_content_records_input_high_water(monkeypatch, tmp_path):
    from lynchpin.ingest import activity_content_materialize
    from lynchpin.ingest.activity_content_materialize import ACTIVITY_CONTENT_SCHEMA_VERSION

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    focus_calls = []

    def fake_focus_spans(**kwargs):
        focus_calls.append(kwargs)
        return iter(())

    monkeypatch.setattr(activity_content_materialize, "focus_spans", fake_focus_spans)

    manifest = activity_content_materialize.materialize_activity_content(
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        output=output,
    )

    assert manifest["row_count"] == 0
    assert manifest["schema_version"] == ACTIVITY_CONTENT_SCHEMA_VERSION
    assert manifest["input_file_count"] == 2
    assert manifest["input_latest_mtime"] is not None
    assert focus_calls
    assert all(call["enrich_polylogue"] is False for call in focus_calls)


def test_materialize_activity_content_bounds_focus_windows(monkeypatch, tmp_path):
    from lynchpin.ingest import activity_content_materialize

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    focus_calls = []
    monkeypatch.setattr(
        activity_content_materialize,
        "focus_spans",
        lambda **kwargs: focus_calls.append(kwargs) or iter(()),
    )

    activity_content_materialize.materialize_activity_content(
        start=date(2026, 1, 1),
        end=date(2026, 2, 15),
        output=output,
    )

    assert len(focus_calls) == 31
    assert (focus_calls[0]["end"] - focus_calls[0]["start"]).days == 15
    assert all(
        (call["end"] - call["start"]).days <= activity_content_materialize.RECENT_CHUNK_DAYS
        for call in focus_calls[1:]
    )


def _write_preserved_daily_fixture(output, day: str) -> None:
    preserved_row = {
        "date": day,
        "focused_seconds": 120.0,
        "matched_seconds": 60.0,
        "gpt_matched_seconds": 0.0,
        "unmatched_seconds": 60.0,
        "matched_ratio": 0.5,
        "gpt_matched_ratio": 0.0,
        "activity_seconds": {},
        "content_type_seconds": {},
        "attention_seconds": {},
        "topic_seconds": {},
        "platform_seconds": {},
        "source_counts": {"rules": 1},
    }
    output.write_text(json.dumps(preserved_row) + "\n", encoding="utf-8")
    output.with_suffix(".manifest.json").write_text(
        f'{{"first_date": "{day}", "last_date": "{day}"}}\n',
        encoding="utf-8",
    )


def test_materialize_activity_content_bootstraps_persistent_store_once(monkeypatch, tmp_path):
    """The first call against a pre-existing (old-code) product must widen
    once to seed the new persistent title-usage store from full history.

    lynchpin-d36: title_usage.ndjson is rebuilt purely from that store. If
    the very first call under the new code stayed scoped to a narrow window,
    the store would only ever contain that window's days and the export
    would silently truncate years of lifetime totals down to a few days.
    """
    from lynchpin.ingest import activity_content_materialize

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"
    _write_preserved_daily_fixture(output, "2026-01-15")

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    focus_calls = []
    monkeypatch.setattr(
        activity_content_materialize,
        "focus_spans",
        lambda **kwargs: focus_calls.append(kwargs) or iter(()),
    )

    activity_content_materialize.materialize_activity_content(
        start=date(2026, 2, 15),
        end=date(2026, 3, 2),
        output=output,
    )

    assert focus_calls[0]["start"].date() == date(2026, 1, 15)
    assert focus_calls[-1]["end"].date() == date(2026, 3, 2)


def test_materialize_activity_content_stays_scoped_once_bootstrapped(monkeypatch, tmp_path):
    """Once the persistent title-usage store exists, further calls must stay
    scoped to exactly what they ask for — the lynchpin-d36 regression was
    every call reprocessing the product's entire history forever.
    """
    from lynchpin.ingest import activity_content_materialize

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"
    _write_preserved_daily_fixture(output, "2026-01-15")

    usage_output = tmp_path / "title_usage.ndjson"
    facts_path = usage_output.with_name(f"{usage_output.stem}.facts.sqlite3")
    activity_content_materialize._TitleUsageStore(facts_path).close()  # simulates an already-bootstrapped store

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    focus_calls = []
    monkeypatch.setattr(
        activity_content_materialize,
        "focus_spans",
        lambda **kwargs: focus_calls.append(kwargs) or iter(()),
    )

    manifest = activity_content_materialize.materialize_activity_content(
        start=date(2026, 2, 15),
        end=date(2026, 3, 2),
        output=output,
    )

    # Scoped to exactly the requested window — not expanded back to 2026-01-15.
    assert focus_calls[0]["start"].date() == date(2026, 2, 15)
    assert focus_calls[-1]["end"].date() == date(2026, 3, 2)

    # The pre-existing day outside the window survives the daily.ndjson merge...
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    dates = {row["date"] for row in rows}
    assert "2026-01-15" in dates
    # ...and the manifest's product-wide bounds reflect the union, not just
    # this call's narrow window (downstream staleness checks read these).
    assert manifest["first_date"] == "2026-01-15"
    assert manifest["window_start"] == "2026-02-15"

    # The pre-existing day outside the window survives the merge...
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    dates = {row["date"] for row in rows}
    assert "2026-01-15" in dates
    # ...and the manifest's product-wide bounds reflect the union, not just
    # this call's narrow window (downstream staleness checks read these).
    assert manifest["first_date"] == "2026-01-15"
    assert manifest["window_start"] == "2026-02-15"


def test_materialize_activity_content_reprocessing_a_window_is_idempotent(monkeypatch, tmp_path):
    """Re-running the same window twice must not double-count title usage.

    The persistent per-day title_usage_daily table (lynchpin-d36 fix) is
    only safe if reprocessing deletes-then-reinserts exactly the requested
    days rather than accumulating on top of a prior run's contribution.
    """
    from lynchpin.ingest import activity_content_materialize
    from lynchpin.sources.activitywatch import FocusSpan

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    monkeypatch.setattr(
        "lynchpin.sources.title_metadata_rules.classify_title_via_rules",
        lambda *args, **kwargs: None,
    )

    def fake_focus_spans(**kwargs):
        from datetime import datetime, timezone

        yield FocusSpan(
            start=datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 2, 15, 10, 1, tzinfo=timezone.utc),
            kind="focused",
            app="kitty",
            title="weechat",
            mode=None,
            project=None,
        )

    monkeypatch.setattr(activity_content_materialize, "focus_spans", fake_focus_spans)

    window = dict(start=date(2026, 2, 15), end=date(2026, 2, 16), output=output)
    activity_content_materialize.materialize_activity_content(**window)
    first_run = json.loads(output.read_text(encoding="utf-8").strip())

    activity_content_materialize.materialize_activity_content(**window)
    second_run = json.loads(output.read_text(encoding="utf-8").strip())

    assert first_run["focused_seconds"] == second_run["focused_seconds"]
