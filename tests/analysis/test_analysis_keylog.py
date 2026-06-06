from __future__ import annotations

import json
from datetime import date
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


def test_keylog_analysis_requests_only_press_events(monkeypatch, tmp_path) -> None:
    calls = []
    bindings = tmp_path / "bindings.nix"
    bindings.write_text('{ bind = [ "SUPER, Return, exec, kitty" ]; }', encoding="utf-8")

    def fake_events(*, start, end, kinds=None):
        calls.append((start, end, kinds))
        return iter(())

    monkeypatch.setattr(keylog, "events", fake_events)

    analysis = keylog_analysis.analyze_keylog(
        start=date(2026, 6, 5),
        end=date(2026, 6, 5),
        bindings_path=bindings,
    )

    assert calls[0][2] == {"press"}
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
    assert saved["text_content"]["top_terms"][0] == {"term": "secret", "count": 1}
    assert "Secret Lynchpin words" not in out.read_text(encoding="utf-8")
    assert saved["caveats"]


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
