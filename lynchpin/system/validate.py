from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

import typer

from ..core.config import get_config
from ..core.vendor import add_vendor_paths
from ..ingest.fbmessenger_export import ensure_export_db_compatibility
from ..sources.captures import (
    activitywatch,
    atuin,
    codex,
    instrumentation,
    webhistory,
    webhistory_raw,
)
from ..sources.exports import (
    chatlog,
    fbmessenger,
    goodreads,
    health,
    polylogue,
    raindrop,
    reddit,
    sleep,
    spotify,
    takeout,
    wykop,
)
from ..sources.indices import gitstats, repos, sessions
from ..sources.libraries import dendron, finance, substack

app = typer.Typer(pretty_exceptions_show_locals=False)

ACTIVE_HPI_MODULES: tuple[str, ...] = (
    "my.coding.commits",
    "my.calendar.holidays",
    "my.fbmessenger",
    "my.smscalls",
    "my.sleep.manual",
    "my.money",
    "my.webhistory",
    "my.browser",
    "my.google.takeout.parser",
    "my.goodreads",
    "my.spotify.gdpr",
    "my.activitywatch",
    "my.activitywatch.active_window",
    "my.atuin",
)


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


def _format_terminal_audit_detail(summary: object, root: Path) -> str:
    return (
        f"root={root} "
        f"generation={summary.counts_by_generation} "
        f"status={summary.counts_by_status} "
        f"manifest={summary.manifest_count}/{summary.cast_count} "
        f"events={summary.events_count}/{summary.cast_count} "
        f"unreadable={summary.unreadable_header_count} "
        f"activity_missing={summary.missing_activity_estimate_count} "
        f"header_only={summary.header_only_count} "
        f"degraded={summary.degraded_count} "
        f"damaged={summary.damaged_count} "
        f"quarantine={summary.quarantine_candidate_count}"
    )


def _select_hpi_modules(
    *,
    modules: list[str],
    registry: dict[str, Callable[[], Tuple[Optional[int], str]]],
) -> list[str]:
    if modules:
        selected = list(dict.fromkeys(modules))
    else:
        selected = list(ACTIVE_HPI_MODULES)

    unknown = [name for name in selected if name not in registry]
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise typer.BadParameter(f"Unknown HPI module(s): {joined}", param_hint="--module")
    return selected


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
    return max(candidates, key=lambda p: p.stat().st_mtime)


