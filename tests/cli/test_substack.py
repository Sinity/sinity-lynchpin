from __future__ import annotations

from pathlib import Path

from lynchpin.cli import substack


def test_publication_name_maps_astral_codex_ten() -> None:
    assert substack._publication_name("https://www.astralcodexten.com/") == "acx"


def test_download_invokes_configured_downloader_without_cookie_value_in_output(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "sbstck-dl"
    executable.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setenv("LYNCHPIN_SUBSTACK_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("LYNCHPIN_SUBSTACK_DOWNLOADER", str(executable))
    monkeypatch.setattr(substack.subprocess, "run", lambda command, check: calls.append(command))

    assert substack.main(["download", "--url", "https://www.astralcodexten.com/"]) == 0
    assert calls == [[
        str(executable), "download", "--url", "https://www.astralcodexten.com/",
        "--format", "html", "--output", str(tmp_path / "archive/acx"), "--rate", "2",
    ]]
