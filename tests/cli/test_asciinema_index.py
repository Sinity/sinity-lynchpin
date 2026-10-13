"""Configured source and publication contracts for the header index."""
import json
from types import SimpleNamespace

from lynchpin.cli import asciinema_index as index


def configured(monkeypatch, root):
    monkeypatch.setattr(index, "get_config", lambda: SimpleNamespace(asciinema_root=root))


def test_custom_capture_root_retains_header_fields(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    cast = root / "2026/01/02/host-user-bash_pts_3-20260102T030405Z-1-2/session.cast"
    cast.parent.mkdir(parents=True)
    cast.write_text(json.dumps({"version": 3, "timestamp": 1767323045,
        "env": {"SHELL": "bash", "SINNIX_CAPTURE_SESSION_ID": "session-1"}}) + "\n")
    configured(monkeypatch, root)
    assert index.main([]) == 0
    rows = [json.loads(x) for x in (root / "index/asciinema_sessions.ndjson").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["capture_session_id"] == "session-1"
    assert rows[0]["path"] == cast.relative_to(root).as_posix()
    assert rows[0]["seq"] == 2


def test_missing_input_preserves_index_and_does_not_create_root(tmp_path, monkeypatch):
    root = tmp_path / "missing"
    output = tmp_path / "index.ndjson"
    output.write_text("retained")
    configured(monkeypatch, root)
    assert index.main(["--out", str(output)]) == 2
    assert output.read_text() == "retained"
    assert not root.exists()


def test_empty_available_input_is_success(tmp_path, monkeypatch):
    root = tmp_path / "empty"
    root.mkdir()
    configured(monkeypatch, root)
    assert index.main([]) == 0
    assert (root / "index/asciinema_sessions.ndjson").read_bytes() == b""


def test_failed_enumeration_preserves_previous_index(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    output = tmp_path / "index.ndjson"
    output.write_text("retained")
    configured(monkeypatch, root)
    def failed(_root):
        yield {"path": "first"}
        raise PermissionError("denied child")
    monkeypatch.setattr(index, "iter_sessions", failed)
    assert index.main(["--out", str(output)]) == 2
    assert output.read_text() == "retained"
    assert not list(tmp_path.glob(".asciinema-index-*"))


def test_unavailable_registry_fails_without_output(tmp_path, monkeypatch):
    def unavailable():
        raise index.SourceUnavailableError("asciinema", reason="registry inaccessible")
    monkeypatch.setattr(index, "get_config", unavailable)
    output = tmp_path / "index.ndjson"
    assert index.main(["--out", str(output)]) == 2
    assert not output.exists()


def test_denied_source_preserves_index(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    output = tmp_path / "index.ndjson"
    output.write_text("retained")
    configured(monkeypatch, root)
    def denied(_root):
        raise PermissionError("source denied")
    monkeypatch.setattr(index.os, "scandir", denied)
    assert index.main(["--out", str(output)]) == 2
    assert output.read_text() == "retained"
