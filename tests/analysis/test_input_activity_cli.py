import json
import stat

import typer
from typer.testing import CliRunner

from lynchpin.analysis.input_activity_cli import register_commands


def test_private_output_end_to_end_without_raw_text_by_default(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-04-02.jsonl").write_text('\n'.join(json.dumps(dict(
        ts=f"2026-04-02T10:00:0{i}+00:00", event="press", session="keyboard", keycode="KEY_A", changed=True,
    )) for i in (1, 2)))
    contexts = tmp_path / "contexts.json"
    contexts.write_text(json.dumps([dict(id="one", start="2026-04-02T10:00:00+00:00", end="2026-04-02T10:10:00+00:00", app="editor", title="draft", source_ref="synthetic")]))
    out = tmp_path / "out.json"
    app = typer.Typer()
    register_commands(app)
    args = ["--start", "2026-04-02T10:00:00+00:00", "--end", "2026-04-02T10:10:00+00:00", "--logs-root", str(logs), "--contexts", str(contexts), "--out", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["totals"]["bout_seconds"] == 1
    assert "text_fragments" not in payload
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    before = out.read_bytes()
    assert CliRunner().invoke(app, args).exit_code != 0
    assert out.read_bytes() == before
