from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

import typer


@dataclass
class CheckResult:
    name: str
    status: str
    count: Optional[int]
    detail: str
    duration_ms: float
    error: Optional[str] = None


def _count_iter(items: Iterable[object], limit: Optional[int]) -> Tuple[int, bool]:
    if limit is None:
        return sum(1 for _ in items), False
    iterator = iter(items)
    count = sum(1 for _ in islice(iterator, limit))
    sentinel = object()
    truncated = next(iterator, sentinel) is not sentinel
    return count, truncated


def _sample_iter(items: Iterable[object], limit: Optional[int]) -> Tuple[list[object], bool]:
    if limit is None:
        return list(items), False
    iterator = iter(items)
    records = list(islice(iterator, limit))
    sentinel = object()
    truncated = next(iterator, sentinel) is not sentinel
    return records, truncated


def _run_check(name: str, fn: Callable[[], Tuple[Optional[int], str]]) -> CheckResult:
    started = time.perf_counter()
    try:
        count, detail = fn()
    except ModuleNotFoundError as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        return CheckResult(
            name=name,
            status="missing",
            count=None,
            detail=f"module missing: {exc.name or exc}",
            duration_ms=duration_ms,
            error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - explicit logging path
        duration_ms = (time.perf_counter() - started) * 1000.0
        return CheckResult(
            name=name,
            status="error",
            count=None,
            detail=str(exc),
            duration_ms=duration_ms,
            error=str(exc),
        )
    duration_ms = (time.perf_counter() - started) * 1000.0
    if count is None:
        status = "ok"
    elif count == 0:
        status = "empty"
    else:
        status = "ok"
    return CheckResult(
        name=name,
        status=status,
        count=count,
        detail=detail,
        duration_ms=duration_ms,
    )


def _log(message: str, enabled: bool) -> None:
    if enabled:
        typer.echo(message, err=True)


def _format_result_line(result: CheckResult) -> str:
    count = "-" if result.count is None else str(result.count)
    detail = f" detail={result.detail}" if result.detail else ""
    error = f" error={result.error}" if result.error else ""
    return (
        f"done name={result.name} status={result.status} count={count} "
        f"duration_ms={result.duration_ms:.1f}{detail}{error}"
    )


def _log_summary(
    results: list[CheckResult],
    elapsed_ms: float,
    enabled: bool,
    label: str,
    quick: bool,
    limit: Optional[int],
) -> None:
    if not enabled:
        return
    counts = Counter(result.status for result in results)
    limit_label = "-" if limit is None else str(limit)
    summary = (
        f"summary label={label} checks={len(results)} ok={counts.get('ok', 0)} "
        f"empty={counts.get('empty', 0)} missing={counts.get('missing', 0)} "
        f"error={counts.get('error', 0)} quick={quick} limit={limit_label} "
        f"duration_s={elapsed_ms / 1000.0:.2f}"
    )
    typer.echo(summary, err=True)
    slowest = sorted(results, key=lambda result: result.duration_ms, reverse=True)[:5]
    for result in slowest:
        typer.echo(
            f"slow name={result.name} status={result.status} duration_ms={result.duration_ms:.1f}",
            err=True,
        )


def _run_checks(
    checks: list[tuple[str, Callable[[], Tuple[Optional[int], str]]]],
    output: Optional[Path],
    progress: bool,
    label: str,
    quick: bool,
    limit: Optional[int],
) -> list[CheckResult]:
    started = time.perf_counter()
    _log(f"start label={label} checks={len(checks)} quick={quick} limit={limit}", progress)
    results: list[CheckResult] = []
    for name, fn in checks:
        _log(f"start name={name}", progress)
        result = _run_check(name, fn)
        _log(_format_result_line(result), progress)
        results.append(result)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _log_summary(results, elapsed_ms, progress, label, quick, limit)
    _emit(results, output)
    return results


def _emit(results: list[CheckResult], output: Optional[Path]) -> None:
    for result in results:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(asdict(result), ensure_ascii=False) for result in results) + "\n"
        output.write_text(payload, encoding="utf-8")


def _exit_on_failures(results: list[CheckResult]) -> None:
    failures = [result for result in results if result.status in {"missing", "error"}]
    if failures:
        raise typer.Exit(code=1)


def _latest_takeout_archive(root: Path) -> Optional[Path]:
    candidates = []
    for pattern in ("*.tgz", "*.tar.gz", "*.zip"):
        candidates.extend(root.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
