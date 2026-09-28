"""Keep Python warnings out of Chisel's terminal output.

A build imports third-party models and parses the Python sources it captures.
Neither kind of warning is operator output: library warnings go to a run log,
and a warning raised while parsing a captured file is returned to the caller,
which records it as evidence about that file.
"""

from __future__ import annotations

import ast
import threading
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

_local = threading.local()
_fallback_lock = threading.Lock()
_active_captures = 0


def _source_warning(category: type[Warning], message: Any, lineno: int) -> str:
    return f"line {lineno}: {category.__name__}: {message}"


class WarningLog:
    """Warnings raised outside source parsing during one build."""

    def __init__(self, path: Path | None, echo: Callable[[str], None] | None) -> None:
        self.path = path
        self.count = 0
        self._echo = echo
        self._lock = threading.Lock()
        self._stream: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("w", encoding="utf-8")

    def record(self, text: str) -> None:
        with self._lock:
            self.count += 1
            if self._stream is not None:
                self._stream.write(text + "\n")
                self._stream.flush()
        if self._echo is not None:
            self._echo(text)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None


@contextmanager
def captured_warnings(
    log_path: Path | None, *, echo: Callable[[str], None] | None = None
) -> Iterator[WarningLog]:
    """Route every warning raised during the block to ``log_path``.

    Enter this once, on the thread that starts the build, before worker
    threads exist: ``warnings.catch_warnings`` swaps process-wide state and is
    not safe to nest across threads on Python 3.12. ``echo`` additionally
    receives each logged line, for a verbose view.
    """
    global _active_captures
    log = WarningLog(log_path, echo)

    def show(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None:
        sink = getattr(_local, "sink", None)
        if sink is not None:
            sink.append(_source_warning(category, message, lineno))
            return
        log.record(f"{filename}:{lineno}: {category.__name__}: {message}")

    with warnings.catch_warnings():
        # A compiler warning is deduplicated by location unless it is always
        # shown; recording it on every parse keeps the evidence deterministic.
        warnings.filterwarnings("always", category=SyntaxWarning)
        warnings.showwarning = show
        _active_captures += 1
        try:
            yield log
        finally:
            _active_captures -= 1
            log.close()


def parse_python_source(text: str, filename: str = "<unknown>") -> tuple[ast.Module, tuple[str, ...]]:
    """Parse captured source and return the warnings the parse raised.

    Inside ``captured_warnings`` the warnings are collected per thread. Outside
    it, parses serialize on a lock and record through ``catch_warnings``; a
    warning another thread raises in that window is re-emitted afterwards.
    ``SyntaxError`` propagates unchanged.
    """
    if _active_captures:
        sink: list[str] = []
        _local.sink = sink
        try:
            tree = ast.parse(text, filename=filename)
        finally:
            _local.sink = None
        return tree, tuple(sink)
    foreign: list[warnings.WarningMessage] = []
    with _fallback_lock:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                tree = ast.parse(text, filename=filename)
            finally:
                own = [w for w in caught if w.filename == filename]
                foreign = [w for w in caught if w.filename != filename]
    for item in foreign:
        warnings.warn_explicit(item.message, item.category, item.filename, item.lineno)
    return tree, tuple(_source_warning(w.category, w.message, w.lineno) for w in own)
