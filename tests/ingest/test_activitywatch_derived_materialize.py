from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_atuin_day_identity_ignores_rewrite_but_detects_payload_change(tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.core.cache import input_versions
    path = tmp_path / "history.ndjson"
    rows = [{"timestamp": "2026-01-04T10:00:00+00:00", "command": "fixture"}]
    path.write_text(json.dumps(rows[0]) + "\n")
    def versions():
        return mod._atuin_day_versions(path, json.dumps(input_versions((path,)), sort_keys=True))
    before = versions()
    path.write_text(json.dumps(rows[0], indent=0).replace("\n", "") + "\n")
    assert versions() == before
    path.write_text(json.dumps({**rows[0], "command": "changed"}) + "\n")
    assert versions() != before
    assert versions()[1]["date"] == "2026-01-04"


def test_changed_atuin_day_preserves_only_proven_unaffected_dates(monkeypatch):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    old = {"path": "/synthetic/history.ndjson", "source": "atuin", "date": "2026-01-04", "sha256": "a" * 64}
    new = {**old, "sha256": "b" * 64}
    previous = {"covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], "repair_input_versions": [old]}
    monkeypatch.setattr(mod, "_atuin_affected_dates", lambda day, retained: {day})
    assert mod._repair_reuse_is_proven(previous, [new], date(2026, 1, 4), date(2026, 1, 5))
    monkeypatch.setattr(mod, "_atuin_affected_dates", lambda day, retained: {date(2026, 1, 1), day})
    assert not mod._repair_reuse_is_proven(previous, [{**old, "sha256": "b" * 64}], date(2026, 1, 4), date(2026, 1, 5))


def test_atuin_influence_covers_long_raw_intervals(monkeypatch):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources import activitywatch_raw
    event = SimpleNamespace(start=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
                            end=datetime(2026, 1, 5, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(activitywatch_raw, "afk_events", lambda **kwargs: iter([event]))
    monkeypatch.setattr(activitywatch_raw, "window_events", lambda **kwargs: iter([]))
    retained = {date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)}
    assert retained <= mod._atuin_affected_dates(date(2026, 1, 4), retained)


def test_repair_refresh_window_includes_changed_atuin_influence(monkeypatch):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    old = {"path": "/synthetic/history.ndjson", "source": "atuin", "date": "2026-01-04", "sha256": "a" * 64, "available": True}
    previous = {"covered_dates": ["2026-01-01", "2026-01-04"], "repair_input_versions": [old]}
    monkeypatch.setattr(mod, "_load_json", lambda path: previous)
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: ())
    monkeypatch.setattr(mod, "activitywatch_repair_input_versions", lambda **kwargs: [{**old, "sha256": "b" * 64}])
    monkeypatch.setattr(mod, "_atuin_affected_dates", lambda day, retained: {date(2026, 1, 1), day})
    monkeypatch.setattr(mod, "_canonical_reuse_is_proven", lambda *args: True)
    assert mod.activitywatch_derived_refresh_window((date(2026, 1, 4), date(2026, 1, 5))) == (date(2026, 1, 1), date(2026, 1, 5))
    previous["repair_input_versions"] = [{"path": old["path"], "source": "atuin", "stat": [1]}]
    assert not mod._repair_reuse_is_proven(previous, [{**old, "sha256": "b" * 64}], date(2026, 1, 4), date(2026, 1, 5))


@pytest.mark.parametrize("start, expected", [(date(2026, 1, 3), True), (date(2026, 1, 4), False)])
def test_repair_reuse_bounds_newly_closed_keyboard_file(start, expected):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    old = {"path": "/synthetic/2026-01-04.jsonl", "source": "keylog", "date": "2026-01-04", "stat": [1]}
    current = [{**old, "stat": [2]}]
    previous = {"covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], "repair_input_versions": [old]}
    assert mod._repair_reuse_is_proven(previous, current, start, date(2026, 1, 5)) is expected
    assert not mod._repair_reuse_is_proven(previous, [{**old, "source": "sleep", "stat": [2]}], start, date(2026, 1, 5))


def test_canonical_reuse_requires_changed_partition_suffix(monkeypatch):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: Path("/synthetic/manifest.json"))
    old = {"path": "/synthetic/2026-01-03.ndjson", "stat": [1]}
    previous = {"covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], "input_versions": [old]}
    current = [{**old, "stat": [2]}]
    assert mod._canonical_reuse_is_proven(previous, current, date(2026, 1, 2), date(2026, 1, 5))
    assert not mod._canonical_reuse_is_proven(previous, current, date(2026, 1, 3), date(2026, 1, 4))


def test_derived_inputs_follow_canonical_index_not_live_database(monkeypatch, tmp_path):
    from lynchpin.core.cache import input_versions
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources import activitywatch_event_index as index

    manifest = index.activitywatch_event_index_manifest_path(tmp_path)
    manifest.parent.mkdir(parents=True)
    partition = manifest.parent / "generations" / "first" / "2026-06-06.ndjson"
    partition.parent.mkdir(parents=True)
    partition.write_text("{}\n", encoding="utf-8")
    manifest.write_text(json.dumps({"product_paths": {"2026-06-06": str(partition)}}))
    native = tmp_path / "native.db"
    native.write_bytes(b"original")
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: manifest)

    inputs = mod.activitywatch_derived_input_files()
    assert manifest in inputs
    assert partition in inputs
    assert native not in inputs
    versions = input_versions(inputs)
    native.write_bytes(b"unrelated live change")
    assert input_versions(mod.activitywatch_derived_input_files()) == versions
    partition.write_text('{"changed": true}\n', encoding="utf-8")
    assert input_versions(mod.activitywatch_derived_input_files()) != versions
    partition.unlink()
    assert partition in mod.activitywatch_derived_input_files()
    replacement = partition.parent.parent / "second" / partition.name
    replacement.parent.mkdir()
    replacement.write_text("{}\n", encoding="utf-8")
    manifest.write_text(json.dumps({"product_paths": {"2026-06-06": str(replacement)}}))
    assert replacement in mod.activitywatch_derived_input_files()
    assert partition not in mod.activitywatch_derived_input_files()
    assert input_versions(mod.activitywatch_derived_input_files()) != versions


def test_activitywatch_derived_repair_inputs_record_optional_absence_and_arrival(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources import keylog, terminal

    sleep = tmp_path / "sleep.jsonl"
    shell = tmp_path / "history.ndjson"
    keyboard = tmp_path / "keyboard.jsonl"
    monkeypatch.setattr(mod, "get_config", lambda: SimpleNamespace(sleep_jsonl=sleep))
    monkeypatch.setattr(terminal, "canonical_atuin_history_path", lambda: shell)
    monkeypatch.setattr(keylog, "log_files", lambda **kwargs: [keyboard] if keyboard.exists() else [])
    absent = mod.activitywatch_repair_input_versions()
    assert len(absent) == 2
    assert all(item["stat"] is None for item in absent)
    assert mod.activitywatch_repair_input_versions() == absent
    for path in (sleep, shell, keyboard):
        before = mod.activitywatch_repair_input_versions()
        path.write_text('{"timestamp":"2026-01-04T10:00:00+00:00","command":"fixture"}\n' if path == shell else "{}\n")
        assert mod.activitywatch_repair_input_versions() != before


def test_materialize_activitywatch_derived_writes_graph_products(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.ingest.activitywatch_derived_materialize import ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    start = datetime(2026, 6, 6, 8, tzinfo=timezone.utc)
    end = datetime(2026, 6, 6, 9, tzinfo=timezone.utc)
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    ensure_calls = []
    monkeypatch.setattr(
        mod,
        "focus_spans",
        lambda **kwargs: (ensure_calls.append(("focus_spans", kwargs["ensure"])) or (
            SimpleNamespace(
                start=start,
                end=end,
                kind="focused",
                app="kitty",
                title="lynchpin",
                mode="coding",
                project="lynchpin",
                duration_s=3600.0,
                keypress_count=3,
                keylog_state="available",
            ),
        )),
    )
    monkeypatch.setattr(
        mod,
        "project_focus_days",
        lambda **kwargs: (ensure_calls.append(("project_focus_days", kwargs["ensure"])) or (
            SimpleNamespace(date=date(2026, 6, 6), project="lynchpin", duration_s=3600.0),
        )),
    )
    monkeypatch.setattr(
        mod,
        "daily_activity",
        lambda **kwargs: (ensure_calls.append(("daily_activity", kwargs["ensure"])) or (
            SimpleNamespace(
                date=date(2026, 6, 6),
                active_hours=1.0,
                deep_work_min=30.0,
                fragmentation_score=0.2,
                project_count=1,
                dominant_mode="coding",
                dominant_project="lynchpin",
                hourly_active=tuple([0.0] * 8 + [60.0] + [0.0] * 15),
                outage_hours=0.0,
                presence_active_hours=1.0,
                presence_typing_hours=0.5,
                presence_data_gap_hours=0.0,
            ),
        )),
    )
    for name in ("deep_work", "circadian", "loops", "fragmentation", "attention"):
        monkeypatch.setattr(mod, name, lambda _name=name, **kwargs: (ensure_calls.append((_name, kwargs["ensure"])) or ()))

    manifest = mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    assert set(manifest["product_paths"]) == set(PRODUCT_KINDS)
    assert manifest["schema_version"] == ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    assert manifest["row_counts"]["focus_spans"] == 1
    assert manifest["row_counts"]["project_focus_days"] == 1
    assert manifest["row_counts"]["daily_activity"] == 1
    assert manifest["row_count"] == 3
    assert manifest["window_semantics"] == "start inclusive, end exclusive"
    assert manifest["covered_dates"] == ["2026-06-06"]
    assert manifest["covered_date_count"] == 1
    assert all(
        Path(path).exists()
        for paths in manifest["product_paths"].values()
        for path in paths.values()
    )
    assert (tmp_path / "activitywatch/graph/manifest.json").exists()
    assert ensure_calls == [
        ("focus_spans", False),
        ("project_focus_days", False),
        ("daily_activity", False),
        ("deep_work", False),
        ("circadian", False),
        ("loops", False),
        ("fragmentation", False),
        ("attention", False),
    ]


def test_materialize_repairs_missing_partition_only_inside_requested_window(
    monkeypatch, tmp_path
):
    from lynchpin.core.errors import MaterializationError
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    def seed_missing(root: Path, day: date) -> Path:
        manifest_path = root / "activitywatch/graph/manifest.json"
        manifest_path.parent.mkdir(parents=True)
        missing = (
            root
            / "activitywatch/graph/generations/generation-old/daily_activity"
            / f"{day.isoformat()}.ndjson"
        )
        product_paths = {kind: {} for kind in PRODUCT_KINDS}
        product_counts = {kind: {} for kind in PRODUCT_KINDS}
        product_paths["daily_activity"][day.isoformat()] = str(missing)
        product_counts["daily_activity"][day.isoformat()] = 1
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": mod.ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
                    "product_paths": product_paths,
                    "partition_row_counts": product_counts,
                    "row_count": 1,
                        "covered_dates": [day.isoformat()],
                        "closed_repair_input_versions": mod.activitywatch_repair_input_versions(closed_only=True),
                }
            ),
            encoding="utf-8",
        )
        return manifest_path

    for name in (
        "focus_spans",
        "project_focus_days",
        "deep_work",
        "circadian",
        "loops",
        "fragmentation",
        "attention",
    ):
        monkeypatch.setattr(mod, name, lambda **_kwargs: ())

    def daily_activity(*, start, **_kwargs):
        return (
            SimpleNamespace(
                date=start,
                active_hours=1.0,
                deep_work_min=30.0,
                fragmentation_score=0.2,
                project_count=1,
                dominant_mode="coding",
                dominant_project="synthetic",
                hourly_active=(0.0,) * 24,
                outage_hours=0.0,
                presence_active_hours=1.0,
                presence_typing_hours=0.5,
                presence_data_gap_hours=0.0,
            ),
        )

    monkeypatch.setattr(mod, "daily_activity", daily_activity)
    monkeypatch.setattr(mod, "_clip_to_real_coverage", lambda start, end: (start, end))

    day = date(2026, 6, 6)
    inside_root = tmp_path / "inside"
    seed_missing(inside_root, day)
    inside_input = inside_root / "events.ndjson"
    inside_input.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        mod, "activitywatch_derived_input_files", lambda: (inside_input,)
    )
    repaired = mod.materialize_activitywatch_derived(
        start=day, end=day + timedelta(days=1), root=inside_root
    )
    repaired_path = Path(repaired["product_paths"]["daily_activity"][day.isoformat()])
    assert repaired_path.is_file()
    repaired_row = json.loads(repaired_path.read_text(encoding="utf-8"))
    assert repaired_row["dominant_project"] == "synthetic"

    outside_root = tmp_path / "outside"
    seed_missing(outside_root, day)
    outside_input = outside_root / "events.ndjson"
    outside_input.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        mod, "activitywatch_derived_input_files", lambda: (outside_input,)
    )
    with pytest.raises(MaterializationError, match="daily_activity/2026-06-06"):
        mod.materialize_activitywatch_derived(
            start=day + timedelta(days=1),
            end=day + timedelta(days=2),
            root=outside_root,
        )


