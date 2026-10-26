from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from lynchpin.analysis import keylog as keylog_analysis
from lynchpin.sources import keylog


def test_keylog_analysis_parses_hyprland_binds_and_infers_adjacent_chords(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-05T10:00:00Z", "event": "press", "keycode": "KEY_125", "changed": False}),
                json.dumps({"ts": "2026-06-05T10:00:00.200Z", "event": "press", "keycode": "KEY_RETURN", "changed": False}),
                json.dumps({"ts": "2026-06-05T10:01:00Z", "event": "press", "keycode": "KEY_H", "changed": True}),
                json.dumps({"ts": "2026-06-05T10:01:01Z", "event": "press", "keycode": "KEY_BACKSPACE", "changed": True}),
                json.dumps({"ts": "2026-06-05T10:02:00Z", "event": "press", "keycode": "KEY_125", "changed": False}),
                json.dumps({"ts": "2026-06-05T10:02:00.100Z", "event": "press", "keycode": "KEY_RETURN", "changed": False}),
                json.dumps({"ts": "2026-06-05T10:01:02Z", "event": "snapshot", "buffer": "separate text product"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bindings = tmp_path / "bindings.nix"
    bindings.write_text(
        """
        { bind = [
          "SUPER, Return, exec, uwsm app -- kitty"
          "SUPER, H, exec, ${script "kitty-hypr-nav"} focus left"
        ]; }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    analysis = keylog_analysis.analyze_keylog(
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    assert analysis.source_event_count == 6
    assert analysis.keypress_count == 6
    assert analysis.matched_keybind_count == 2
    assert analysis.keybind_usage[0].chord == "SUPER+KEY_ENTER"
    assert analysis.keybind_usage[0].family == "launch"
    assert analysis.keybind_usage[0].confidence == "inferred_adjacent_modifier_press"
    assert analysis.keybind_summaries[0].chord == "SUPER+KEY_ENTER"
    assert analysis.keybind_summaries[0].total_count == 2
    assert analysis.keybind_summaries[0].active_days == 1
    assert analysis.keybind_family_summaries[0].family == "launch"
    assert analysis.keybind_family_summaries[0].total_count == 2
    assert analysis.keybind_family_summaries[0].unique_chords == 1
    assert analysis.keybind_temporal_buckets[0].chord == "SUPER+KEY_ENTER"
    assert analysis.keybind_temporal_buckets[0].family == "launch"
    assert analysis.keybind_temporal_buckets[0].weekday == 4
    assert analysis.keybind_temporal_buckets[0].hour == 12
    assert analysis.keybind_temporal_buckets[0].count == 2
    assert any(row.argument == '${script "kitty-hypr-nav"} focus left' for row in analysis.keybinds)
    assert analysis.text_shape_days[0].changed_keypress_count == 2
    assert analysis.text_shape_days[0].backspace_count == 1
    assert "separate text product" not in json.dumps(analysis.to_json())


def test_keylog_analysis_prefers_exact_modifier_state(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-05T10:00:00Z",
                        "event": "press",
                        "keycode": "KEY_RETURN",
                        "modifiers": ["SUPER"],
                        "changed": False,
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-06-05T10:01:00Z",
                        "event": "press",
                        "keycode": "KEY_125",
                        "changed": False,
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-06-05T10:01:00.100Z",
                        "event": "press",
                        "keycode": "KEY_RETURN",
                        "changed": False,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    analysis = keylog_analysis.analyze_keylog(
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    assert [(row.confidence, row.count) for row in analysis.keybind_usage] == [
        ("exact_modifier_state", 1),
        ("inferred_adjacent_modifier_press", 1),
    ]
    assert analysis.keybind_summaries[0].total_count == 2


def test_keylog_parser_extracts_inline_binds_and_interpolation_quotes(tmp_path) -> None:
    bindings = tmp_path / "bindings.nix"
    bindings.write_text(
        '{ bind = [ "SUPER, Return, exec, kitty" "SUPER, H, exec, ${script "kitty-hypr-nav"} focus left" ]; }\n'
        '# "SUPER, X, exec, ignored-comment"\n',
        encoding="utf-8",
    )

    rows = keylog_analysis.parse_hyprland_keybinds(bindings)

    assert [row.chord for row in rows] == ["SUPER+KEY_ENTER", "SUPER+KEY_H"]
    assert rows[1].argument == '${script "kitty-hypr-nav"} focus left'


def test_keylog_daily_activity_uses_logical_day_boundary(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-05T02:00:00Z", "event": "press", "keycode": "KEY_A", "changed": True}),
                json.dumps({"ts": "2026-06-05T04:00:00Z", "event": "press", "keycode": "KEY_B", "changed": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    rows = keylog.daily_activity(start=date(2026, 6, 4), end=date(2026, 6, 5))

    assert [row.keypress_count for row in rows] == [1, 1]
    assert rows[0].date == date(2026, 6, 4)
    assert rows[1].date == date(2026, 6, 5)


def test_keylog_analysis_keybind_usage_uses_logical_day_boundary(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-05T01:59:59Z", "event": "press", "keycode": "KEY_125", "changed": False}),
                json.dumps({"ts": "2026-06-05T02:00:00Z", "event": "press", "keycode": "KEY_RETURN", "changed": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    analysis = keylog_analysis.analyze_keylog(
        start=date(2026, 6, 4),
        end=date(2026, 6, 4),
        bindings_path=bindings,
    )

    assert analysis.keybind_usage[0].date == date(2026, 6, 4)
    assert analysis.text_shape_days[0].date == date(2026, 6, 4)


def test_keylog_analysis_reads_the_combined_analysis_stream_once(monkeypatch, tmp_path) -> None:
    calls = []
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")

    def fake_records(*, start, end):
        calls.append((start, end))
        return iter(())

    monkeypatch.setattr(keylog, "_analysis_records", fake_records)

    analysis = keylog_analysis.analyze_keylog(
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    assert len(calls) == 1
    assert analysis.source_event_count == 0


def test_write_keylog_analysis_saves_metadata_only(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-05T10:00:00Z", "event": "press", "keycode": "KEY_A", "changed": True}),
                json.dumps({"ts": "2026-06-05T10:01:00Z", "event": "snapshot", "text": "Secret Lynchpin words"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    reads = _record_tails(monkeypatch)
    out = tmp_path / "analysis.json"

    analysis = keylog_analysis.write_keylog_analysis(
        out,
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["dataset"] == "lynchpin.keylog_analysis"
    assert saved["schema_version"] == 1
    assert saved["input_file_count"] == 2
    assert saved["input_latest_mtime"] is not None
    assert saved["keypress_count"] == analysis.keypress_count == 1
    assert saved["keybinds"][0]["chord"] == "SUPER+KEY_ENTER"
    assert saved["text_content"]["snapshot_count"] == 1
    assert saved["text_content"]["word_count"] == 3
    # Equal counts are broken alphabetically so the artifact is reproducible.
    assert saved["text_content"]["top_terms"] == [
        {"term": "lynchpin", "count": 1},
        {"term": "secret", "count": 1},
        {"term": "words", "count": 1},
    ]
    assert reads == [("2026-06-05.jsonl", 0)]
    assert "Secret Lynchpin words" not in out.read_text(encoding="utf-8")
    assert saved["caveats"]


def test_write_keylog_analysis_retries_when_inputs_change_during_scan(tmp_path, monkeypatch) -> None:
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    original = keylog_analysis._windowed_day_counters
    scans = 0

    def drifting_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        result = original(*args, **kwargs)
        if scans == 1:
            bindings.write_text(
                '{ bind = [ "SUPER, Return, exec, kitty" ]; }\n',
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(keylog_analysis, "_windowed_day_counters", drifting_scan)
    out = tmp_path / "analysis.json"

    keylog_analysis.write_keylog_analysis(
        out,
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert scans == 2
    assert saved["input_latest_mtime"] == keylog_analysis.latest_mtime_iso(
        (bindings,)
    )


def test_keylog_text_content_analysis_uses_snapshot_text(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-05T10:00:00Z",
                        "event": "snapshot",
                        "session": "s1",
                        "buffer": "Hello hello Lynchpin\ntext",
                    }
                ),
                json.dumps({"ts": "2026-06-05T10:01:00Z", "event": "press", "keycode": "KEY_A"}),
                json.dumps(
                    {
                        "ts": "2026-06-05T10:02:00Z",
                        "event": "snapshot",
                        "session": "s1",
                        "text": "Lynchpin materializes text",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    analysis = keylog_analysis.analyze_keylog_text_content(
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        top_n=2,
    )

    assert analysis.snapshot_count == 2
    assert analysis.word_count == 7
    assert analysis.line_count == 3
    assert analysis.days[0].snapshot_count == 2
    assert [row.term for row in analysis.top_terms] == ["hello", "lynchpin"]


def _write_day(logs, day: str, keycode: str, count: int) -> None:
    (logs / f"{day}.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "ts": f"{day}T10:{index:02d}:00Z",
                    "event": "press",
                    "keycode": keycode,
                    "changed": True,
                }
            )
            for index in range(count)
        )
        + "\n",
        encoding="utf-8",
    )


def _record_tails(monkeypatch) -> list[tuple[str, int]]:
    """Record which raw files a refresh reads and from which byte offset."""
    reads: list[tuple[str, int]] = []
    original = keylog.KeylogAnalysisTail

    class RecordingTail(original):
        def __init__(self, path, offset=0):
            reads.append((path.name, offset))
            super().__init__(path, offset)

    monkeypatch.setattr(keylog, "KeylogAnalysisTail", RecordingTail)
    return reads


def test_write_keylog_analysis_reads_only_bytes_appended_since_the_last_refresh(
    tmp_path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for day in ("2026-06-05", "2026-06-06", "2026-06-07"):
        _write_day(logs, day, "KEY_A", 2)
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    out = tmp_path / "analysis.json"
    window = {"start": date(2026, 6, 5), "end": date(2026, 6, 7)}

    first = keylog_analysis.write_keylog_analysis(out, bindings_path=bindings, **window)
    assert first.keypress_count == 6

    # Only the last file grows. Its earlier bytes and every other file were
    # already folded, so rereading them is the waste this cache prevents.
    previous_size = (logs / "2026-06-07.jsonl").stat().st_size
    _write_day(logs, "2026-06-07", "KEY_A", 5)
    keylog._indexed_log_files.cache_clear()
    reads = _record_tails(monkeypatch)

    second = keylog_analysis.write_keylog_analysis(out, bindings_path=bindings, **window)

    assert reads == [("2026-06-07.jsonl", previous_size)]
    assert second.keypress_count == 9
    assert [row.keypress_count for row in second.text_shape_days] == [2, 2, 5]


def test_keylog_file_counter_cache_ignores_a_different_chord_recipe(tmp_path) -> None:
    cache = tmp_path / "counters.json"
    moment = datetime(2026, 6, 5, 10, tzinfo=UTC)
    entry = keylog_analysis._FileCounters(
        device=1, inode=2, offset=3, tail_digest="digest",
        start_modifiers={}, end_modifiers={"SUPER": moment},
        days={date(2026, 6, 5): keylog_analysis._DayCounters(source_event_count=3)},
    )
    keylog_analysis._save_file_counter_cache(
        cache, chord_window_ms=1500, chords_digest="chords", entries={"a.jsonl": entry},
    )

    assert keylog_analysis._load_file_counter_cache(
        cache, chord_window_ms=1500, chords_digest="chords"
    ) == {"a.jsonl": entry}
    assert keylog_analysis._load_file_counter_cache(cache, chord_window_ms=900, chords_digest="chords") == {}
    assert keylog_analysis._load_file_counter_cache(cache, chord_window_ms=1500, chords_digest="other") == {}


def _super_return_chords(tmp_path) -> dict[str, keylog_analysis.HyprlandKeybind]:
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    return {row.chord: row for row in keylog_analysis.parse_hyprland_keybinds(bindings)}


def _press(ts: str, keycode: str) -> str:
    return json.dumps({"ts": ts, "event": "press", "keycode": keycode, "changed": False}) + "\n"


def test_incremental_counters_match_a_full_scan_across_appends_and_file_boundaries(
    tmp_path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    by_chord = _super_return_chords(tmp_path)
    first = logs / "2026-06-05.jsonl"
    second = logs / "2026-06-06.jsonl"
    # The modifier pressed at the end of one UTC file completes a chord at the
    # start of the next, and a partially written line is completed later.
    first.write_text(_press("2026-06-05T10:00:00Z", "KEY_125") + _press("2026-06-05T23:59:59.500Z", "KEY_125"))
    partial = _press("2026-06-06T00:00:00.200Z", "KEY_RETURN")
    second.write_text(partial[:20])
    cache = tmp_path / "counters.json"
    window = {"start": date(2026, 6, 4), "end": date(2026, 6, 6)}

    def incremental():
        keylog._indexed_log_files.cache_clear()
        return keylog_analysis._windowed_day_counters(
            by_chord=by_chord, chord_window_ms=1500, cache_path=cache, **window,
        )

    def full():
        return keylog_analysis._scan_day_counters(by_chord=by_chord, chord_window_ms=1500, **window)

    incremental()
    with second.open("a") as stream:
        stream.write(partial[20:] + _press("2026-06-06T10:00:00Z", "KEY_A"))
    reads = _record_tails(monkeypatch)
    after_append = incremental()

    assert reads == [("2026-06-06.jsonl", 0)]
    expected = full()
    assert {day: value for day, value in after_append.items() if value != keylog_analysis._DayCounters()} == expected
    assert sum(sum(value.usage.values()) for value in after_append.values()) == 1


def test_incremental_counters_refold_a_rewritten_file_and_its_successor(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    first = logs / "2026-06-05.jsonl"
    second = logs / "2026-06-06.jsonl"
    first.write_text(_press("2026-06-05T12:00:00Z", "KEY_A") + _press("2026-06-05T23:59:59.500Z", "KEY_B"))
    second.write_text(_press("2026-06-06T00:00:00.200Z", "KEY_RETURN"))
    cache = tmp_path / "counters.json"
    by_chord = _super_return_chords(tmp_path)
    window = {"start": date(2026, 6, 5), "end": date(2026, 6, 6)}

    def incremental():
        keylog._indexed_log_files.cache_clear()
        return keylog_analysis._windowed_day_counters(
            by_chord=by_chord, chord_window_ms=1500, cache_path=cache, **window,
        )

    incremental()
    # A repair rewrites the first file in place: its last key becomes a
    # modifier, so the state carried into the second file changes as well.
    first.write_text(_press("2026-06-05T12:00:00Z", "KEY_A") + _press("2026-06-05T23:59:59.500Z", "KEY_125"))
    reads = _record_tails(monkeypatch)
    incremental()

    assert reads == [("2026-06-05.jsonl", 0), ("2026-06-06.jsonl", 0)]


def test_analysis_input_files_exclude_the_still_open_capture_day(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    today = keylog_analysis._open_log_date()
    closed = today - timedelta(days=1)
    for day in (closed, today):
        _write_day(logs, day.isoformat(), "KEY_A", 1)
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    files = keylog_analysis._analysis_input_files(
        start=today - timedelta(days=3), end=today + timedelta(days=1), bindings_path=bindings
    )

    # The capture appends to today's file continuously. Judging freshness
    # against it makes every artifact stale the instant it is written, so the
    # signature covers only append-complete days.
    assert logs / f"{closed.isoformat()}.jsonl" in files
    assert logs / f"{today.isoformat()}.jsonl" not in files


def test_analysis_input_signature_is_stable_while_the_open_day_grows(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    today = keylog_analysis._open_log_date()
    _write_day(logs, (today - timedelta(days=1)).isoformat(), "KEY_A", 1)
    _write_day(logs, today.isoformat(), "KEY_A", 1)
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    window = {"start": today - timedelta(days=3), "end": today + timedelta(days=1)}

    before = keylog_analysis._input_signature(
        keylog_analysis._analysis_input_files(bindings_path=bindings, **window)
    )
    _write_day(logs, today.isoformat(), "KEY_A", 50)
    keylog._indexed_log_files.cache_clear()
    after = keylog_analysis._input_signature(
        keylog_analysis._analysis_input_files(bindings_path=bindings, **window)
    )

    assert before == after


def test_refresh_observes_open_day_growth_without_rescanning_closed_days(tmp_path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    today = date(2026, 6, 7)
    monkeypatch.setattr(keylog_analysis, "_open_log_date", lambda: today)
    for day in ("2026-06-05", "2026-06-06", "2026-06-07"):
        _write_day(logs, day, "KEY_A", 2)
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    out = tmp_path / "analysis.json"
    window = {"start": date(2026, 6, 5), "end": today}
    first = keylog_analysis.write_keylog_analysis(out, bindings_path=bindings, **window)
    assert first.keypress_count == 6
    previous_size = (logs / "2026-06-07.jsonl").stat().st_size
    _write_day(logs, "2026-06-07", "KEY_A", 5)
    keylog._indexed_log_files.cache_clear()
    reads = _record_tails(monkeypatch)
    second = keylog_analysis.write_keylog_analysis(out, bindings_path=bindings, **window)
    assert second.keypress_count == 9
    assert reads == [("2026-06-07.jsonl", previous_size)]
    reads.clear()
    third = keylog_analysis.write_keylog_analysis(out, bindings_path=bindings, **window)
    assert third.keypress_count == 9
    assert reads == []
    narrower = keylog_analysis.write_keylog_analysis(
        out, bindings_path=bindings, start=today, end=today,
    )
    assert narrower.keypress_count == 5
    assert reads == []
