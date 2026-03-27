from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Iterator, Sequence

from lynchpin.core.config import get_config
from lynchpin.sources.captures import webhistory as lp_webhistory
from lynchpin.sources.exports import goodreads as lp_goodreads
from lynchpin.sources.exports import health as lp_health
from lynchpin.sources.exports import raindrop as lp_raindrop
from lynchpin.sources.exports import reddit as lp_reddit
from lynchpin.sources.exports import spotify as lp_spotify
from lynchpin.sources.exports import takeout_archives as lp_takeout_archives
from lynchpin.sources.exports import takeout_common as lp_takeout_common
from lynchpin.sources.exports import takeout_life as lp_takeout_life
from lynchpin.sources.exports import takeout_youtube as lp_takeout_youtube
from lynchpin.sources.exports import wykop as lp_wykop
from lynchpin.sources.indices import gitstats as lp_gitstats
from lynchpin.sources.libraries import finance as lp_finance
from lynchpin.sources.libraries import knowledgebase as lp_knowledgebase

from .life_range_models import (
    LifeFinanceSummary,
    LifeGitSummary,
    LifeGoodreadsSummary,
    LifeHealthSummary,
    LifeNotesSummary,
    LifeRangeEvidence,
    LifeRangeInputs,
    LifeWebHistorySummary,
)
from .life_summary_context import build_recent_context_summaries


