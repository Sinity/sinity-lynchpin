from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .life_pipeline import iter_months

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


def as_int(value: Any) -> int:
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


def as_float(value: Any) -> float | None:
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


def md_inline_code(text: str) -> str:
    text = text or ""
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    return f"{fence}{text}{fence}"


def format_pairs(
    pairs: Any,
    *,
    limit: int | None = None,
    wrap_label: bool = False,
) -> str:
    if not isinstance(pairs, list):
        return ""
    out: list[str] = []
    for item in pairs[:limit] if limit is not None else pairs:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        label = str(item[0]) if item[0] is not None else ""
        count = item[1]
        if wrap_label:
            label = md_inline_code(label)
        out.append(f"{label} {count}")
    return "; ".join(out)


def safe_number(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def iter_pairs(value: object) -> Iterable[tuple[str, float]]:
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


def extract_month_metrics(month_data: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
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


def render_life_digest(
    payload: Mapping[str, object],
    *,
    start: str | None = None,
    end: str | None = None,
    title: str = "Month-by-month (chronological)",
    source_path: str | None = None,
) -> str:
    months: dict[str, Any] = payload.get("months") if isinstance(payload.get("months"), dict) else {}
    if not months:
        raise ValueError("Expected non-empty life timeline payload")

    backing_range = payload.get("range") if isinstance(payload.get("range"), dict) else {}
    backing_start = str(backing_range.get("start_month") or "?")
    backing_end = str(backing_range.get("end_month") or "?")

    resolved_start = start or (backing_start if backing_start != "?" else None)
    resolved_end = end or (backing_end if backing_end != "?" else None)
    if resolved_start is None or resolved_end is None:
        raise ValueError("Missing explicit start/end and payload range is unavailable")

    source_label = source_path or str(payload.get("output_path") or "<unknown>")
    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"Backed by: `{source_label}` ({backing_start} → {backing_end})")
    lines.append("")

    for month in iter_months(resolved_start, resolved_end):
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

        reddit_comments = as_int(output_rec.get("reddit_comments"))
        reddit_posts = as_int(output_rec.get("reddit_posts"))
        reddit_messages = as_int(output_rec.get("reddit_messages"))
        wykop_link_comments = as_int(output_rec.get("wykop_link_comments"))
        wykop_entries = as_int(output_rec.get("wykop_entries"))
        wykop_entry_comments = as_int(output_rec.get("wykop_entry_comments"))
        git_commits = as_int(work_rec.get("git_commits"))
        webhistory_events = as_int(intake_rec.get("webhistory_events"))
        google_searches = as_int(intake_rec.get("google_searches"))
        youtube_watch_history = as_int(intake_rec.get("youtube_watch_history"))
        youtube_search_history = as_int(intake_rec.get("youtube_search_history"))
        raindrop_bookmarks = as_int(intake_rec.get("raindrop_bookmarks"))

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

        sleep_sessions = as_int(health_rec.get("sleep_sessions"))
        sleep_total = as_float(health_rec.get("sleep_total_h"))
        sleep_avg = as_float(health_rec.get("sleep_avg_h"))
        weight_n = as_int(health_rec.get("weight_n"))
        weight_min = as_float(health_rec.get("weight_min"))
        weight_max = as_float(health_rec.get("weight_max"))

        health_bits: list[str] = []
        if sleep_sessions and sleep_total is not None:
            avg = sleep_avg if sleep_avg is not None else sleep_total / sleep_sessions
            health_bits.append(f"sleep {sleep_total:.1f}h / {sleep_sessions} sessions (avg {avg:.2f}h)")
        if weight_n and weight_min is not None and weight_max is not None:
            health_bits.append(f"weight n={weight_n} ({weight_min:.1f}–{weight_max:.1f})")
        if health_bits:
            lines.append("- Health: " + "; ".join(health_bits))

        ledger_expenses_pln = as_float(money_rec.get("ledger_expenses_pln"))
        revolut_out_pln = as_float(money_rec.get("revolut_out_pln")) or 0.0
        revolut_in_pln = as_float(money_rec.get("revolut_in_pln")) or 0.0
        mbank_personal_out_pln = as_float(money_rec.get("mbank_personal_out_pln")) or 0.0
        mbank_personal_in_pln = as_float(money_rec.get("mbank_personal_in_pln")) or 0.0
        mbank_business_out_pln = as_float(money_rec.get("mbank_business_out_pln")) or 0.0
        mbank_business_in_pln = as_float(money_rec.get("mbank_business_in_pln")) or 0.0

        money_bits: list[str] = []
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

        location_records = as_int(location_rec.get("records"))
        semantic_place_visits = as_int(location_rec.get("semantic_place_visits"))
        semantic_activity_segments = as_int(location_rec.get("semantic_activity_segments"))
        if location_records or semantic_place_visits or semantic_activity_segments:
            location_bits = [
                f"records {location_records}",
                f"semantic places {semantic_place_visits}",
                f"semantic activity segments {semantic_activity_segments}",
            ]
            top_places = format_pairs(location_rec.get("semantic_top_places"), limit=3, wrap_label=True)
            if top_places:
                location_bits.append(f"top places {top_places}")
            lines.append("- Location: " + "; ".join(location_bits))

        gmail_messages = as_int(mail_rec.get("gmail_messages"))
        if gmail_messages:
            top_domains = format_pairs(mail_rec.get("gmail_top_from_domains"), limit=4, wrap_label=True)
            top_subjects = format_pairs(mail_rec.get("gmail_top_subject_tokens"), limit=4, wrap_label=True)
            mail_bits = [f"gmail {gmail_messages}"]
            if top_domains:
                mail_bits.append(f"from {top_domains}")
            if top_subjects:
                mail_bits.append(f"subjects {top_subjects}")
            lines.append("- Mail: " + "; ".join(mail_bits))

        notes_bits: list[str] = []
        onenote = as_int(notes_rec.get("onenote_journal_entries"))
        substance = as_int(notes_rec.get("substance_log_headings"))
        if onenote:
            notes_bits.append(f"OneNote {onenote}")
        if substance:
            notes_bits.append(f"Substance {substance}")
        if notes_bits:
            lines.append("- Notes: " + "; ".join(notes_bits))

        context_rec = record.get("context") if isinstance(record.get("context"), dict) else {}
        active_h = as_float(context_rec.get("active_hours"))
        recovery_h = as_float(context_rec.get("recovery_hours"))
        if active_h is not None or recovery_h is not None:
            context_bits = []
            if active_h is not None:
                context_bits.append(f"active {active_h:.1f}h")
            if recovery_h is not None:
                context_bits.append(f"recovery {recovery_h:.1f}h")
            dominant_projects = format_pairs(context_rec.get("dominant_projects"), limit=3)
            dominant_topics = format_pairs(context_rec.get("dominant_topics"), limit=3)
            if dominant_projects:
                context_bits.append(f"projects {dominant_projects}")
            if dominant_topics:
                context_bits.append(f"topics {dominant_topics}")
            lines.append("- Context: " + "; ".join(context_bits))

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def new_bucket(label: str) -> dict[str, object]:
    return {
        "label": label,
        "months": [],
        "numeric": Counter(),
        "tops": {field: Counter() for field in TOP_FIELDS},
    }


def add_month(bucket: dict[str, object], month: str, metrics: dict[str, object]) -> None:
    bucket["months"].append(month)
    for field, value in metrics["numeric"].items():
        bucket["numeric"][field] += value
    for field, counter in metrics["tops"].items():
        bucket["tops"][field].update(counter)


def quarter_key(month: str) -> tuple[tuple[int, int], str]:
    year, month_i = (int(part) for part in month.split("-", 1))
    quarter = (month_i - 1) // 3 + 1
    return (year, quarter), f"{year} Q{quarter}"


def year_key(month: str) -> tuple[int, str]:
    year = int(month.split("-", 1)[0])
    return year, str(year)


def format_counter(counter: Counter, limit: int = 3) -> str:
    if not counter:
        return "n/a"
    parts = []
    for label, value in counter.most_common(limit):
        count_str = f"{value:.1f}" if isinstance(value, float) and not value.is_integer() else str(int(value))
        parts.append(f"{label} ({count_str})")
    return ", ".join(parts)


def describe_bucket(bucket: dict[str, object]) -> list[str]:
    numeric: Counter = bucket["numeric"]
    tops: dict[str, Counter] = bucket["tops"]
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

    lines.append(f"*Topics:* {format_counter(tops['intake_top_topic_tokens'])}")
    return lines


def render_section(title: str, buckets: list[tuple[object, dict[str, object]]], limit: int) -> str:
    if not buckets:
        return f"## {title}\nNo data available.\n"
    lines = [f"## {title}"]
    for _, bucket in buckets[:limit]:
        lines.append(f"### {bucket['label']}")
        lines.extend(describe_bucket(bucket))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_life_rollups(
    payload: Mapping[str, object],
    *,
    source_path: str | None = None,
    quarter_limit: int = 8,
    year_limit: int = 10,
    generated_at: str | None = None,
) -> str:
    months = sorted(payload.get("months", {}))
    if not months:
        raise ValueError("No months found in the life timeline payload")

    quarter_buckets: dict[tuple[int, int], dict[str, object]] = {}
    year_buckets: dict[int, dict[str, object]] = {}
    payload_months = payload["months"]
    for month in months:
        metrics = extract_month_metrics(payload_months[month])
        quarter_tuple, quarter_label = quarter_key(month)
        add_month(quarter_buckets.setdefault(quarter_tuple, new_bucket(quarter_label)), month, metrics)
        year_value, year_label = year_key(month)
        add_month(year_buckets.setdefault(year_value, new_bucket(year_label)), month, metrics)

    quarter_items = sorted(quarter_buckets.items(), key=lambda item: item[0], reverse=True)
    year_items = sorted(year_buckets.items(), key=lambda item: item[0], reverse=True)

    now = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    range_info = payload.get("range", {}) if isinstance(payload.get("range"), dict) else {}
    start_month = range_info.get("start_month", months[0])
    end_month = range_info.get("end_month", months[-1])
    source_label = source_path or str(payload.get("output_path") or "<unknown>")

    header = [
        "# Automated Life Narrative",
        "",
        f"Generated: {now}",
        f"Source: {source_label}",
        f"Range: {start_month} → {end_month}",
        "",
    ]
    sections = [
        render_section("Quarterly Highlights", quarter_items, quarter_limit),
        render_section("Annual Highlights", year_items, year_limit),
    ]
    return "\n".join(header + sections).rstrip() + "\n"