def test_materialize_activitywatch_derived_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    old_path = tmp_path / "activitywatch/graph/project_focus_days.ndjson"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(
        "\n".join(
            [
                '{"date": "2026-06-05", "duration_s": 111.0, "project": "old"}',
                '{"date": "2026-06-06", "duration_s": 222.0, "project": "replace"}',
                '{"date": "2026-06-07", "duration_s": 333.0, "project": "future"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    monkeypatch.setattr(mod, "focus_spans", lambda **kwargs: ())
    monkeypatch.setattr(
        mod,
        "project_focus_days",
        lambda **kwargs: (
            SimpleNamespace(date=date(2026, 6, 6), project="new", duration_s=444.0),
        ),
    )
    monkeypatch.setattr(mod, "daily_activity", lambda **kwargs: ())
    monkeypatch.setattr(mod, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(mod, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(mod, "loops", lambda **kwargs: ())
    monkeypatch.setattr(mod, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(mod, "attention", lambda **kwargs: ())

    manifest = mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    # The legacy monolith is a recovery carrier and remains untouched. The
    # newly published manifest points each logical day at immutable files.
    legacy_rows = [line for line in old_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any('"project": "replace"' in row for row in legacy_rows)
    paths = manifest["product_paths"]["project_focus_days"]
    rows = [
        json.loads(line)
        for path in paths.values()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["project"] for row in rows} == {"old", "new", "future"}


def test_materialize_activitywatch_derived_preserves_sparse_covered_dates(monkeypatch, tmp_path):
    from lynchpin.core.cache import input_versions
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    manifest_path = tmp_path / "activitywatch/graph/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"schema_version": mod.ACTIVITYWATCH_DERIVED_SCHEMA_VERSION, "covered_dates": ["2026-06-05", "2026-06-07"], "first_date": "2026-06-05", "last_date": "2026-06-07", "closed_repair_input_versions": mod.activitywatch_repair_input_versions(closed_only=True)}),
        encoding="utf-8",
    )
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    seeded = mod._load_json(manifest_path)
    seeded["input_versions"] = input_versions((canonical,))
    manifest_path.write_text(json.dumps(seeded))

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    monkeypatch.setattr(mod, "focus_spans", lambda **kwargs: ())
    monkeypatch.setattr(mod, "project_focus_days", lambda **kwargs: ())
    monkeypatch.setattr(mod, "daily_activity", lambda **kwargs: ())
    monkeypatch.setattr(mod, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(mod, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(mod, "loops", lambda **kwargs: ())
    monkeypatch.setattr(mod, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(mod, "attention", lambda **kwargs: ())

    manifest = mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["first_date"] == "2026-06-05"
    assert manifest["last_date"] == "2026-06-07"
    assert manifest["row_count"] == 0


def test_materialize_activitywatch_derived_chunked_matches_single_pass(monkeypatch, tmp_path):
    """chunk_days must bound each generator window without changing the output."""
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    from datetime import timedelta

    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))

    seen_windows: list[tuple[date, date]] = []

    def fake_daily_activity(*, start, end, ensure=True):
        seen_windows.append((start, end))
        day = start
        while day <= end:
            yield SimpleNamespace(
                date=day,
                active_hours=1.0,
                deep_work_min=30.0,
                fragmentation_score=0.2,
                project_count=1,
                dominant_mode="coding",
                dominant_project="lynchpin",
                hourly_active=tuple([0.0] * 24),
                outage_hours=0.0,
                presence_active_hours=1.0,
                presence_typing_hours=0.5,
                presence_data_gap_hours=0.0,
            )
            day += timedelta(days=1)

    for name in ("focus_spans", "project_focus_days", "deep_work", "circadian", "loops", "fragmentation", "attention"):
        monkeypatch.setattr(mod, name, lambda **kwargs: ())
    monkeypatch.setattr(mod, "daily_activity", fake_daily_activity)

    start, end = date(2026, 6, 1), date(2026, 6, 8)

    single_root = tmp_path / "single"
    chunked_root = tmp_path / "chunked"
    single = mod.materialize_activitywatch_derived(start=start, end=end, root=single_root, chunk_days=365)
    seen_single = list(seen_windows)
    seen_windows.clear()
    chunked = mod.materialize_activitywatch_derived(start=start, end=end, root=chunked_root, chunk_days=2)

    assert len(seen_single) == 1
    assert len(seen_windows) == 4  # ceil(7 / 2)
    for w_start, w_end in seen_windows:
        assert (w_end - w_start).days <= 2

    for kind in PRODUCT_KINDS:
        single_rows = [
            line
            for path in single["product_paths"][kind].values()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        chunked_rows = [
            line
            for path in chunked["product_paths"][kind].values()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert chunked_rows == single_rows

    for key in ("row_counts", "row_count", "covered_dates", "first_date", "last_date"):
        assert chunked[key] == single[key]
    assert chunked["window_start"] == "2026-06-01"
    assert single["window_start"] == "2026-06-01"


def test_materialize_activitywatch_derived_default_chunk_bounds_generators(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))

    seen_windows: list[tuple[date, date]] = []

    def fake_daily_activity(*, start, end, ensure=True):
        seen_windows.append((start, end))
        return ()

    for name in ("focus_spans", "project_focus_days", "deep_work", "circadian", "loops", "fragmentation", "attention"):
        monkeypatch.setattr(mod, name, lambda **kwargs: ())
    monkeypatch.setattr(mod, "daily_activity", fake_daily_activity)

    mod.materialize_activitywatch_derived(
        start=date(2026, 6, 1),
        end=date(2026, 7, 1),
        root=tmp_path,
    )

    assert len(seen_windows) == 30
    assert all((end - start).days <= mod.DEFAULT_CHUNK_DAYS for start, end in seen_windows)


def test_materialize_activitywatch_derived_clips_window_to_real_coverage(monkeypatch, tmp_path):
    """lynchpin-3yp: an ensure-cascade window reaching before AW's real
    coverage (e.g. temporal_signals asking for its own 2013-era history)
    must be clipped to the canonical product's real span before generators
    ever see it — otherwise they compute "outages" for days AW never ran.
    """
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    canonical = tmp_path / "canonical-events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")
    canonical_manifest = canonical.with_suffix(".manifest.json")
    real_first, real_last = date(2024, 2, 15), date(2026, 7, 12)
    canonical_manifest.write_text(
        json.dumps({"first_date": real_first.isoformat(), "last_date": real_last.isoformat()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))

    seen_windows: list[tuple[date, date]] = []

    def record(**kwargs):
        seen_windows.append((kwargs.get("start"), kwargs.get("end")))
        return ()

    for name in (
        "focus_spans", "project_focus_days", "daily_activity", "deep_work",
        "circadian", "loops", "fragmentation", "attention",
    ):
        monkeypatch.setattr(mod, name, record)

    # Requested window reaches back to 2013 — years before real AW coverage —
    # mirroring the actual mis-scoped ensure-cascade window that produced
    # the 184 phantom rows.
    manifest = mod.materialize_activitywatch_derived(
        start=date(2013, 1, 1), end=date(2024, 3, 1), root=tmp_path / "out",
    )

    # covered_dates marks the CLIPPED window as verified, never the raw
    # pre-2024 request the ensure-cascade actually asked for.
    assert manifest["covered_dates"]
    assert min(manifest["covered_dates"]) == real_first.isoformat()

    def as_date(value):
        return value.date() if isinstance(value, datetime) else value

    for w_start, w_end in seen_windows:
        assert as_date(w_start) >= real_first
        assert as_date(w_end) <= real_last + timedelta(days=1)
    # The first generator call must reflect the clipped floor, never the raw
    # request. Later calls may begin at subsequent chunk boundaries.
    assert seen_windows
    assert as_date(seen_windows[0][0]) == real_first


def test_materialize_activitywatch_derived_noop_when_window_entirely_before_coverage(monkeypatch, tmp_path):
    """A request wholly before real AW coverage must not touch the generators."""
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    canonical = tmp_path / "canonical-events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")
    canonical_manifest = canonical.with_suffix(".manifest.json")
    canonical_manifest.write_text(
        json.dumps({"first_date": "2024-02-15", "last_date": "2026-07-12"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)

    called = False

    def fail_if_called(**kwargs):
        nonlocal called
        called = True
        return ()

    for name in (
        "focus_spans", "project_focus_days", "daily_activity", "deep_work",
        "circadian", "loops", "fragmentation", "attention",
    ):
        monkeypatch.setattr(mod, name, fail_if_called)

    out_root = tmp_path / "out"
    import pytest
    from lynchpin.core.errors import MaterializationError

    with pytest.raises(MaterializationError):
        mod.materialize_activitywatch_derived(
            start=date(2013, 1, 1), end=date(2013, 6, 1), root=out_root,
        )

    assert not called


def test_default_window_uses_current_index_instead_of_stale_carrier(
    monkeypatch, tmp_path
):
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    carrier = tmp_path / "events.ndjson"
    carrier.with_suffix(".manifest.json").write_text(
        json.dumps({"first_date": "2026-08-01", "last_date": "2026-08-02"})
    )
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"first_date": "2026-07-01", "last_date": "2026-08-05"})
    )
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: carrier)
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: index)
    assert mod._default_window(None, None) == (date(2026, 7, 1), date(2026, 8, 6))
    index.unlink()
    assert mod._default_window(None, None) == (date(2026, 8, 1), date(2026, 8, 3))


@pytest.mark.parametrize("failure", ["exception", "input_change", "closed_repair_change", "open_repair_change", "prior_repair_change"])
def test_failed_later_chunk_preserves_published_manifest(monkeypatch, tmp_path, failure):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources.activitywatch_models import AWDayActivity

    for name in (
        "focus_spans",
        "project_focus_days",
        "deep_work",
        "circadian",
        "loops",
        "fragmentation",
        "attention",
    ):
        monkeypatch.setattr(mod, name, lambda **_kwargs: ())
    monkeypatch.setattr(mod, "_clip_to_real_coverage", lambda start, end: (start, end))
    source = tmp_path / "source.ndjson"
    source.write_text("{}\n")
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (source,))
    if failure.endswith("repair_change"):
        from lynchpin.core.cache import input_versions
        monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: ())
        monkeypatch.setattr(
            mod, "activitywatch_repair_input_versions",
            lambda closed_only=False: [] if closed_only and failure == "open_repair_change" else [
                {**item, "source": "keylog", "date": datetime.now(timezone.utc).date().isoformat()}
                if failure == "open_repair_change" else item for item in input_versions((source,))
            ],
        )

    def daily(*, start, **_kwargs):
        if start == date(2026, 8, 3):
            if failure == "exception":
                raise RuntimeError("injected later chunk failure")
            source.write_text('{"changed": true}\n')
        return (
            AWDayActivity(start, 1.0, 0.0, 0.0, 1, "coding", "fixture", (0.0,) * 24),
        )

    monkeypatch.setattr(mod, "daily_activity", daily)
    mod.materialize_activitywatch_derived(
        start=date(2026, 8, 1), end=date(2026, 8, 2), root=tmp_path
    )
    manifest = mod.activitywatch_derived_manifest_path(tmp_path)
    before = manifest.read_bytes()
    if failure == "prior_repair_change":
        source.write_text('{"changed": true}\n')
        from lynchpin.core.errors import MaterializationError
        with pytest.raises(MaterializationError, match="rebuild all retained dates"):
            mod.materialize_activitywatch_derived(
                start=date(2026, 8, 2), end=date(2026, 8, 4), root=tmp_path
            )
        assert manifest.read_bytes() == before
        return
    if failure == "open_repair_change":
        result = mod.materialize_activitywatch_derived(
            start=date(2026, 8, 2), end=date(2026, 8, 4), root=tmp_path
        )
        assert manifest.read_bytes() != before
        assert result["repair_input_versions"] != mod.activitywatch_repair_input_versions()
        return
    from lynchpin.core.errors import MaterializationError
    error = RuntimeError if failure == "exception" else MaterializationError
    message = "later chunk failure" if failure == "exception" else "inputs changed"
    with pytest.raises(error, match=message):
        mod.materialize_activitywatch_derived(
            start=date(2026, 8, 2), end=date(2026, 8, 4), root=tmp_path
        )
    assert manifest.read_bytes() == before


def test_datetime_generators_cover_complete_logical_day(monkeypatch, tmp_path):
    from zoneinfo import ZoneInfo
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources.activitywatch_models import FocusSpan
    monkeypatch.setenv("TZ", "Europe/Warsaw")
    zone = ZoneInfo("Europe/Warsaw")
    start = date(2026, 5, 1)
    end = start + timedelta(days=1)
    tail = datetime(2026, 5, 2, 3, tzinfo=zone)
    seen = {}
    def focus(**kwargs):
        seen["focus_spans"] = (kwargs["start"], kwargs["end"])
        if kwargs["start"] <= tail < kwargs["end"]:
            return [FocusSpan(tail, tail + timedelta(hours=1), "focused", "editor", "fixture", None, None)]
        return []
    monkeypatch.setattr(mod, "focus_spans", focus)
    for name in ("project_focus_days", "deep_work", "loops"):
        def capture(_name=name, **kwargs):
            seen[_name] = (kwargs["start"], kwargs["end"])
            return []
        monkeypatch.setattr(mod, name, capture)
    for name in ("daily_activity", "circadian", "fragmentation", "attention"):
        monkeypatch.setattr(mod, name, lambda **kwargs: [])
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: ())
    monkeypatch.setattr(mod, "_clip_to_real_coverage", lambda start, end: (start, end))
    manifest = mod.materialize_activitywatch_derived(start=start, end=end, root=tmp_path)
    assert manifest["row_counts"]["focus_spans"] == 1
    assert all(lower.hour == upper.hour == 6 for lower, upper in seen.values())
    assert all(lower.date() == start and upper.date() == end for lower, upper in seen.values())


def test_retired_logical_day_product_refuses_partial_reuse(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.core.errors import MaterializationError
    manifest = mod.activitywatch_derived_manifest_path(tmp_path)
    manifest.parent.mkdir(parents=True)
    original = json.dumps({"schema_version": mod.ACTIVITYWATCH_DERIVED_SCHEMA_VERSION - 1, "first_date": "2026-05-01", "last_date": "2026-05-02"})
    manifest.write_text(original)
    with pytest.raises(MaterializationError, match="unbounded rebuild"):
        mod.materialize_activitywatch_derived(start=date(2026, 5, 1), end=date(2026, 5, 2), root=tmp_path)
    assert manifest.read_text() == original


def test_partitioned_retired_schema_rebuild_ignores_flat_predecessor(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources.activitywatch_models import AWDayActivity
    day = date(2026, 5, 1)
    monkeypatch.setattr(mod, "_default_window", lambda *args: (day, day + timedelta(days=1)))
    monkeypatch.setattr(mod, "_clip_to_real_coverage", lambda start, end: (start, end))
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: ())
    for name in ("focus_spans", "project_focus_days", "deep_work", "circadian", "loops", "fragmentation", "attention"):
        monkeypatch.setattr(mod, name, lambda **kwargs: [])
    monkeypatch.setattr(mod, "daily_activity", lambda **kwargs: [
        AWDayActivity(day, 1.0, 0.0, 0.0, 0, "fixture", None, (0.0,) * 24)
    ])
    first = mod.materialize_activitywatch_derived(root=tmp_path)
    manifest = mod.activitywatch_derived_manifest_path(tmp_path)
    first["schema_version"] = 3
    manifest.write_text(json.dumps(first))
    flat = mod.activitywatch_derived_path("daily_activity", tmp_path)
    flat.write_text(json.dumps({"date": str(day), "active_hours": 99.0}) + "\n")
    original = flat.read_bytes()
    second = mod.materialize_activitywatch_derived(root=tmp_path)
    assert second["schema_version"] == mod.ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    assert second["row_counts"]["daily_activity"] == 1
    assert flat.read_bytes() == original


def test_candidate_reads_one_input_snapshot_through_chunks_and_promotion(monkeypatch, tmp_path):
    from lynchpin.core.cache import input_versions
    from lynchpin.core.input_snapshot import input_snapshot, read_input_path
    from lynchpin.core.source import read_jsonl_with
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources import activitywatch_event_index as index, keylog, terminal

    first = date(2026, 6, 6)
    pointer = tmp_path / "index.json"
    partition = tmp_path / "retained.ndjson"
    partition.write_text(json.dumps({"bucket": "aw-window-test", "start": "2026-06-06T07:00:00Z", "end": "2026-06-06T08:00:00Z", "data": {"title": "old"}}) + "\n")
    original = {"schema_version": 2, "transform_recipe": index.activitywatch_event_index_recipe(), "first_date": str(first), "last_date": "2026-06-07", "product_paths": {str(first): str(partition)}}
    pointer.write_text(json.dumps(original))
    shell = tmp_path / "shell.ndjson"
    shell.write_text(json.dumps({"timestamp": "2026-06-06T07:00:00Z", "command": "old"}) + "\n")
    sleep = tmp_path / "sleep.jsonl"
    sleep.write_text('{"value":"old"}\n')
    logs = tmp_path / "keyboard" / "logs"
    logs.mkdir(parents=True)
    keyboard = logs / "2026-06-06.jsonl"
    keyboard.write_text('{"event":"press","ts":"2026-06-06T07:00:00Z"}\n')
    monkeypatch.setattr(mod, "get_config", lambda: SimpleNamespace(sleep_jsonl=sleep))
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=logs.parent))
    monkeypatch.setattr(terminal, "canonical_atuin_history_path", lambda: shell)
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: pointer)
    monkeypatch.setattr(index, "activitywatch_event_index_manifest_path", lambda root=None: pointer)
    expected = input_versions((pointer, partition))
    seen = []

    def chunk(*, start, **kwargs):
        assert index._index_manifest() == original
        events = list(index.iter_indexed_activitywatch_events(bucket_prefix="aw-window", start=datetime(2026, 6, 6, tzinfo=timezone.utc), end=datetime(2026, 6, 8, tzinfo=timezone.utc)))
        assert events[0].data == {"title": "old"}
        assert next(terminal.commands(ensure=False)).command == "old"
        assert list(read_jsonl_with(sleep, lambda value: value)) == [{"value": "old"}]
        assert keylog.log_files(ensure=False) == [keyboard]
        seen.append(start)
        if len(seen) == 1:
            replacement = tmp_path / "replacement.json"
            replacement.write_text(json.dumps({**original, "product_paths": {}}))
            replacement.replace(pointer)
            partition.unlink()
            shell.write_text(json.dumps({"timestamp": "2026-06-06T07:00:00Z", "command": "new"}) + "\n")
            sleep.unlink()
            (logs / "2026-06-07.jsonl").write_text('{}\n')
        return {"schema_version": mod.ACTIVITYWATCH_DERIVED_SCHEMA_VERSION, "covered_dates": [str(start)]}

    monkeypatch.setattr(mod, "_materialize_window", chunk)
    with input_snapshot():
        result = mod.materialize_activitywatch_derived(start=first, end=date(2026, 6, 8), root=tmp_path / "products")
        assert seen == [first, date(2026, 6, 7)]
        assert result["input_versions"] == expected
        assert next(terminal.commands(ensure=False)).command == "old"
        assert read_input_path(partition).is_file()
    assert not partition.exists()
    assert next(terminal.commands(ensure=False)).command == "new"