@contextmanager
def _stage(label: str) -> Iterator[None]:
    start = time.monotonic()
    print(f"[life-range] {label}…", file=sys.stderr, flush=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        print(f"[life-range] {label} done in {elapsed:.1f}s", file=sys.stderr, flush=True)


def collect_life_range_evidence(
    *,
    start_month: str,
    end_month: str,
    months: Sequence[str],
    inputs: LifeRangeInputs,
) -> LifeRangeEvidence:
    cfg = get_config()

    with _stage("Parse Reddit"):
        reddit = lp_reddit.summarize_activity(
            start_month=start_month,
            end_month=end_month,
            comments_paths=[inputs.reddit_comments] if inputs.reddit_comments else None,
            posts_paths=[inputs.reddit_posts] if inputs.reddit_posts else None,
            message_paths=[inputs.reddit_messages] if inputs.reddit_messages else None,
            tokenize_text=lp_takeout_common.tokenize_topic,
        )

    with _stage("Parse Wykop"):
        wykop = lp_wykop.summarize_activity(
            start_month=start_month,
            end_month=end_month,
            link_comments_path=inputs.wykop_link_comments,
            entries_path=inputs.wykop_entries,
            entry_comments_path=inputs.wykop_entry_comments,
            tokenize_text=lp_takeout_common.tokenize_topic,
        )

    with _stage("Parse webhistory"):
        webhistory = _collect_webhistory_summary(start_month=start_month, end_month=end_month, inputs=inputs)

    resolved_spotify_dir = inputs.spotify_dir or cfg.spotify_root
    with _stage("Parse bookmarks/media"):
        raindrop_counts = lp_raindrop.summarize_bookmarks(
            start_month=start_month,
            end_month=end_month,
            csv_path=inputs.raindrop_bookmarks,
        )
        goodreads = _collect_goodreads_summary(start_month=start_month, end_month=end_month, inputs=inputs)
        spotify = lp_spotify.summarize_streaming(start_month, end_month, root=resolved_spotify_dir)

    with _stage("Parse finance"):
        revolut_out_annotated, revolut_in_annotated = lp_finance.parse_revolut_statement(
            inputs.revolut_annotated,
            start_month,
            end_month,
        )
        revolut_out_recent, revolut_in_recent = lp_finance.parse_revolut_statement(
            inputs.revolut_recent,
            start_month,
            end_month,
        )
        mbank_personal_out, mbank_personal_in = lp_finance.parse_mbank_operations(
            inputs.mbank_personal,
            start_month,
            end_month,
        )
        mbank_business_out, mbank_business_in = lp_finance.parse_mbank_operations(
            inputs.mbank_business,
            start_month,
            end_month,
        )
        finance = LifeFinanceSummary(
            ledger_expenses=lp_finance.parse_ledger_expenses(inputs.ledger, start_month, end_month),
            revolut_out_annotated=revolut_out_annotated,
            revolut_in_annotated=revolut_in_annotated,
            revolut_out_recent=revolut_out_recent,
            revolut_in_recent=revolut_in_recent,
            mbank_personal_out=mbank_personal_out,
            mbank_personal_in=mbank_personal_in,
            mbank_business_out=mbank_business_out,
            mbank_business_in=mbank_business_in,
        )

    with _stage("Parse health"):
        sleep_sessions, sleep_total_hours = lp_health.parse_samsung_health_sleep(
            inputs.samsung_health_export,
            start_month,
            end_month,
        )
        health = LifeHealthSummary(
            sleep_sessions=sleep_sessions,
            sleep_total_hours=sleep_total_hours,
            weight_values=lp_health.parse_samsung_health_weight(
                inputs.samsung_health_export,
                start_month,
                end_month,
            ),
        )

    with _stage("Parse notes"):
        notes = LifeNotesSummary(
            onenote_counts=lp_knowledgebase.summarize_onenote_journal_entries(
                inputs.onenote_journal,
                start_month,
                end_month,
            ),
            substance_headings=lp_knowledgebase.summarize_substance_log_headings(
                inputs.substance_log,
                start_month,
                end_month,
            ),
        )

    with _stage("Parse git activity"):
        git_repos = tuple(lp_gitstats.active_repo_paths())
        git_commit_counts, git_commit_repos = lp_gitstats.summarize_commit_activity(
            start_month=start_month,
            end_month=end_month,
            repos=git_repos,
        )
        git = LifeGitSummary(repos=git_repos, commit_counts=git_commit_counts, commit_repos=git_commit_repos)

    with _stage("Discover Google Takeout archives"):
        resolved_takeout_root = inputs.takeout_root or (cfg.exports_root / "google" / "raw" / "takeout")
        takeout_paths = tuple(
            lp_takeout_archives.resolve_archives(
                explicit_seeds=list(inputs.takeout_paths),
                root=resolved_takeout_root,
            )
        )
        if not takeout_paths:
            raise FileNotFoundError(
                f"No Google Takeout archives found (expected takeout*.tgz under {resolved_takeout_root})."
            )

    with _stage(f"Parse Google Takeout ({len(takeout_paths)} archives)"):
        takeout = lp_takeout_life.parse_life_takeouts(
            list(takeout_paths),
            start_month=start_month,
            end_month=end_month,
        )

    youtube_oembed_by_id = lp_takeout_youtube.load_youtube_oembed_cache(inputs.youtube_oembed_cache)
    context_months, context_window = build_recent_context_summaries(months)

    return LifeRangeEvidence(
        reddit=reddit,
        wykop=wykop,
        webhistory=webhistory,
        raindrop_counts=raindrop_counts,
        goodreads=goodreads,
        spotify=spotify,
        finance=finance,
        health=health,
        notes=notes,
        git=git,
        takeout_paths=takeout_paths,
        takeout=takeout,
        youtube_oembed_by_id=youtube_oembed_by_id,
        context_months=context_months,
        context_window=context_window,
        resolved_spotify_dir=resolved_spotify_dir,
    )


def _collect_webhistory_summary(
    *,
    start_month: str,
    end_month: str,
    inputs: LifeRangeInputs,
) -> LifeWebHistorySummary:
    if inputs.webhistory_gestalt_dir is not None and inputs.webhistory_gestalt_dir.exists():
        source = "gestalt"
        counts, domains, reddit_subs, title_tokens = lp_webhistory.summarize_gestalt_dir(
            inputs.webhistory_gestalt_dir,
            start_month,
            end_month,
        )
    else:
        source = "ndjson"
        counts, domains, reddit_subs, title_tokens = lp_webhistory.summarize_ndjson(
            inputs.webhistory,
            start_month,
            end_month,
        )
    return LifeWebHistorySummary(
        source=source,
        counts=counts,
        domains=domains,
        reddit_subs=reddit_subs,
        title_tokens=title_tokens,
    )


def _collect_goodreads_summary(
    *,
    start_month: str,
    end_month: str,
    inputs: LifeRangeInputs,
) -> LifeGoodreadsSummary:
    read_counts, added_counts, authors_read, titles_read = lp_goodreads.summarize_library(
        start_month,
        end_month,
        path=inputs.goodreads_library,
    )
    return LifeGoodreadsSummary(
        read_counts=read_counts,
        added_counts=added_counts,
        authors_read=authors_read,
        titles_read=titles_read,
    )