@app.command()
def lynchpin(
    quick: bool = typer.Option(
        True,
        "--quick/--no-quick",
        help="Limit heavy sources to a small sample.",
    ),
    limit: int = typer.Option(2000, "--limit", help="Sample size for large iterators when --quick is set."),
    progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Log per-check progress to stderr.",
    ),
    output: Optional[Path] = typer.Option(
        Path("artefacts/lynchpin/validation/lynchpin.jsonl"),
        "--output",
        help="Optional JSONL output path.",
    ),
) -> None:
    """Validate lynchpin sources against real local data."""
    cfg = get_config()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    sample_limit = limit if quick else None

    def _activitywatch_window() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(
            activitywatch.window_events(start=start, end=now),
            sample_limit,
        )
        detail = f"range={start.isoformat()}..{now.isoformat()}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _activitywatch_afk() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(
            activitywatch.afk_events(start=start, end=now),
            sample_limit,
        )
        detail = f"range={start.isoformat()}..{now.isoformat()}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _atuin_commands() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(
            atuin.iter_commands(start=start, end=now),
            sample_limit,
        )
        detail = f"range={start.isoformat()}..{now.isoformat()}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _chatlog_transcripts() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(chatlog.iter_transcripts(), sample_limit)
        detail = f"root={cfg.polylogue_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _codex_sessions() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(codex.iter_sessions(), sample_limit)
        detail = f"root={cfg.codex_sessions_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _dendron_notes() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(dendron.iter_notes(), sample_limit)
        detail = f"root={cfg.dendron_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _finance_transactions() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(finance.iter_transactions(), sample_limit)
        detail = f"journal={cfg.finance_journal}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _fbmessenger_messages() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(fbmessenger.iter_messages(), sample_limit)
        detail = f"root={cfg.fbmessenger_gdpr_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _gitstats_commits() -> Tuple[Optional[int], str]:
        baseline_path = cfg.baseline_dir / "git_numstat.jsonl"
        if not baseline_path.exists():
            return 0, f"missing {baseline_path}"
        count, truncated = _count_iter(gitstats.iter_commits(), sample_limit)
        detail = f"baseline={baseline_path}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _goodreads_books() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(goodreads.iter_books(), sample_limit)
        detail = f"library={cfg.goodreads_library}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _health_sleep_sessions() -> Tuple[Optional[int], str]:
        export_path = cfg.exports_root / "health" / "raw" / "samsung-health"
        count, truncated = _count_iter(health.iter_samsung_sleep_sessions(export_path), sample_limit)
        detail = f"export={export_path}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _health_weight_entries() -> Tuple[Optional[int], str]:
        export_path = cfg.exports_root / "health" / "raw" / "samsung-health"
        count, truncated = _count_iter(health.iter_samsung_weight_entries(export_path), sample_limit)
        detail = f"export={export_path}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _instrumentation_terminal_sessions() -> Tuple[Optional[int], str]:
        entries, truncated = _sample_iter(instrumentation.iter_terminal_audit(), sample_limit)
        summary = instrumentation.summarize_terminal_audit(iter(entries))
        detail = _format_terminal_audit_detail(summary, cfg.asciinema_root)
        if truncated:
            detail += f" (sample {sample_limit})"
        return summary.cast_count, detail

    def _instrumentation_terminal_events() -> Tuple[Optional[int], str]:
        events, truncated = _sample_iter(instrumentation.iter_terminal_session_events(), sample_limit)
        detail = (
            f"root={cfg.asciinema_root} "
            f"sources={dict(Counter(getattr(event, 'source', 'unknown') for event in events))} "
            f"types={dict(Counter(getattr(event, 'type', 'unknown') for event in events).most_common(5))}"
        )
        if truncated:
            detail += f" (sample {sample_limit})"
        return len(events), detail

    def _instrumentation_audio() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(instrumentation.iter_audio_recordings(), sample_limit)
        detail = f"root={cfg.audio_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _instrumentation_screen() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(instrumentation.iter_screenshots(), sample_limit)
        detail = f"root={cfg.screenshot_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _polylogue_docs() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(polylogue.iter_documents(), sample_limit)
        detail = f"root={cfg.polylogue_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _polylogue_runs() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(polylogue.iter_runs(), sample_limit)
        detail = f"root={cfg.polylogue_archive_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _raindrop_bookmarks() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(raindrop.iter_bookmarks(), sample_limit)
        detail = f"csv={cfg.raindrop_csv}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_comments() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_comments(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_posts() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_posts(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_saved_posts() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_saved_posts(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_saved_comments() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_saved_comments(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_post_votes() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_post_votes(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_comment_votes() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_comment_votes(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _reddit_message_headers() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(reddit.iter_message_headers(), sample_limit)
        detail = f"dir={cfg.reddit_export_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _repos_recent_commit() -> Tuple[Optional[int], str]:
        repo = repos.GitRepository(cfg.repo_root)
        commits = repo.recent_commits(max_count=1)
        detail = f"repo={cfg.repo_root}"
        return len(commits), detail

    def _sessions_records() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(sessions.iter_sessions(), sample_limit)
        detail = f"docs={cfg.session_docs_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _sleep_entries() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(sleep.iter_sleep(), sample_limit)
        detail = f"jsonl={cfg.sleep_jsonl}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _spotify_streams() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(spotify.iter_streams(), sample_limit)
        detail = f"root={cfg.spotify_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _substack_posts() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(substack.iter_posts(), sample_limit)
        detail = f"root={cfg.substack_root}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _takeout_archives() -> Tuple[Optional[int], str]:
        root = cfg.exports_root / "google" / "raw" / "takeout"
        latest = _latest_takeout_archive(root)
        if latest is None:
            return 0, f"no takeout archives in {root}"
        parts = takeout.expand_takeout_parts(latest)
        detail = f"latest={latest.name} parts={len(parts)}"
        return len(parts), detail

    def _webhistory_entries() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(webhistory.iter_entries(), sample_limit)
        detail = (
            f"ndjson={cfg.webhistory_ndjson}" if cfg.webhistory_ndjson else f"root={cfg.webhistory_dir}"
        )
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _webhistory_raw_entries() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(webhistory_raw.iter_entries(), sample_limit)
        detail = f"root={cfg.webhistory_raw_dir}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _wykop_entries() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(wykop.iter_entries(), sample_limit)
        detail = f"user={cfg.wykop_username}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _wykop_entry_comments() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(wykop.iter_entry_comments(), sample_limit)
        detail = f"user={cfg.wykop_username}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _wykop_link_comments() -> Tuple[Optional[int], str]:
        count, truncated = _count_iter(wykop.iter_link_comments(), sample_limit)
        detail = f"user={cfg.wykop_username}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    checks = [
        ("lynchpin.activitywatch.window_24h", _activitywatch_window),
        ("lynchpin.activitywatch.afk_24h", _activitywatch_afk),
        ("lynchpin.atuin.commands_24h", _atuin_commands),
        ("lynchpin.chatlog.transcripts", _chatlog_transcripts),
        ("lynchpin.codex.sessions", _codex_sessions),
        ("lynchpin.dendron.notes", _dendron_notes),
        ("lynchpin.finance.transactions", _finance_transactions),
        ("lynchpin.fbmessenger.messages", _fbmessenger_messages),
        ("lynchpin.gitstats.commits", _gitstats_commits),
        ("lynchpin.goodreads.books", _goodreads_books),
        ("lynchpin.health.samsung_sleep", _health_sleep_sessions),
        ("lynchpin.health.samsung_weight", _health_weight_entries),
        ("lynchpin.instrumentation.terminal_sessions", _instrumentation_terminal_sessions),
        ("lynchpin.instrumentation.terminal_events", _instrumentation_terminal_events),
        ("lynchpin.instrumentation.audio", _instrumentation_audio),
        ("lynchpin.instrumentation.screen", _instrumentation_screen),
        ("lynchpin.polylogue.docs", _polylogue_docs),
        ("lynchpin.polylogue.runs", _polylogue_runs),
        ("lynchpin.raindrop.bookmarks", _raindrop_bookmarks),
        ("lynchpin.reddit.comments", _reddit_comments),
        ("lynchpin.reddit.posts", _reddit_posts),
        ("lynchpin.reddit.saved_posts", _reddit_saved_posts),
        ("lynchpin.reddit.saved_comments", _reddit_saved_comments),
        ("lynchpin.reddit.post_votes", _reddit_post_votes),
        ("lynchpin.reddit.comment_votes", _reddit_comment_votes),
        ("lynchpin.reddit.message_headers", _reddit_message_headers),
        ("lynchpin.repos.recent_commit", _repos_recent_commit),
        ("lynchpin.sessions.records", _sessions_records),
        ("lynchpin.sleep.entries", _sleep_entries),
        ("lynchpin.spotify.streams", _spotify_streams),
        ("lynchpin.substack.posts", _substack_posts),
        ("lynchpin.takeout.archives", _takeout_archives),
        ("lynchpin.webhistory.entries", _webhistory_entries),
        ("lynchpin.webhistory.raw_entries", _webhistory_raw_entries),
        ("lynchpin.wykop.entries", _wykop_entries),
        ("lynchpin.wykop.entry_comments", _wykop_entry_comments),
        ("lynchpin.wykop.link_comments", _wykop_link_comments),
    ]

    results = _run_checks(
        checks,
        output=output,
        progress=progress,
        label="lynchpin",
        quick=quick,
        limit=sample_limit,
    )
    _exit_on_failures(results)


