#!/usr/bin/env python3
"""Generate quarterly and annual narrative snippets from the life timeline JSON."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import typer

app = typer.Typer(pretty_exceptions_show_locals=False)

NUMERIC_FIELDS = {
    "reddit_comments": ("output", "reddit_comments"),
    "wykop_link_comments": ("output", "wykop_link_comments"),
    "wykop_entries": ("output", "wykop_entries"),
    "wykop_entry_comments": ("output", "wykop_entry_comments"),
    "git_commits": ("work", "git_commits"),
    "webhistory_events": ("intake", "webhistory_events"),
    "google_searches": ("intake", "google_searches"),
    "youtube_watch_history": ("intake", "youtube_watch_history"),
    "spotify_hours": ("intake", "spotify_hours"),
    "sleep_total_h": ("health", "sleep_total_h"),
    "sleep_sessions": ("health", "sleep_sessions"),
}

TOP_FIELDS = {
    "git_top_repos": ("work", "git_top_repos"),
    "reddit_top_subs": ("output", "reddit_top_subs"),
    "wykop_top_tags": ("output", "wykop_top_tags"),
    "google_search_top_tokens": ("intake", "google_search_top_tokens"),
    "youtube_watch_history_top_channels": ("intake", "youtube_watch_history_top_channels"),
    "spotify_top_artists": ("intake", "spotify_top_artists"),
    "intake_top_topic_tokens": ("intake", "intake_top_topic_tokens"),
}


def safe_number(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def iter_pairs(value: object) -> Iterable[Tuple[str, float]]:
    if not value:
        return []
    pairs = []
    for entry in value:
        key = None
        count = 0.0
        if isinstance(entry, dict):
            for candidate in ("name", "label", "value", "token", "key"):
                if entry.get(candidate):
                    key = str(entry[candidate])
                    break
            for candidate in ("count", "value", "total", "score"):
                if candidate in entry:
                    count = safe_number(entry[candidate])
                    break
            if not count:
                count = 1.0
        elif isinstance(entry, (list, tuple)) and entry:
            key = str(entry[0])
            if len(entry) > 1:
                count = safe_number(entry[1])
            else:
                count = 1.0
        else:
            key = str(entry)
            count = 1.0
        if key:
            pairs.append((key, count))
    return pairs


def extract_month_metrics(month_data: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    numeric = {}
    for field, (category, key) in NUMERIC_FIELDS.items():
        numeric[field] = safe_number((month_data.get(category) or {}).get(key))
    tops = {}
    for field, (category, key) in TOP_FIELDS.items():
        counter = Counter()
        for label, count in iter_pairs((month_data.get(category) or {}).get(key)):
            counter[label] += count
        tops[field] = counter
    return {"numeric": numeric, "tops": tops}


def new_bucket(label: str) -> Dict[str, object]:
    return {
        "label": label,
        "months": [],
        "numeric": Counter(),
        "tops": {field: Counter() for field in TOP_FIELDS},
    }


def add_month(bucket: Dict[str, object], month: str, metrics: Dict[str, object]) -> None:
    bucket["months"].append(month)
    for field, value in metrics["numeric"].items():
        bucket["numeric"][field] += value
    for field, counter in metrics["tops"].items():
        bucket["tops"][field].update(counter)


def quarter_key(month: str) -> Tuple[Tuple[int, int], str]:
    year, month_i = (int(part) for part in month.split("-", 1))
    quarter = (month_i - 1) // 3 + 1
    return (year, quarter), f"{year} Q{quarter}"


def year_key(month: str) -> Tuple[int, str]:
    year = int(month.split("-", 1)[0])
    return year, str(year)


def format_counter(counter: Counter, limit: int = 3) -> str:
    if not counter:
        return "n/a"
    parts = []
    for label, value in counter.most_common(limit):
        if isinstance(value, float) and not value.is_integer():
            count_str = f"{value:.1f}"
        else:
            count_str = str(int(value))
        parts.append(f"{label} ({count_str})")
    return ", ".join(parts)


def describe_bucket(bucket: Dict[str, object]) -> List[str]:
    numeric: Counter = bucket["numeric"]
    tops: Dict[str, Counter] = bucket["tops"]
    months = sorted(bucket["months"])
    span = f"{months[0]} → {months[-1]}" if months else "n/a"
    lines = [f"*Span:* {span}"]

    output_bits = []
    if numeric["reddit_comments"]:
        output_bits.append(f"{int(numeric['reddit_comments'])} Reddit comments")
    if numeric["wykop_link_comments"] or numeric["wykop_entries"]:
        output_bits.append(
            f"{int(numeric['wykop_link_comments'])} Wykop comments / {int(numeric['wykop_entries'])} entries"
        )
    output_line = ", ".join(output_bits) if output_bits else "No notable posting captured."
    output_line += f" | Top subs: {format_counter(tops['reddit_top_subs'])}; tags: {format_counter(tops['wykop_top_tags'])}"
    lines.append(f"*Output:* {output_line}")

    work_bits = []
    if numeric["git_commits"]:
        work_bits.append(f"{int(numeric['git_commits'])} git commits")
    work_line = ", ".join(work_bits) if work_bits else "Minimal tracked git activity."
    work_line += f" | Top repos: {format_counter(tops['git_top_repos'])}"
    lines.append(f"*Work:* {work_line}")

    intake_bits = []
    if numeric["webhistory_events"]:
        intake_bits.append(f"{int(numeric['webhistory_events']):,} web events")
    if numeric["google_searches"]:
        intake_bits.append(f"{int(numeric['google_searches'])} Google searches")
    if numeric["youtube_watch_history"]:
        intake_bits.append(f"{int(numeric['youtube_watch_history'])} YouTube plays")
    intake_line = ", ".join(intake_bits) if intake_bits else "No major intake recorded."
    intake_line += f" | Search tokens: {format_counter(tops['google_search_top_tokens'])}"
    lines.append(f"*Intake:* {intake_line}")

    media_bits = []
    if numeric["spotify_hours"]:
        media_bits.append(f"{numeric['spotify_hours']:.1f} Spotify hours")
    media_line = ", ".join(media_bits) if media_bits else "No Spotify usage logged."
    media_line += (
        f" | Top artists: {format_counter(tops['spotify_top_artists'])}; "
        f"YouTube channels: {format_counter(tops['youtube_watch_history_top_channels'])}"
    )
    lines.append(f"*Media:* {media_line}")

    if numeric["sleep_sessions"]:
        total_h = numeric["sleep_total_h"]
        avg = total_h / numeric["sleep_sessions"] if numeric["sleep_sessions"] else 0
        sleep_line = f"{total_h:.1f} h over {int(numeric['sleep_sessions'])} sessions"
        if avg:
            sleep_line += f" (~{avg:.1f} h avg)"
    else:
        sleep_line = "No sleep records captured."
    lines.append(f"*Sleep:* {sleep_line}")

    topic_line = format_counter(tops["intake_top_topic_tokens"])
    lines.append(f"*Topics:* {topic_line}")
    return lines


def render_section(title: str, buckets: List[Tuple[object, Dict[str, object]]], limit: int) -> str:
    if not buckets:
        return f"## {title}\nNo data available.\n"
    lines = [f"## {title}"]
    for _, bucket in buckets[:limit]:
        lines.append(f"### {bucket['label']}")
        lines.extend(describe_bucket(bucket))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@app.command()
def main(
    life_json: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/monthly_life_latest.json"),
        help="Path to the monthly life timeline JSON.",
    ),
    output: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/narratives/life_auto_summary.md"),
        help="Where to write the generated Markdown narrative.",
    ),
    quarter_limit: int = typer.Option(8, help="How many most recent quarters to include."),
    year_limit: int = typer.Option(10, help="How many most recent years to include."),
) -> None:
    data = json.loads(life_json.read_text())
    months = sorted(data.get("months", {}))
    if not months:
        raise typer.BadParameter("No months found in the life timeline JSON.")

    quarter_buckets: Dict[Tuple[int, int], Dict[str, object]] = {}
    year_buckets: Dict[int, Dict[str, object]] = {}
    for month in months:
        metrics = extract_month_metrics(data["months"][month])
        quarter_tuple, quarter_label = quarter_key(month)
        quarter_bucket = quarter_buckets.setdefault(quarter_tuple, new_bucket(quarter_label))
        add_month(quarter_bucket, month, metrics)

        year_value, year_label = year_key(month)
        year_bucket = year_buckets.setdefault(year_value, new_bucket(year_label))
        add_month(year_bucket, month, metrics)

    quarter_items = sorted(quarter_buckets.items(), key=lambda item: item[0], reverse=True)
    year_items = sorted(year_buckets.items(), key=lambda item: item[0], reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    start_month = data.get("range", {}).get("start_month", months[0])
    end_month = data.get("range", {}).get("end_month", months[-1])
    header = [
        "# Automated Life Narrative",
        "",
        f"Generated: {now}",
        f"Source: {life_json}",
        f"Range: {start_month} → {end_month}",
        "",
    ]
    sections = [
        render_section("Quarterly Highlights", quarter_items, quarter_limit),
        render_section("Annual Highlights", year_items, year_limit),
    ]
    content = "\n".join(header + sections)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    typer.echo(f"Wrote {output}")


if __name__ == "__main__":
    app()
