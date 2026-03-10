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
) -> None:
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


def _emit(results: list[CheckResult], output: Optional[Path]) -> None:
    for result in results:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(asdict(result), ensure_ascii=False) for result in results) + "\n"
        output.write_text(payload, encoding="utf-8")


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
        detail = (
            f"root={cfg.asciinema_root} "
            f"generation={summary.counts_by_generation} "
            f"manifest={summary.manifest_count}/{summary.cast_count} "
            f"events={summary.events_count}/{summary.cast_count} "
            f"legacy={summary.legacy_meta_count} "
            f"malformed_legacy={summary.malformed_legacy_meta_count}"
        )
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
        detail = f"csv={cfg.sessions_csv}"
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

    _run_checks(
        checks,
        output=output,
        progress=progress,
        label="lynchpin",
        quick=quick,
        limit=sample_limit,
    )


@app.command()
def hpi(
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
    """Validate vendored upstream HPI modules against local data/config."""
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

    def _check_body_weight() -> Tuple[Optional[int], str]:
        from my.body import weight

        count, truncated = _count_iter(weight.from_orgmode(), sample_limit)
        detail = "source=orgmode"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_body_exercise() -> Tuple[Optional[int], str]:
        from my.body.exercise import all as exercise_all
        from my import endomondo, runnerup
        from my.core import get_files

        if not endomondo.inputs() and not get_files(runnerup.config.export_path):
            return 0, "source=my.body.exercise.all (empty inputs)"
        df = exercise_all.dataframe()
        return int(df.shape[0]), f"rows={df.shape[0]}"

    def _check_fbmessenger() -> Tuple[Optional[int], str]:
        from my.fbmessenger import all as fb_all

        count, truncated = _count_iter(fb_all.messages(), sample_limit)
        detail = "source=my.fbmessenger.all.messages"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_github_gdpr() -> Tuple[Optional[int], str]:
        from my.github import gdpr

        if not gdpr.inputs():
            return 0, "source=my.github.gdpr.inputs (empty)"
        stats = gdpr.stats()
        return len(stats), "source=my.github.gdpr.stats"

    def _check_github_ghexport() -> Tuple[Optional[int], str]:
        from my.github import ghexport

        if not ghexport.inputs():
            return 0, "source=my.github.ghexport.inputs (empty)"
        stats = ghexport.stats()
        return len(stats), "source=my.github.ghexport.stats"

    def _check_github_all() -> Tuple[Optional[int], str]:
        from my.github import all as gh_all
        from my.github import gdpr, ghexport

        if not gdpr.inputs() and not ghexport.inputs():
            return 0, "source=my.github.{gdpr,ghexport}.inputs (empty)"

        count, truncated = _count_iter(gh_all.events(), sample_limit)
        detail = "source=my.github.all.events"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_lastfm() -> Tuple[Optional[int], str]:
        from my import lastfm

        if not lastfm.inputs():
            return 0, "source=my.lastfm.inputs (empty)"
        count, truncated = _count_iter(lastfm.scrobbles(), sample_limit)
        detail = "source=my.lastfm.scrobbles"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_location_google() -> Tuple[Optional[int], str]:
        if quick:
            from my.google.takeout import paths as takeout_paths

            last = takeout_paths.get_last_takeout(
                path="Takeout/Location History/Location History.json"
            )
            detail = f"quick=presence latest={last}" if last else "quick=presence none"
            return (1 if last else 0), detail
        from my.location import google as loc_google

        count, truncated = _count_iter(loc_google.locations(), sample_limit)
        detail = "source=my.location.google.locations"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_photos_main() -> Tuple[Optional[int], str]:
        from my.photos import main as photos_main

        if sample_limit is not None:
            roots = [Path(p) for p in photos_main.config.paths]
            existing = [root for root in roots if root.exists()]
            detail = f"source=my.photos.main.config.paths roots={len(existing)}"
            return len(existing), detail
        count, truncated = _count_iter(photos_main.photos(), sample_limit)
        detail = "source=my.photos.main.photos"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_reddit() -> Tuple[Optional[int], str]:
        from my.reddit import all as reddit_all

        count, truncated = _count_iter(reddit_all.comments(), sample_limit)
        detail = "source=my.reddit.all.comments"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_smscalls() -> Tuple[Optional[int], str]:
        from my import smscalls

        stats = smscalls.stats()
        return len(stats), "source=my.smscalls.stats"

    def _check_twitter_archive() -> Tuple[Optional[int], str]:
        from my.twitter import archive

        if not archive.inputs():
            return 0, "source=my.twitter.archive.inputs (empty)"
        stats = archive.stats()
        return len(stats), "source=my.twitter.archive.stats"

    def _check_twitter_twint() -> Tuple[Optional[int], str]:
        from my.twitter import twint as twitter_twint
        from my.core import get_files

        if not get_files(twitter_twint.config.export_path):
            return 0, "source=my.twitter.twint.export_path (empty)"
        stats = twitter_twint.stats()
        return len(stats), "source=my.twitter.twint.stats"

    def _check_twitter_all() -> Tuple[Optional[int], str]:
        from my.twitter import all as twitter_all
        from my.twitter import archive, twint as twitter_twint
        from my.core import get_files

        if not archive.inputs() and not get_files(twitter_twint.config.export_path):
            return 0, "source=my.twitter.{archive,twint}.export_path (empty)"
        count, truncated = _count_iter(twitter_all.tweets(), sample_limit)
        detail = "source=my.twitter.all.tweets"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

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
            from my.browser import export as browser_export

            inputs = browser_export.inputs()
            return len(inputs), "source=my.browser.export.inputs"
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

    def _check_taskwarrior() -> Tuple[Optional[int], str]:
        from my import taskwarrior

        inputs = taskwarrior.inputs()
        return len(inputs), "source=my.taskwarrior.inputs"

    def _check_linkedin() -> Tuple[Optional[int], str]:
        from my.linkedin import privacy_export

        path = privacy_export.input()
        return 1 if path.exists() else 0, "source=my.linkedin.privacy_export.input"

    def _check_steam_scraper() -> Tuple[Optional[int], str]:
        from my.steam import scraper

        inputs = scraper.inputs()
        return len(inputs), "source=my.steam.scraper.inputs"

    def _check_zsh() -> Tuple[Optional[int], str]:
        from my import zsh

        count, truncated = _count_iter(zsh.history(), sample_limit)
        detail = "source=my.zsh.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_bash() -> Tuple[Optional[int], str]:
        from my import bash

        count, truncated = _count_iter(bash.history(), sample_limit)
        detail = "source=my.bash.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    def _check_atuin() -> Tuple[Optional[int], str]:
        from my import atuin

        count, truncated = _count_iter(atuin.history(), sample_limit)
        detail = "source=my.atuin.history"
        if truncated:
            detail += f" (sample {sample_limit})"
        return count, detail

    checks = [
        ("my.coding.commits", _check_commits),
        ("my.calendar.holidays", _check_holidays),
        ("my.body.weight", _check_body_weight),
        ("my.body.exercise.all", _check_body_exercise),
        ("my.fbmessenger", _check_fbmessenger),
        ("my.github.all", _check_github_all),
        ("my.github.gdpr", _check_github_gdpr),
        ("my.github.ghexport", _check_github_ghexport),
        ("my.lastfm", _check_lastfm),
        ("my.location.google", _check_location_google),
        ("my.photos.main", _check_photos_main),
        ("my.reddit", _check_reddit),
        ("my.smscalls", _check_smscalls),
        ("my.twitter.all", _check_twitter_all),
        ("my.twitter.archive", _check_twitter_archive),
        ("my.twitter.twint", _check_twitter_twint),
        ("my.sleep.manual", _check_sleep_manual),
        ("my.money", _check_money),
        ("my.webhistory", _check_webhistory),
        ("my.browser", _check_browser),
        ("my.google.takeout.parser", _check_takeout_parser),
        ("my.goodreads", _check_goodreads),
        ("my.spotify.gdpr", _check_spotify_gdpr),
        ("my.activitywatch", _check_activitywatch),
        ("my.activitywatch.active_window", _check_activitywatch_active_window),
        ("my.taskwarrior", _check_taskwarrior),
        ("my.linkedin.privacy_export", _check_linkedin),
        ("my.steam.scraper", _check_steam_scraper),
        ("my.zsh", _check_zsh),
        ("my.bash", _check_bash),
        ("my.atuin", _check_atuin),
    ]

    progress = progress or verbose
    _run_checks(
        checks,
        output=output,
        progress=progress,
        label="hpi",
        quick=quick,
        limit=sample_limit,
    )


if __name__ == "__main__":
    app()