@app.command()
def hpi(
    modules: list[str] = typer.Option(
        [],
        "--module",
        help="Explicit supported HPI module(s) to validate; defaults to the full supported set.",
    ),
    quick: bool = typer.Option(
        True,
        "--quick/--no-quick",
        help="Limit heavy sources to a small sample.",
    ),
    limit: int = typer.Option(2000, "--limit", help="Sample size for large iterators when --quick is set."),
    verbose: bool = typer.Option(False, "--verbose", help="Alias for --progress."),
    progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Log per-check progress to stderr.",
    ),
    output: Optional[Path] = typer.Option(
        Path("artefacts/lynchpin/validation/hpi.jsonl"),
        "--output",
        help="Optional JSONL output path.",
    ),
) -> None:
    """Validate the supported vendored HPI modules against local data/config."""
    cfg = get_config()
    ensure_export_db_compatibility(cfg.fbmessenger_db)
    add_vendor_paths()
    sample_limit = limit if quick else None

    def _check_holidays() -> Tuple[Optional[int], str]:
        if quick:
            return None, "skipped (quick mode)"
        from my.calendar import holidays

        stats = holidays.stats()
        return len(stats), "sample=calendar.holidays.stats"

    def _check_commits() -> Tuple[Optional[int], str]:
        from my.coding import commits as m_commits

        repos_count = len(m_commits.repos())
        count, truncated = _count_iter(m_commits.commits(), sample_limit)
        detail = f"repos={repos_count}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_fbmessenger() -> Tuple[Optional[int], str]:
        from my.fbmessenger import all as fb_all

        count, truncated = _count_iter(fb_all.messages(), sample_limit)
        detail = f"source=my.fbmessenger.all.messages db={cfg.fbmessenger_db}"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_smscalls() -> Tuple[Optional[int], str]:
        from my import smscalls

        stats = smscalls.stats()
        return len(stats), "source=my.smscalls.stats"

    def _check_sleep_manual() -> Tuple[Optional[int], str]:
        from my.sleep import manual

        count, truncated = _count_iter(manual.sleep(), sample_limit)
        detail = "source=my.sleep.manual.sleep"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_money() -> Tuple[Optional[int], str]:
        from my import money

        stats = money.stats()
        return len(stats), "source=my.money.stats"

    def _check_webhistory() -> Tuple[Optional[int], str]:
        from my import webhistory

        count, truncated = _count_iter(webhistory.history(), sample_limit)
        detail = "source=my.webhistory.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_browser() -> Tuple[Optional[int], str]:
        if quick:
            from my.browser import active_browser
            from my.browser import export as browser_export

            export_inputs = browser_export.inputs()
            active_inputs = active_browser.inputs()
            total_inputs = len(export_inputs) + len(active_inputs)
            detail = (
                "source=my.browser.{export,active_browser}.inputs "
                f"export={len(export_inputs)} active={len(active_inputs)} "
                f"export_path={browser_export.config.export_path} active_path={active_browser.config.export_path}"
            )
            return total_inputs, detail
        from my.browser import all as browser_all

        count, truncated = _count_iter(browser_all.history(), sample_limit)
        detail = "source=my.browser.all.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_takeout_parser() -> Tuple[Optional[int], str]:
        if quick:
            return None, "skipped (quick mode)"
        from my.google.takeout import parser as takeout_parser

        inputs = takeout_parser.inputs()
        return len(inputs), "source=my.google.takeout.parser.inputs"

    def _check_goodreads() -> Tuple[Optional[int], str]:
        from my import goodreads as hpi_goodreads

        inputs = hpi_goodreads.inputs()
        return len(inputs), "source=my.goodreads.inputs"

    def _check_spotify_gdpr() -> Tuple[Optional[int], str]:
        from my.spotify import gdpr as spotify_gdpr

        inputs = spotify_gdpr.inputs()
        return len(inputs), "source=my.spotify.gdpr.inputs"

    def _check_activitywatch() -> Tuple[Optional[int], str]:
        from my import activitywatch as aw

        inputs = aw.inputs()
        return len(inputs), "source=my.activitywatch.inputs"

    def _check_activitywatch_active_window() -> Tuple[Optional[int], str]:
        from my.activitywatch import active_window

        inputs = active_window.inputs()
        return len(inputs), "source=my.activitywatch.active_window.inputs"

    def _check_atuin() -> Tuple[Optional[int], str]:
        from my import atuin

        count, truncated = _count_iter(atuin.history(), sample_limit)
        detail = "source=my.atuin.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    registry = {
        "my.coding.commits": _check_commits,
        "my.calendar.holidays": _check_holidays,
        "my.fbmessenger": _check_fbmessenger,
        "my.smscalls": _check_smscalls,
        "my.sleep.manual": _check_sleep_manual,
        "my.money": _check_money,
        "my.webhistory": _check_webhistory,
        "my.browser": _check_browser,
        "my.google.takeout.parser": _check_takeout_parser,
        "my.goodreads": _check_goodreads,
        "my.spotify.gdpr": _check_spotify_gdpr,
        "my.activitywatch": _check_activitywatch,
        "my.activitywatch.active_window": _check_activitywatch_active_window,
        "my.atuin": _check_atuin,
    }
    selected = _select_hpi_modules(modules=modules, registry=registry)
    checks = [(name, registry[name]) for name in selected]

    progress = progress or verbose
    results = _run_checks(
        checks,
        output=output,
        progress=progress,
        label="hpi",
        quick=quick,
        limit=sample_limit,
    )
    _exit_on_failures(results)


if __name__ == "__main__":
    app()
