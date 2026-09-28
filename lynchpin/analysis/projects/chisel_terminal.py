"""Operator-facing terminal text for a Chisel build.

Everything here renders values the build already measured; nothing decides
build state. Published packages never contain this text.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from lynchpin.sources import chisel as source_chisel
from lynchpin.sources.chisel import RepoPlan, _fmt_bytes, _print_live

SUMMARY_COLUMNS = ("Project", "Status", "Slices", "Issues", "PRs", "Commits", "Size", "Time")
SUMMARY_RIGHT_ALIGNED = frozenset({"Slices", "Issues", "PRs", "Commits", "Size", "Time"})
_STATUS_LABELS = {"generated": "ok"}
_STATUS_STYLES = {"generated": "green", "partial": "yellow"}
# Statuses whose project produced measurements; the others render as dashes.
MEASURED_STATUSES = frozenset({"generated", "partial"})


def _n(value: Any) -> str:
    return f"{int(value or 0):,}"


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def status_style(status: str) -> str:
    return _STATUS_STYLES.get(status, "red")


def github_count_cell(counts: Mapping[str, Any], kind: str) -> str:
    """Render open and completed counts, e.g. ``29 open · 4,504 merged``.

    ``≥`` marks an open count that is a lower bound because the local
    inventory may be truncated; ``summary_legend`` explains it once.
    """
    done_key, done_word = ("issues_closed", "closed") if kind == "issues" else ("prs_merged", "merged")
    truncated = (
        counts.get(f"{kind}_open_current") is None
        and counts.get(f"{kind}_open_count_coverage") == "possibly_truncated"
    )
    return f"{'≥' if truncated else ''}{_n(counts.get(f'{kind}_open'))} open · {_n(counts.get(done_key))} {done_word}"


def slices_cell(configured: int, xml_rendered: int, *, xml_requested: bool) -> str:
    """Configured slice count, plus the XML renderings when XML was requested."""
    return f"{configured:,} · {xml_rendered:,} XML" if xml_requested else f"{configured:,}"


def summary_rows(
    plans: Sequence[RepoPlan],
    results: Mapping[str, Mapping[str, Any]],
    counts_by_project: Mapping[str, Mapping[str, Any]],
    *,
    xml_requested: bool,
    total_elapsed: float,
) -> tuple[list[tuple[str, ...]], tuple[str, ...], list[str]]:
    """Return (project rows, total row, statuses) in ``SUMMARY_COLUMNS`` order."""
    rows: list[tuple[str, ...]] = []
    statuses: list[str] = []
    total_bytes = 0
    for plan in plans:
        result = results.get(plan.name, {})
        status = str(result.get("status", "unknown"))
        statuses.append(status)
        size = int(result.get("total_bytes", 0) or 0)
        total_bytes += size
        elapsed = f"{float(result.get('elapsed_s', 0) or 0):.1f}s"
        if status not in MEASURED_STATUSES:
            rows.append((plan.name, status_label(status), "–", "–", "–", "–", _fmt_bytes(size), elapsed))
            continue
        counts = counts_by_project.get(plan.name, {})
        rows.append((
            plan.name,
            status_label(status),
            slices_cell(len(plan.slices), int(result.get("slices", 0) or 0), xml_requested=xml_requested),
            github_count_cell(counts, "issues"),
            github_count_cell(counts, "prs"),
            _n(result.get("gitlog_commits")),
            _fmt_bytes(size),
            elapsed,
        ))
    total = ("Total", "", "", "", "", "", _fmt_bytes(total_bytes), f"{total_elapsed:.1f}s")
    return rows, total, statuses


def summary_legend(
    counts_by_project: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
) -> list[str]:
    """Explain, once, how current the GitHub counts in the table are."""
    lines: list[str] = []
    counts = list(counts_by_project.values())
    unknown = any(
        c.get("issues_open_current") is None or c.get("prs_open_current") is None for c in counts
    )
    refresh_status = (manifest or {}).get("refresh_status")
    if refresh_status == "unavailable":
        lines.append("GitHub mirror unavailable: issue and PR counts are empty, not zero activity.")
    elif unknown:
        if refresh_status == "local_only":
            lines.append("GitHub counts from the local mirror; remote not checked (--refresh updates it).")
        elif refresh_status == "stale_fallback":
            lines.append("GitHub counts from the local mirror; the remote refresh failed, so they may be stale.")
        else:
            lines.append("GitHub counts from the local mirror; current remote counts unavailable.")
    if any(
        c.get(f"{kind}_open_current") is None and c.get(f"{kind}_open_count_coverage") == "possibly_truncated"
        for c in counts
        for kind in ("issues", "prs")
    ):
        lines.append("≥ marks an open count that is a lower bound: the mirror's inventory may be truncated.")
    return lines


def render_plain_table(rows: Sequence[Sequence[str]], total: Sequence[str]) -> list[str]:
    """Fixed-width text rendering of the summary, for output without rich."""
    body = [SUMMARY_COLUMNS, *rows, total]
    widths = [max(len(str(row[i])) for row in body) for i in range(len(SUMMARY_COLUMNS))]

    def line(row: Sequence[str]) -> str:
        cells = [
            str(cell).rjust(width) if name in SUMMARY_RIGHT_ALIGNED else str(cell).ljust(width)
            for cell, width, name in zip(row, widths, SUMMARY_COLUMNS)
        ]
        return "  ".join(cells).rstrip()

    rule = "-" * len(line(SUMMARY_COLUMNS))
    return [line(SUMMARY_COLUMNS), rule, *(line(row) for row in rows), rule, line(total)]


def github_context_line(manifest: Mapping[str, Any] | None, elapsed: float) -> str:
    """Say what the GitHub context step did, in operator terms."""
    manifest = manifest or {}
    status = manifest.get("refresh_status")
    if not manifest:
        return f"GitHub: using the existing context product ({elapsed:.1f}s)"
    if status == "local_only":
        return f"GitHub: read the local mirror in {elapsed:.1f}s; remote not checked (--refresh updates it)"
    if status == "unavailable":
        return f"GitHub: local mirror unavailable ({manifest.get('reason') or 'no reason given'}); issue and PR counts will be empty"
    if status == "stale_fallback":
        return f"GitHub: remote refresh failed; using the existing mirror ({elapsed:.1f}s)"
    parts = [
        f"{_n(manifest.get('inventory_items_seen'))} inventory items seen",
        f"{_n(manifest.get('detail_refreshes'))} details fetched",
        f"{_n(manifest.get('detail_reuses'))} unchanged and reused",
    ]
    if missed := int(manifest.get("detail_misses") or 0):
        parts.append(f"{missed:,} details missed")
    stale_open = sum(int(v or 0) for v in (manifest.get("project_stale_open_removed") or {}).values())
    if stale_open:
        parts.append(f"{stale_open:,} stale open items removed")
    fetched = int(manifest.get("missing_commit_refs_fetched") or 0)
    deferred = int(manifest.get("missing_commit_refs_deferred") or 0)
    if fetched or deferred:
        parts.append(f"{fetched:,} missing commit refs fetched")
    if deferred:
        parts.append(f"{deferred:,} deferred")
    reasons = {
        str(key): int(value)
        for key, value in (manifest.get("detail_decision_reasons") or {}).items()
        if key != "unchanged_inventory" and int(value or 0)
    }
    if reasons:
        parts.append("fetch reasons " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    if manifest.get("substrate_status") == "degraded":
        parts.append(f"substrate promotion degraded after {int(manifest.get('substrate_attempts') or 1)} attempt(s)")
    return f"GitHub: refreshed in {elapsed:.1f}s — " + "; ".join(parts)


def header_lines(
    plans: Sequence[RepoPlan],
    *,
    output_root: Any,
    xml_version: str,
    repo_workers: int,
    slice_workers: int,
    repomix_slots: int,
) -> list[str]:
    xml = "XML not requested" if xml_version == "not requested" else f"XML via repomix {xml_version}"
    lines = [
        f"[bold]Chisel evidence packages[/bold] · {xml}",
        f"Output    {output_root}",
        f"Workers   {_plural(repo_workers, 'project')} at a time × "
        f"{_plural(slice_workers, 'slice')} each · {repomix_slots} repomix slots",
        f"Projects  {len(plans)} selected; completion order may differ",
        "Contents  raw source, evidence tables, an offline index and archives per package",
    ]
    for index, plan in enumerate(plans, start=1):
        compressed = "compressed whole-repo XML" if plan.compressed else "no compressed XML"
        lines.append(
            f"  {index}. {plan.name}  {_plural(len(plan.slices), 'slice')} · {compressed} → {output_root}/{plan.name}"
        )
    return lines


def interactive() -> bool:
    console = source_chisel._console
    return console is not None and bool(console.is_terminal)


class ProgressLine:
    """One live status line on a terminal; one line per change otherwise.

    On a terminal the line redraws in place below ordinary output. Elsewhere
    each distinct state prints once, so logs keep one line per event.
    """

    def __init__(self) -> None:
        self._last: str | None = None
        self._started = time.perf_counter()
        self._live: Any = None
        if interactive():
            from rich.live import Live
            from rich.text import Text

            self._text = Text
            self._live = Live(Text(""), console=source_chisel._console, transient=True,
                              refresh_per_second=4)
            self._live.start()

    poll_seconds = 1.0

    @property
    def live(self) -> bool:
        return self._live is not None

    def update(self, text: str) -> None:
        if self._live is not None:
            elapsed = time.perf_counter() - self._started
            self._live.update(self._text(f"{text}  ({elapsed:.0f}s)", style="dim"))
            return
        if text != self._last:
            self._last = text
            _print_live(text)

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def __enter__(self) -> "ProgressLine":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def progress_text(completed: int, total: int, active: Mapping[str, set[str]]) -> str:
    running = [f"{name} ({', '.join(sorted(stages)) or 'waiting'})" for name, stages in active.items()]
    return f"Progress {completed}/{total}: " + ("; ".join(running) if running else "waiting for workers")


@contextmanager
def timed_step(label: str) -> Iterator[None]:
    """Report one publication step: live while it runs, one line when done."""
    started = time.perf_counter()
    with ProgressLine() as line:
        if line.live:
            line.update(f"{label}…")
        yield
    _print_live(f"  ✓ {label}  {time.perf_counter() - started:.1f}s")
