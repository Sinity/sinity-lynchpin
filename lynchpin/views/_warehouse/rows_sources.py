from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator, Tuple

from ...core.config import get_config
from ...sources.captures import activitywatch, atuin, codex, instrumentation, webhistory, webhistory_raw
from ...sources.exports import (
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
from ...sources.indices import gitstats, session_summaries as session_summaries_source, sessions
from ...sources.libraries import dendron, finance, substack
from .core import WarehouseContext, _json_dumps, _maybe_limit, _parse_dt


def _activitywatch_rows(events: Iterable[activitywatch.ActivityWatchEvent], ctx: WarehouseContext) -> Iterator[Tuple]:
    for event in _maybe_limit(events, ctx.limit):
        yield (
            event.bucket,
            event.start,
            event.end,
            _json_dumps(event.data),
        )


def _activitywatch_events(fn, fn_all, ctx: WarehouseContext) -> Iterable[activitywatch.ActivityWatchEvent]:
    if ctx.since is None and ctx.until is None:
        return fn_all()
    return fn(start=ctx.since, end=ctx.until)


def _atuin_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for cmd in _maybe_limit(atuin.iter_commands(start=ctx.since, end=ctx.until), ctx.limit):
        yield (
            cmd.timestamp,
            cmd.duration_ns,
            cmd.exit_code,
            cmd.cwd,
            cmd.command,
        )


def _codex_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for session in _maybe_limit(codex.iter_sessions(start=ctx.since, end=ctx.until), ctx.limit):
        yield (session.start, str(session.source))


def _chatlog_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for transcript in _maybe_limit(chatlog.iter_transcripts(start=ctx.since, end=ctx.until), ctx.limit):
        yield (
            transcript.provider,
            transcript.slug,
            transcript.title,
            str(transcript.path),
            transcript.started_at,
            transcript.tokens,
            transcript.words,
            transcript.attachment_count,
            transcript.attachment_bytes,
        )


def _dendron_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for note in _maybe_limit(dendron.iter_notes(), ctx.limit):
        yield (
            str(note.path),
            note.id,
            note.title,
            _json_dumps(note.tags),
            _json_dumps(note.frontmatter),
            note.body,
        )


def _finance_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for row in _maybe_limit(finance.iter_transactions(), ctx.limit):
        for idx, posting in enumerate(row.postings):
            yield (
                row.date,
                row.payee,
                row.narration,
                idx,
                posting.account,
                posting.amount,
                posting.currency or "",
                None,  # cost — not tracked in LedgerPosting
            )


def _fbmessenger_threads_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for thread in _maybe_limit(fbmessenger.iter_threads(), ctx.limit):
        yield (
            thread.thread_name,
            _json_dumps(thread.participants),
            thread.source,
        )


def _fbmessenger_messages_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for message in _maybe_limit(fbmessenger.iter_messages(), ctx.limit):
        yield (
            message.thread_name,
            _json_dumps(message.participants),
            message.sender,
            message.timestamp,
            message.text,
            message.kind,
            message.is_unsent,
            message.media_count,
            message.reaction_count,
            message.source,
        )


def _gitstats_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for commit in _maybe_limit(gitstats.iter_commits(), ctx.limit):
        yield (
            commit.date,
            commit.repo,
            commit.commit,
            commit.lines_added,
            commit.lines_deleted,
            commit.subject,
        )


def _goodreads_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for book in _maybe_limit(goodreads.iter_books(), ctx.limit):
        yield (
            book.book_id,
            book.title,
            book.author,
            book.additional_authors,
            book.date_read,
            book.date_added,
            book.shelves,
            book.exclusive_shelf,
            book.my_rating,
            book.average_rating,
            book.pages,
            book.year_published,
            book.original_year_published,
            book.publisher,
            book.binding,
            book.read_count,
            book.owned_copies,
            book.source,
        )


def _health_sleep_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    cfg = get_config()
    export_path = cfg.exports_root / "health" / "raw" / "samsung-health"
    for session in _maybe_limit(health.iter_samsung_sleep_sessions(export_path), ctx.limit):
        yield (session.start_time, session.duration_minutes)


def _health_weight_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    cfg = get_config()
    export_path = cfg.exports_root / "health" / "raw" / "samsung-health"
    for entry in _maybe_limit(health.iter_samsung_weight_entries(export_path), ctx.limit):
        yield (entry.recorded_at, entry.weight)


def _instrumentation_terminal_session_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(instrumentation.iter_terminal_sessions(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.session_id,
            record.schema_generation,
            _parse_dt(record.created_at),
            _parse_dt(record.finished_at),
            record.duration_seconds,
            record.active_seconds,
            record.idle_seconds,
            record.command_count,
            record.event_count,
            record.command,
            record.title,
            record.shell,
            record.term,
            record.term_type,
            record.term_cols,
            record.term_rows,
            record.host,
            record.user,
            record.terminal,
            record.tty,
            record.start_cwd,
            record.final_cwd,
            record.project_root,
            record.final_project_root,
            record.repo_root,
            record.final_repo_root,
            record.repo_branch,
            record.final_repo_branch,
            record.repo_commit,
            record.final_repo_commit,
            record.repo_dirty,
            record.final_repo_dirty,
            record.exit_code,
            record.exit_reason,
            record.recorder_exit_code,
            record.cleanup_escalated,
            record.manifest_path,
            record.events_path,
            record.has_events,
            record.timing_source,
            record.quality_status,
            _json_dumps(record.quality_flags),
            _json_dumps(record.field_sources),
        )


def _instrumentation_terminal_event_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(instrumentation.iter_terminal_session_events(), ctx.limit):
        yield (
            record.session_id,
            record.cast_path,
            record.schema_generation,
            record.source,
            _parse_dt(record.time),
            record.type,
            record.pwd,
            record.project_root,
            record.repo_root,
            record.repo_branch,
            record.repo_commit,
            record.repo_dirty,
            record.exit_code,
            record.payload.get("command") or record.payload.get("cmd"),
            record.payload.get("duration_ms"),
            _json_dumps(record.payload),
        )


def _instrumentation_audio_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(instrumentation.iter_audio_recordings(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.sha256,
            _parse_dt(record.created_at),
            record.duration_seconds,
            record.format,
            record.channels,
            record.sample_rate,
        )


def _instrumentation_screen_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(instrumentation.iter_screenshots(), ctx.limit):
        yield (
            record.path,
            record.size_bytes,
            record.sha256,
            _parse_dt(record.created_at),
            record.width,
            record.height,
            record.format,
        )


def _polylogue_docs_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for doc in _maybe_limit(polylogue.iter_documents(), ctx.limit):
        yield (
            doc.provider,
            str(doc.path),
            doc.modified_at,
            doc.size_bytes,
        )


def _polylogue_runs_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for run in _maybe_limit(polylogue.iter_runs(), ctx.limit):
        yield (
            run.run_id,
            run.timestamp,
            _json_dumps(run.counts),
            _json_dumps(run.drift),
            run.indexed,
            run.index_error,
            run.duration_ms,
            str(run.path),
        )


def _polylogue_session_profile_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    """One row per conversation — rich semantic profile."""
    start = ctx.since or (
        datetime.combine(date.fromisoformat(ctx.start_date), datetime.min.time(), tzinfo=timezone.utc)
        if ctx.start_date else None
    )
    end = ctx.until or (
        datetime.combine(date.fromisoformat(ctx.end_date), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        if ctx.end_date else None
    )
    for profile in _maybe_limit(polylogue.iter_session_profiles(start=start, end=end), ctx.limit):
        dominant_kind = None
        if profile.work_events:
            from collections import Counter as _Counter
            _kind_counts: _Counter[str] = _Counter()
            for we in profile.work_events:
                _kind_counts[we.kind.value if hasattr(we.kind, "value") else str(we.kind)] += 1
            dominant_kind = _kind_counts.most_common(1)[0][0]
        yield (
            profile.conversation_id,
            profile.provider,
            profile.title,
            profile.created_at,
            profile.message_count,
            profile.substantive_count,
            profile.word_count,
            profile.total_cost_usd,
            profile.cost_is_estimated,
            len(profile.work_events),
            dominant_kind,
            len(profile.phases),
            len(profile.decisions),
            _json_dumps(list(profile.repo_paths)),
            _json_dumps(list(profile.canonical_projects)),
            _json_dumps(list(profile.languages_detected)),
            profile.is_continuation,
            profile.continuation_depth,
            profile.thread_id,
            profile.first_message_at,
            profile.last_message_at,
            profile.wall_duration_ms,
            _json_dumps(list(profile.auto_tags)),
        )


def _polylogue_work_event_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    """One row per WorkEvent — enables GROUP BY kind queries across all time."""
    start = ctx.since or (
        datetime.combine(date.fromisoformat(ctx.start_date), datetime.min.time(), tzinfo=timezone.utc)
        if ctx.start_date else None
    )
    end = ctx.until or (
        datetime.combine(date.fromisoformat(ctx.end_date), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        if ctx.end_date else None
    )
    count = 0
    for profile in polylogue.iter_session_profiles(start=start, end=end):
        for idx, event in enumerate(profile.work_events):
            if ctx.limit and count >= ctx.limit:
                return
            count += 1
            yield (
                profile.conversation_id,
                profile.provider,
                profile.created_at,
                idx,
                event.kind.value if hasattr(event.kind, "value") else str(event.kind),
                event.confidence,
                event.start_index,
                event.end_index,
                event.summary,
                _json_dumps(list(event.file_paths)),
                _json_dumps(list(event.tools_used)),
            )


def _polylogue_work_thread_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    """One row per WorkThread — groups continuation chains."""
    from polylogue.lib.threads import build_session_threads

    start = ctx.since or (
        datetime.combine(date.fromisoformat(ctx.start_date), datetime.min.time(), tzinfo=timezone.utc)
        if ctx.start_date else None
    )
    end = ctx.until or (
        datetime.combine(date.fromisoformat(ctx.end_date), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        if ctx.end_date else None
    )
    all_profiles = list(polylogue.iter_session_profiles(start=start, end=end))
    threads = build_session_threads(all_profiles)
    for thread in _maybe_limit(threads, ctx.limit):
        yield (
            thread.thread_id,
            thread.root_id,
            len(thread.session_ids),
            thread.depth,
            thread.branch_count,
            thread.start_time,
            thread.end_time,
            thread.wall_duration_ms,
            thread.total_messages,
            thread.total_cost_usd,
            thread.dominant_project,
            _json_dumps(thread.work_event_breakdown),
        )


def _polylogue_session_tag_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    """One row per tag per session (auto-inferred + manual)."""
    start = ctx.since or (
        datetime.combine(date.fromisoformat(ctx.start_date), datetime.min.time(), tzinfo=timezone.utc)
        if ctx.start_date else None
    )
    end = ctx.until or (
        datetime.combine(date.fromisoformat(ctx.end_date), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        if ctx.end_date else None
    )
    count = 0
    for profile in polylogue.iter_session_profiles(start=start, end=end):
        for tag in profile.auto_tags:
            if ctx.limit and count >= ctx.limit:
                return
            count += 1
            yield (profile.conversation_id, tag, "auto")
        for tag in profile.tags:
            if ctx.limit and count >= ctx.limit:
                return
            count += 1
            yield (profile.conversation_id, tag, "manual")


def _raindrop_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for bookmark in _maybe_limit(raindrop.iter_bookmarks(), ctx.limit):
        yield (
            bookmark.id,
            bookmark.title,
            bookmark.url,
            bookmark.folder,
            _json_dumps(bookmark.tags),
            bookmark.created,
            bookmark.note,
            bookmark.excerpt,
            bookmark.cover,
            bookmark.favorite,
            _json_dumps(bookmark.raw),
        )


def _reddit_comment_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for comment in _maybe_limit(reddit.iter_comments(), ctx.limit):
        yield (
            comment.id,
            comment.created,
            comment.subreddit,
            comment.body,
            comment.permalink,
            comment.parent,
            comment.gildings,
            comment.source,
        )


def _reddit_post_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for post in _maybe_limit(reddit.iter_posts(), ctx.limit):
        yield (
            post.id,
            post.created,
            post.subreddit,
            post.title,
            post.body,
            post.url,
            post.gildings,
            post.source,
        )


def _reddit_message_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for message in _maybe_limit(reddit.iter_message_headers(), ctx.limit):
        yield (
            message.id,
            message.created,
            message.thread_id,
            message.sender,
            message.recipient,
            message.permalink,
            message.source,
        )


def _reddit_saved_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for saved in _maybe_limit(reddit.iter_saved_posts(), ctx.limit):
        yield (saved.id, saved.permalink, saved.kind, saved.source)
    for saved in _maybe_limit(reddit.iter_saved_comments(), ctx.limit):
        yield (saved.id, saved.permalink, saved.kind, saved.source)


def _reddit_votes_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for vote in _maybe_limit(reddit.iter_post_votes(), ctx.limit):
        yield (vote.id, vote.permalink, vote.direction, vote.kind, vote.source)
    for vote in _maybe_limit(reddit.iter_comment_votes(), ctx.limit):
        yield (vote.id, vote.permalink, vote.direction, vote.kind, vote.source)


def _sessions_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for record in _maybe_limit(sessions.iter_sessions(), ctx.limit):
        yield (
            record.date,
            record.provider,
            record.label,
            record.doc_path,
            record.highlights,
        )


def _session_summaries_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for summary in _maybe_limit(session_summaries_source.iter_session_summaries(), ctx.limit):
        yield (
            str(summary.summary_path),
            summary.source_path,
            summary.provider,
            summary.title,
            summary.timeframe,
            summary.summary,
            summary.generated_at,
            len(summary.highlights),
            len(summary.decisions),
            len(summary.follow_ups),
            len(summary.action_items),
            len(summary.risks),
            _json_dumps(summary.highlights),
            _json_dumps(summary.decisions),
            _json_dumps(summary.follow_ups),
            _json_dumps(summary.action_items),
            _json_dumps(summary.risks),
            _json_dumps(summary.raw_references),
        )


def _sleep_entries_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for entry in _maybe_limit(sleep.iter_sleep(), ctx.limit):
        yield (
            entry.date,
            entry.total_minutes,
            entry.avg_score,
            len(entry.segments),
        )


def _sleep_segments_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for segment in _maybe_limit(sleep.iter_segments(), ctx.limit):
        yield (
            segment.start,
            segment.end,
            segment.duration_minutes,
            segment.score,
            segment.device,
            segment.comment,
        )


def _spotify_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for stream in _maybe_limit(spotify.iter_streams(), ctx.limit):
        yield (
            stream.end_time,
            stream.artist,
            stream.track,
            stream.ms_played,
            stream.platform,
            stream.context,
            stream.source_file,
        )


def _substack_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for post in _maybe_limit(substack.iter_posts(), ctx.limit):
        yield (
            post.source,
            str(post.path),
            post.published_at,
            post.slug,
            post.title,
            post.format,
            post.content,
        )


def _takeout_archives_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    cfg = get_config()
    root = cfg.exports_root / "google" / "raw" / "takeout"
    if not root.exists():
        return iter(())

    def generator() -> Iterator[Tuple]:
        candidates = list(root.glob("*.tgz"))
        candidates.extend(root.glob("*.tar.gz"))
        candidates.extend(root.glob("*.zip"))
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            parts = takeout.expand_takeout_parts(path)
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            yield (str(path), mtime, len(parts))

    return _maybe_limit(generator(), ctx.limit)


def _webhistory_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    start_date = ctx.start_date
    end_date = ctx.end_date
    if start_date is None and ctx.since is not None:
        start_date = ctx.since.date().isoformat()
    if end_date is None and ctx.until is not None:
        end_date = ctx.until.date().isoformat()

    for entry in _maybe_limit(
        webhistory.iter_entries(start_date=start_date, end_date=end_date),
        ctx.limit,
    ):
        iso_time = _parse_dt(entry.get("iso_time"))
        url = entry.get("url") or ""
        title = entry.get("title") or ""
        source_file = entry.get("_source_file") or entry.get("source") or ""
        yield (url, title, iso_time, source_file, _json_dumps(entry))


def _webhistory_raw_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for entry in _maybe_limit(webhistory_raw.iter_entries(), ctx.limit):
        yield (
            entry.timestamp,
            entry.url,
            entry.title,
            entry.source_file,
            entry.payload_json,
        )


def _wykop_entries_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for entry in _maybe_limit(wykop.iter_entries(), ctx.limit):
        yield (
            entry.id,
            entry.created_at,
            entry.url,
            entry.content,
            _json_dumps(entry.tags),
            entry.votes_up,
            entry.votes_down,
        )


def _wykop_entry_comments_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for comment in _maybe_limit(wykop.iter_entry_comments(), ctx.limit):
        yield (
            comment.id,
            comment.created_at,
            comment.entry_id,
            comment.url,
            comment.content,
            comment.rating,
        )


def _wykop_link_comments_rows(ctx: WarehouseContext) -> Iterator[Tuple]:
    for comment in _maybe_limit(wykop.iter_link_comments(), ctx.limit):
        yield (
            comment.id,
            comment.created_at,
            comment.url,
            comment.content,
            comment.rating,
            comment.link_id,
            comment.link_title,
            comment.link_url,
            _json_dumps(comment.tags),
        )
