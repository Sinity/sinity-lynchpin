from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lynchpin.cli import narrative


def test_narrative_script_writes_artifacts(monkeypatch, tmp_path):
    calls = {}

    def fake_narrate(**kwargs):
        calls.update(kwargs)
        if kwargs.get("out"):
            Path(kwargs["out"]).write_text("# narrative\n", encoding="utf-8")
        if kwargs.get("json_out"):
            Path(kwargs["json_out"]).write_text('{"ok": true}\n', encoding="utf-8")
        return SimpleNamespace()

    monkeypatch.setattr(narrative, "narrate", fake_narrate)
    out = tmp_path / "narrative.md"
    json_out = tmp_path / "narrative.json"

    code = narrative.main(
        [
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-05",
            "--mode",
            "local-heavy",
            "--project",
            "polylogue",
            "--output",
            str(out),
            "--json-output",
            str(json_out),
        ]
    )

    assert code == 0
    assert calls["start"].isoformat() == "2026-05-01"
    assert calls["end"].isoformat() == "2026-05-05"
    assert calls["mode"] == "local-heavy"
    assert calls["projects"] == ["polylogue"]
    assert out.read_text(encoding="utf-8") == "# narrative\n"
    assert json_out.read_text(encoding="utf-8") == '{"ok": true}\n'


def test_narrative_script_rejects_reversed_window(capsys):
    code = narrative.main(["--start", "2026-05-05", "--end", "2026-05-01"])

    captured = capsys.readouterr()
    assert code == 2
    assert "--end must be on or after --start" in captured.err