def test_transparent_refresh_expands_changed_canonical_suffix(monkeypatch):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    pointer = Path("/synthetic/manifest.json")
    old = {"path": "/synthetic/2020-01-03.ndjson", "stat": [1]}
    previous = {"covered_dates": ["2020-01-01", "2020-01-03", "2020-01-06"], "input_versions": [old]}
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: pointer)
    monkeypatch.setattr(mod, "_load_json", lambda path: previous)
    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: ())
    monkeypatch.setattr(mod, "input_versions", lambda paths: [{**old, "stat": [2]}])
    assert mod.activitywatch_derived_refresh_window((date(2020, 1, 4), date(2020, 1, 5))) == (date(2020, 1, 2), date(2020, 1, 7))
    monkeypatch.setattr(mod, "input_versions", lambda paths: [old])
    assert mod.activitywatch_derived_refresh_window((date(2020, 1, 4), date(2020, 1, 5))) == (date(2020, 1, 4), date(2020, 1, 5))


def test_keyboard_capture_handles_rollover_after_operation_start(monkeypatch, tmp_path):
    from lynchpin.core import input_snapshot as module
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.sources import keylog, terminal
    logs = tmp_path / "keyboard" / "logs"
    logs.mkdir(parents=True)
    paths = (logs / "2020-01-01.jsonl", logs / "2020-01-02.jsonl")
    for path in paths:
        path.write_bytes(b"{}\n")
    monkeypatch.setattr(mod, "get_config", lambda: SimpleNamespace(sleep_jsonl=tmp_path / "sleep"))
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=logs.parent))
    monkeypatch.setattr(terminal, "canonical_atuin_history_path", lambda: tmp_path / "shell")
    monkeypatch.setattr(mod, "activitywatch_event_index_manifest_path", lambda: tmp_path / "index")
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: tmp_path / "events")
    monkeypatch.setattr(mod, "datetime", SimpleNamespace(now=lambda tz: datetime(2020, 1, 2, tzinfo=timezone.utc)))
    copy, prefix = module._copy_file, module._copy_prefix
    def append(source):
        with source.open("ab") as stream:
            stream.write(b"{}\n")
    def growing_copy(source, target):
        copy(source, target)
        append(source)
    def growing_prefix(source, target, length):
        prefix(source, target, length)
        append(source)
    monkeypatch.setattr(module, "_copy_file", growing_copy)
    monkeypatch.setattr(module, "_copy_prefix", growing_prefix)
    monkeypatch.setattr(mod, "_materialize_activitywatch_derived", lambda **kwargs: {path.name: module.read_input_path(path).read_bytes() for path in paths})
    with module.input_snapshot() as snapshot:
        snapshot.observed_at = datetime(2020, 1, 1, 23, 59, tzinfo=timezone.utc)
        assert mod.materialize_activitywatch_derived() == {path.name: b"{}\n" for path in paths}
    assert all(path.read_bytes() == b"{}\n{}\n" for path in paths)
