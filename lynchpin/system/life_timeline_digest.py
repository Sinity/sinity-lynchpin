#!/usr/bin/env python3
"""Render a data-dense month-by-month digest from a life-timeline JSON.

Digests are intentionally *not* tracked. Generate them on demand into ignored
paths (e.g. under `artefacts/`) to avoid churn.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Tuple

import typer

from lynchpin.system.life_timeline_paths import (
    LATEST_LIFE_TIMELINE_JSON,
    LIFE_TIMELINE_DIGEST_OUTPUT,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


def iter_months(start_month: str, end_month: str) -> Iterable[str]:
    year, month = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_i = (int(part) for part in end_month.split("-", 1))
    while (year, month) <= (end_year, end_month_i):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            month = 1
            year += 1


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except ValueError:
        return 0


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _md_inline_code(text: str) -> str:
    text = text or ""
    runs = [len(m.group(0)) for m in re.finditer(r"`+", text)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    return f"{fence}{text}{fence}"


def _fmt_pairs(
    pairs: Any,
    *,
    limit: int | None = None,
    wrap_label: bool = False,
) -> str:
    if not isinstance(pairs, list):
        return ""
    out: List[str] = []
    for item in pairs[:limit] if limit is not None else pairs:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        label = str(item[0]) if item[0] is not None else ""
        count = item[1]
        if wrap_label:
            label = _md_inline_code(label)
        out.append(f"{label} {count}")
    return "; ".join(out)


@app.command()
def main(
    life_json: Path = typer.Option(
        LATEST_LIFE_TIMELINE_JSON,
        help="Life timeline JSON (output of python -m lynchpin.system.life_timeline).",
    ),
    start: str | None = typer.Option(None, help="Start month (YYYY-MM). Defaults to the life-json range start."),
    end: str | None = typer.Option(None, help="End month (YYYY-MM). Defaults to the life-json range end."),
    output: Path = typer.Option(LIFE_TIMELINE_DIGEST_OUTPUT, help="Output markdown file."),
    title: str = typer.Option(
        "Month-by-month (chronological)",
        help="Top-level markdown header title.",
    ),
) -> None:
    payload = json.loads(life_json.read_text(encoding="utf-8"))
    months: dict[str, Any] = payload.get("months") if isinstance(payload.get("months"), dict) else {}
    if not months:
        raise typer.Exit(code=2)

    backing_range = payload.get("range") if isinstance(payload.get("range"), dict) else {}
    backing_start = str(backing_range.get("start_month") or "?")
    backing_end = str(backing_range.get("end_month") or "?")

    if start is None:
        start = backing_start if backing_start != "?" else None
    if end is None:
        end = backing_end if backing_end != "?" else None
    if start is None or end is None:
        raise typer.BadParameter("Missing --start/--end and the life-json range is unavailable.")

    lines: List[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(
        f"Backed by: `{life_json.as_posix()}` ({backing_start} \u2192 {backing_end})"
    )
    lines.append("")

    for month in iter_months(start, end):
        record = months.get(month)
        if not isinstance(record, dict):
            continue

        output_rec = record.get("output") if isinstance(record.get("output"), dict) else {}
        work_rec = record.get("work") if isinstance(record.get("work"), dict) else {}
        intake_rec = record.get("intake") if isinstance(record.get("intake"), dict) else {}
        mail_rec = record.get("mail") if isinstance(record.get("mail"), dict) else {}
        money_rec = record.get("money") if isinstance(record.get("money"), dict) else {}
        location_rec = record.get("location") if isinstance(record.get("location"), dict) else {}
        health_rec = record.get("health") if isinstance(record.get("health"), dict) else {}
        notes_rec = record.get("notes") if isinstance(record.get("notes"), dict) else {}

        reddit_comments = _as_int(output_rec.get("reddit_comments"))
        reddit_posts = _as_int(output_rec.get("reddit_posts"))
        reddit_messages = _as_int(output_rec.get("reddit_messages"))
        wykop_link_comments = _as_int(output_rec.get("wykop_link_comments"))
        wykop_entries = _as_int(output_rec.get("wykop_entries"))
        wykop_entry_comments = _as_int(output_rec.get("wykop_entry_comments"))

        git_commits = _as_int(work_rec.get("git_commits"))

        webhistory_events = _as_int(intake_rec.get("webhistory_events"))
        google_searches = _as_int(intake_rec.get("google_searches"))
        youtube_watch_history = _as_int(intake_rec.get("youtube_watch_history"))
        youtube_search_history = _as_int(intake_rec.get("youtube_search_history"))
        raindrop_bookmarks = _as_int(intake_rec.get("raindrop_bookmarks"))

        lines.append(f"### {month}")
        lines.append("")
        lines.append(
            f"- Output: Reddit c {reddit_comments}, p {reddit_posts}, m {reddit_messages}; "
            f"Wykop lc {wykop_link_comments}, e {wykop_entries}, ec {wykop_entry_comments}"
        )
        lines.append(f"- Work: git commits {git_commits}")
        lines.append(
            f"- Intake: webhistory {webhistory_events}; google searches {google_searches}; "
            f"YT watch-history {youtube_watch_history}; YT search-history {youtube_search_history}; "
            f"Raindrop {raindrop_bookmarks}"
        )

        sleep_sessions = _as_int(health_rec.get("sleep_sessions"))
        sleep_total = _as_float(health_rec.get("sleep_total_h"))
        sleep_avg = _as_float(health_rec.get("sleep_avg_h"))
        weight_n = _as_int(health_rec.get("weight_n"))
        weight_min = _as_float(health_rec.get("weight_min"))
        weight_max = _as_float(health_rec.get("weight_max"))

        health_bits: List[str] = []
        if sleep_sessions and sleep_total is not None:
            avg = sleep_avg
            if avg is None and sleep_sessions:
                avg = sleep_total / sleep_sessions
            if avg is not None:
                health_bits.append(
                    f"sleep {sleep_total:.1f}h / {sleep_sessions} sessions (avg {avg:.2f}h)"
                )
            else:
                health_bits.append(f"sleep {sleep_total:.1f}h / {sleep_sessions} sessions")
        if weight_n and weight_min is not None and weight_max is not None:
            health_bits.append(f"weight n={weight_n} ({weight_min:.1f}\u2013{weight_max:.1f})")
        if health_bits:
            lines.append("- Health: " + "; ".join(health_bits))

        ledger_expenses_pln = _as_float(money_rec.get("ledger_expenses_pln"))
        revolut_out_pln = _as_float(money_rec.get("revolut_out_pln")) or 0.0
        revolut_in_pln = _as_float(money_rec.get("revolut_in_pln")) or 0.0
        mbank_personal_out_pln = _as_float(money_rec.get("mbank_personal_out_pln")) or 0.0
        mbank_personal_in_pln = _as_float(money_rec.get("mbank_personal_in_pln")) or 0.0
        mbank_business_out_pln = _as_float(money_rec.get("mbank_business_out_pln")) or 0.0
        mbank_business_in_pln = _as_float(money_rec.get("mbank_business_in_pln")) or 0.0

        money_bits: List[str] = []
        if ledger_expenses_pln is not None and abs(ledger_expenses_pln) >= 0.01:
            money_bits.append(f"ledger exp PLN {ledger_expenses_pln:.2f}")
        if abs(revolut_out_pln) >= 0.01 or abs(revolut_in_pln) >= 0.01:
            money_bits.append(f"revolut out/in PLN {revolut_out_pln:.2f}/{revolut_in_pln:.2f}")
        if abs(mbank_personal_out_pln) >= 0.01 or abs(mbank_personal_in_pln) >= 0.01:
            money_bits.append(f"mbank personal out/in PLN {mbank_personal_out_pln:.2f}/{mbank_personal_in_pln:.2f}")
        if abs(mbank_business_out_pln) >= 0.01 or abs(mbank_business_in_pln) >= 0.01:
            money_bits.append(f"mbank business out/in PLN {mbank_business_out_pln:.2f}/{mbank_business_in_pln:.2f}")
        if money_bits:
            lines.append("- Money: " + "; ".join(money_bits))

        location_records = _as_int(location_rec.get("records"))
        semantic_place_visits = _as_int(location_rec.get("semantic_place_visits"))
        semantic_activity_segments = _as_int(location_rec.get("semantic_activity_segments"))
        if location_records or semantic_place_visits or semantic_activity_segments:
            location_bits = [
                f"records {location_records}",
                f"semantic visits {semantic_place_visits}",
                f"segments {semantic_activity_segments}",
            ]
            top_places = _fmt_pairs(location_rec.get("semantic_top_places"), limit=5)
            top_activities = _fmt_pairs(location_rec.get("semantic_top_activities"), limit=5)
            if top_places:
                location_bits.append(f"top places: {top_places}")
            if top_activities:
                location_bits.append(f"top activities: {top_activities}")
            lines.append("- Location: " + "; ".join(location_bits))

        onenote_journal_entries = _as_int(notes_rec.get("onenote_journal_entries"))
        substance_log_headings = _as_int(notes_rec.get("substance_log_headings"))
        if onenote_journal_entries or substance_log_headings:
            notes_bits = [
                f"onenote {onenote_journal_entries}",
                f"substance {substance_log_headings}",
            ]
            lines.append("- Notes: " + "; ".join(notes_bits))

        gmail_messages = _as_int(mail_rec.get("gmail_messages"))
        if gmail_messages:
            from_domains = _fmt_pairs(mail_rec.get("gmail_top_from_domains"), limit=7)
            subj_tokens = _fmt_pairs(mail_rec.get("gmail_top_subject_tokens"), limit=8)
            mail_bits: List[str] = [f"Gmail {gmail_messages}"]
            if from_domains:
                mail_bits.append(f"from: {from_domains}")
            if subj_tokens:
                mail_bits.append(f"subj: {subj_tokens}")
            lines.append("- Mail: " + "; ".join(mail_bits))

        if webhistory_events:
            web_domains = _fmt_pairs(intake_rec.get("webhistory_top_domains"), limit=8)
            web_subs = _fmt_pairs(intake_rec.get("webhistory_top_reddit_subs"), limit=10)
            web_tokens = _fmt_pairs(intake_rec.get("webhistory_top_title_tokens"), limit=12)
            if web_domains:
                lines.append(f"- Web domains: {web_domains}")
            if web_subs:
                lines.append(f"- Web Reddit subs: {web_subs}")
            if web_tokens:
                lines.append(f"- Web title tokens: {web_tokens}")

        google_tokens = _fmt_pairs(intake_rec.get("google_search_top_tokens"), limit=12)
        google_queries = _fmt_pairs(intake_rec.get("google_search_top_queries"), limit=10, wrap_label=True)
        if google_tokens:
            lines.append(f"- Google tokens: {google_tokens}")
        if google_queries:
            lines.append(f"- Google queries: {google_queries}")

        yt_channels = _fmt_pairs(intake_rec.get("youtube_watch_history_top_channels"), limit=10)
        yt_titles = _fmt_pairs(intake_rec.get("youtube_watch_history_top_titles"), limit=8, wrap_label=True)
        if yt_channels:
            lines.append(f"- YouTube channels: {yt_channels}")
        if yt_titles:
            lines.append(f"- YouTube titles: {yt_titles}")

        yt_search_tokens = _fmt_pairs(intake_rec.get("youtube_search_history_top_tokens"), limit=12)
        yt_search_queries = _fmt_pairs(intake_rec.get("youtube_search_history_top_queries"), limit=10, wrap_label=True)
        if yt_search_tokens:
            lines.append(f"- YouTube search tokens: {yt_search_tokens}")
        if yt_search_queries:
            lines.append(f"- YouTube search queries: {yt_search_queries}")

        if reddit_comments:
            commented_subs = _fmt_pairs(output_rec.get("reddit_top_subs"), limit=10)
            if commented_subs:
                lines.append(f"- Reddit subs commented: {commented_subs}")

        if wykop_link_comments:
            wykop_tags = _fmt_pairs(output_rec.get("wykop_top_tags"), limit=10)
            if wykop_tags:
                lines.append(f"- Wykop tags (commented links): {wykop_tags}")

        output_tokens = _fmt_pairs(output_rec.get("output_top_topic_tokens"), limit=12)
        if output_tokens:
            lines.append(f"- Output topic tokens: {output_tokens}")

        if git_commits:
            git_repos = _fmt_pairs(work_rec.get("git_top_repos"), limit=10)
            if git_repos:
                lines.append(f"- Git repos: {git_repos}")

        other_cats = _fmt_pairs(intake_rec.get("myactivity_other_categories"))
        if other_cats:
            lines.append(f"- Other MyActivity categories: {other_cats}")

        lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    typer.echo(
        f"Wrote {output.as_posix()} (range {start} \u2192 {end}) at {datetime.now().isoformat(timespec='seconds')}",
        err=True,
    )


if __name__ == "__main__":
    app()
