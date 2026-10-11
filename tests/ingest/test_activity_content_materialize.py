from __future__ import annotations

import json
from datetime import date

import pytest


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
    from lynchpin.ingest.activity_content_materialize import ACTIVITY_CONTENT_SCHEMA_VERSION

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
        json.dumps({"first_date": day, "last_date": day, "schema_version": ACTIVITY_CONTENT_SCHEMA_VERSION}) + "\n",
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
    with activity_content_materialize._TitleUsageStore(facts_path) as store:
        store.ensure_row(title_hash="fixture", app="fixture", normalized_title="fixture",
                         example_title="fixture", classification=None)
        store.add(title_hash="fixture", app="fixture", day=date(2026, 1, 15), seconds=120.0)

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


def test_failed_title_usage_replacement_rolls_back_existing_facts(tmp_path):
    import pytest
    from lynchpin.ingest.activity_content_materialize import _TitleUsageStore

    path = tmp_path / "title_usage.facts.sqlite3"
    store = _TitleUsageStore(path)
    store.ensure_row(title_hash="fixture", app="fixture", normalized_title="neutral",
                     example_title="neutral", classification=None)
    store.add(title_hash="fixture", app="fixture", day=date(2026, 1, 1), seconds=60)
    store.close()
    with pytest.raises(RuntimeError, match="source failed"):
        with _TitleUsageStore(path) as replacement:
            replacement.reset_window(date(2026, 1, 1), date(2026, 1, 2))
            replacement.commit()
            raise RuntimeError("source failed")
    retained = _TitleUsageStore(path)
    try:
        assert list(retained.iter_rows())[0]["focused_seconds"] == 60
    finally:
        retained.close()


def test_corrupt_usage_export_migrates_from_retained_facts(tmp_path):
    from lynchpin.ingest.activity_content_materialize import _TitleUsageStore, _migrate_usage_store
    from lynchpin.materializers.partition_store import ArtifactStore

    output = tmp_path / "title_usage.ndjson"
    output.write_text('{"incomplete":')
    store = _TitleUsageStore(output.with_name("title_usage.facts.sqlite3"))
    store.ensure_row(title_hash="fixture", app="fixture", normalized_title="neutral",
                     example_title="neutral", classification=None)
    store.add(title_hash="fixture", app="fixture", day=date(2026, 1, 1), seconds=60)
    store.close()
    artifacts = ArtifactStore(tmp_path / "usage-partitions")
    _migrate_usage_store(artifacts, output)
    rows = [json.loads(line) for ref in artifacts.logical_partitions().values()
            for line in artifacts.read(ref).decode().splitlines()]
    assert len(rows) == 1
    assert rows[0]["focused_seconds"] == 60


