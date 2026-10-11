"""Warnings raised during a Chisel build stay out of its terminal output."""

from __future__ import annotations

import threading
import warnings
from pathlib import Path

import pytest

from lynchpin.sources.chisel_warnings import captured_warnings, parse_python_source

_BAD_ESCAPE = 'PATTERN = "blockers=\\(\\)"\n'

def _assert_source_warning(result: tuple[str, ...]) -> None:
    assert len(result) == 1
    assert result[0].startswith("line 1: SyntaxWarning:")
    assert "invalid escape sequence" in result[0]
    assert "\\(" in result[0]


@pytest.mark.parametrize("inside_build", [False, True])
def test_parse_returns_source_warnings_on_every_parse(tmp_path: Path, inside_build: bool, capfd) -> None:
    """Fails if a repeated parse of the same file loses its warning to
    deduplication, or if the warning is printed instead of returned."""

    def parse_twice() -> list[tuple[str, ...]]:
        return [parse_python_source(_BAD_ESCAPE, "pkg/mod.py")[1] for _ in range(2)]

    if inside_build:
        with captured_warnings(tmp_path / "warnings.log") as log:
            results = parse_twice()
        assert log.count == 0
    else:
        results = parse_twice()
    assert results[0] == results[1]
    for result in results:
        _assert_source_warning(result)
    assert capfd.readouterr().err == ""


def test_parse_error_still_raises() -> None:
    with pytest.raises(SyntaxError):
        parse_python_source("def broken(:\n", "pkg/broken.py")


def test_library_warning_from_worker_thread_goes_to_log(tmp_path: Path, capfd) -> None:
    """Fails if a worker-thread library warning reaches stderr, or if a source
    parse on the same thread claims it as that file's evidence."""
    log_path = tmp_path / "warnings.log"
    echoed: list[str] = []
    parsed: list[tuple[str, ...]] = []

    def worker() -> None:
        warnings.warn('Field "model_ref" has conflict with protected namespace "model_"', UserWarning)
        parsed.append(parse_python_source(_BAD_ESCAPE, "pkg/mod.py")[1])

    with captured_warnings(log_path, echo=echoed.append) as log:
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

    assert log.count == 1
    assert len(parsed) == 1
    _assert_source_warning(parsed[0])
    text = log_path.read_text()
    assert "UserWarning" in text and "model_ref" in text
    assert echoed == text.splitlines()
    assert capfd.readouterr().err == ""