@pytest.fixture
def content_refresh(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from lynchpin.ingest import activity_content_materialize as materializer
    from lynchpin.sources import activity_content
    from lynchpin.sources.activitywatch import FocusSpan

    derived = tmp_path / "derived"
    output = derived / "activity_content" / "daily.ndjson"
    events = tmp_path / "events.ndjson"
    events.write_text("{}\n")
    monkeypatch.setattr(materializer, "activity_content_input_files", lambda: (events,))
    monkeypatch.setattr(materializer, "load_title_classification_map", lambda: {})
    monkeypatch.setattr(activity_content, "get_config", lambda: SimpleNamespace(derived_root=derived))
    seconds = {date(2026, 1, 1): 60, date(2026, 1, 2): 60}

    def spans(*, start, end, **kwargs):
        for day, duration in sorted(seconds.items()):
            timestamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
            if start <= timestamp < end:
                yield FocusSpan(start=timestamp, end=timestamp + timedelta(seconds=duration),
                                kind="focused", app="fixture", title="fixture", mode=None, project=None)

    monkeypatch.setattr(materializer, "focus_spans", spans)

    def refresh(start=date(2026, 1, 1), end=date(2026, 1, 3)):
        return materializer.materialize_activity_content(start=start, end=end, output=output)

    def totals():
        import sqlite3
        daily = sum(row.focused_seconds for row in activity_content.iter_activity_content_days(ensure=False))
        usage = sum(row.focused_seconds for row in activity_content.iter_activity_title_usage(ensure=False))
        with sqlite3.connect(output.parent / "title_usage.facts.sqlite3") as connection:
            facts = connection.execute("SELECT SUM(focused_seconds) FROM title_usage_daily").fetchone()[0]
        manifest = json.loads(output.with_suffix(".manifest.json").read_text())
        return daily, usage, facts, manifest["focused_seconds"]

    return materializer, output, seconds, refresh, totals


@pytest.mark.parametrize("stage", ["daily", "title", "usage_selection", "daily_selection", "manifest", "facts_commit", "pointer_before", "pointer_after"])
def test_activity_content_interrupted_bundle_is_complete(content_refresh, monkeypatch, stage):
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    assert totals() == (120, 120, 120, 120)
    seconds[date(2026, 1, 2)] = 120

    with monkeypatch.context() as patch:
        if stage in {"daily", "title"}:
            original = materializer.atomic_write_ndjson
            def interrupt(path, rows):
                original(path, rows)
                if path.name == ("daily.ndjson" if stage == "daily" else "title_usage.ndjson"):
                    raise RuntimeError("injected publication interruption")
            patch.setattr(materializer, "atomic_write_ndjson", interrupt)
        elif stage in {"usage_selection", "daily_selection"}:
            original = materializer.ArtifactStore.publish
            def interrupt(store, *args, **kwargs):
                result = original(store, *args, **kwargs)
                if store.root.name == (".title_usage.partitions" if stage == "usage_selection" else ".daily.partitions"):
                    raise RuntimeError("injected publication interruption")
                return result
            patch.setattr(materializer.ArtifactStore, "publish", interrupt)
        elif stage == "manifest":
            original = materializer.write_manifest
            def interrupt(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected publication interruption")
            patch.setattr(materializer, "write_manifest", interrupt)
        elif stage == "facts_commit":
            original = materializer._TitleUsageStore.__exit__
            def interrupt(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected publication interruption")
            patch.setattr(materializer._TitleUsageStore, "__exit__", interrupt)
        else:
            original = materializer._select_bundle
            def interrupt(*args, **kwargs):
                if stage == "pointer_after":
                    original(*args, **kwargs)
                raise RuntimeError("injected publication interruption")
            patch.setattr(materializer, "_select_bundle", interrupt)
        with pytest.raises(RuntimeError, match="injected"):
            refresh(start=date(2026, 1, 2))
    expected = 180 if stage == "pointer_after" else 120
    assert totals() == (expected,) * 4
    assert len(list((output.parent / ".daily.generations").glob("gen-*"))) == 2
    refresh(start=date(2026, 1, 2))
    assert totals() == (180,) * 4


def test_failed_bootstrap_does_not_turn_file_creation_into_completion(content_refresh, monkeypatch):
    materializer, output, seconds, refresh, totals = content_refresh
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps({"date": "2026-01-01", "focused_seconds": 60,
                                 "matched_seconds": 0, "source_counts": {}}) + "\n")
    output.with_suffix(".manifest.json").write_text(json.dumps({"schema_version": materializer.ACTIVITY_CONTENT_SCHEMA_VERSION}))
    original = materializer.focus_spans
    with monkeypatch.context() as patch:
        def fail(**kwargs):
            raise RuntimeError("source unavailable")
        patch.setattr(materializer, "focus_spans", fail)
        with pytest.raises(RuntimeError, match="source unavailable"):
            refresh(start=date(2026, 1, 2))
    assert not (output.parent / "title_usage.facts.sqlite3").exists()
    monkeypatch.setattr(materializer, "focus_spans", original)
    refresh(start=date(2026, 1, 2))
    assert totals() == (120,) * 4


def test_activity_content_rejects_changed_inputs_before_selection(content_refresh, monkeypatch):
    from lynchpin.core.errors import MaterializationError
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    original = materializer.focus_spans
    def change(**kwargs):
        yield from original(**kwargs)
        materializer.activity_content_input_files()[0].write_text("changed input\n")
    monkeypatch.setattr(materializer, "focus_spans", change)
    with pytest.raises(MaterializationError, match="inputs changed"):
        refresh(start=date(2026, 1, 2))
    assert totals() == (120,) * 4


def test_compound_content_reader_keeps_one_generation(content_refresh):
    from lynchpin.sources import activity_content
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    daily, usage = activity_content.activity_content_bundle_paths()
    seconds[date(2026, 1, 2)] = 120
    refresh(start=date(2026, 1, 2))
    assert totals() == (180,) * 4
    assert sum(row.focused_seconds for row in activity_content.iter_activity_content_days(daily)) == 120
    assert sum(row.focused_seconds for row in activity_content.iter_activity_title_usage(usage)) == 120


def test_unchanged_unbounded_content_reuses_selection(content_refresh, monkeypatch):
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    root = output.parent / ".daily.generations"
    before = {path.name for path in root.iterdir()}
    def fail(**kwargs):
        raise AssertionError("warm reuse must not acquire inputs again")
    monkeypatch.setattr(materializer, "focus_spans", fail)
    manifest = materializer.materialize_activity_content(output=output)
    assert manifest["focused_seconds"] == 120
    assert {path.name for path in root.iterdir()} == before


@pytest.mark.parametrize("entrance_name", ["daily.ndjson", "daily.manifest.json", "title_usage.ndjson", "title_usage.facts.sqlite3", ".daily.partitions", ".title_usage.partitions"])
def test_legacy_adoption_keeps_entrances_readable(content_refresh, monkeypatch, entrance_name, tmp_path):
    from types import SimpleNamespace
    from lynchpin.sources import activity_content
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    legacy = tmp_path / "legacy" / "activity_content"
    materializer._copy_bundle(output.resolve().parent, legacy, materializer._bundle_names(output))
    monkeypatch.setattr(activity_content, "get_config", lambda: SimpleNamespace(derived_root=legacy.parent))
    original = materializer.os.replace
    with monkeypatch.context() as patch:
        def interrupt(source, destination):
            if destination.name.startswith("preimage-") and destination.name.endswith("-" + entrance_name):
                assert sum(row.focused_seconds for row in activity_content.iter_activity_content_days(ensure=False)) == 120
                assert sum(row.focused_seconds for row in activity_content.iter_activity_title_usage(ensure=False)) == 120
                raise RuntimeError("injected adoption interruption")
            return original(source, destination)
        patch.setattr(materializer.os, "replace", interrupt)
        with pytest.raises(RuntimeError, match="adoption interruption"):
            materializer.materialize_activity_content(start=date(2026, 1, 2), end=date(2026, 1, 3), output=legacy / output.name)
    materializer.materialize_activity_content(start=date(2026, 1, 2), end=date(2026, 1, 3), output=legacy / output.name)
    assert sum(row.focused_seconds for row in activity_content.iter_activity_content_days(ensure=False)) == 120
    assert sum(row.focused_seconds for row in activity_content.iter_activity_title_usage(ensure=False)) == 120


def test_daily_rounding_does_not_reject_complete_facts(content_refresh):
    materializer, output, seconds, refresh, totals = content_refresh
    for day in range(1, 4):
        seconds[date(2026, 1, day)] = 60.0004
    refresh(end=date(2026, 1, 4))
    daily, usage, facts, manifest = totals()
    assert daily == manifest == 180
    assert usage == pytest.approx(180.001)
    assert facts == pytest.approx(180.0012)


def test_bootstrap_checks_fact_days_not_only_the_grand_total(content_refresh):
    import sqlite3
    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    with sqlite3.connect(output.parent / "title_usage.facts.sqlite3") as connection:
        connection.execute("DELETE FROM title_usage_daily WHERE day = '2026-01-01'")
        connection.execute("UPDATE title_usage_daily SET focused_seconds = 120 WHERE day = '2026-01-02'")
    refresh(start=date(2026, 1, 2))
    assert totals() == (120,) * 4


def test_retired_temporal_bundle_requires_full_rebuild(content_refresh, monkeypatch):
    from lynchpin.core.errors import MaterializationError

    materializer, output, seconds, refresh, totals = content_refresh
    refresh()
    manifest_path = output.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = materializer.ACTIVITY_CONTENT_SCHEMA_VERSION - 1
    manifest_path.write_text(json.dumps(manifest))
    old_output = output.read_bytes()
    old_manifest = manifest_path.read_bytes()
    old_selection = output.resolve().parent
    monkeypatch.setattr(materializer, "_default_window", lambda start, end: (date(2026, 1, 1), date(2026, 1, 3)))
    with pytest.raises(MaterializationError, match="unbounded rebuild"):
        refresh()
    assert output.resolve().parent == old_selection
    assert output.read_bytes() == old_output
    assert manifest_path.read_bytes() == old_manifest
    with monkeypatch.context() as patch:
        def fail(**kwargs):
            raise RuntimeError("injected acquisition failure")
        patch.setattr(materializer, "focus_spans", fail)
        with pytest.raises(RuntimeError, match="acquisition failure"):
            materializer.materialize_activity_content(output=output)
    assert output.resolve().parent == old_selection
    assert output.read_bytes() == old_output
    assert manifest_path.read_bytes() == old_manifest
    seconds[date(2026, 1, 2)] = 120
    result = materializer.materialize_activity_content(output=output)
    assert result["schema_version"] == materializer.ACTIVITY_CONTENT_SCHEMA_VERSION
    assert output.resolve().parent != old_selection
    assert totals() == (180, 180, 180, 180)
