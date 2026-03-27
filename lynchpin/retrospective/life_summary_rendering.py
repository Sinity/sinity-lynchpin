from __future__ import annotations

from typing import Mapping

from .life_summary_utils import _render_counter


def render_markdown(payload: Mapping[str, object]) -> str:
    generated_at = payload.get("generated_at", "<unknown>")
    months = payload.get("months") or {}
    if not isinstance(months, Mapping):
        months = {}
    range_info = payload.get("range") or {}
    start_month = range_info.get("start_month", "<unknown>") if isinstance(range_info, Mapping) else "<unknown>"
    end_month = range_info.get("end_month", "<unknown>") if isinstance(range_info, Mapping) else "<unknown>"
    output_path = payload.get("output_path")

    lines: list[str] = []
    lines.append(f"# Life timeline auto-summary ({start_month} → {end_month})")
    lines.append("")
    lines.append(f"Generated: `{generated_at}`")
    if output_path:
        lines.append(f"Backing JSON: `{output_path}`")
    lines.append("")
    for month in sorted(months.keys()):
        month_payload = months[month]
        if not isinstance(month_payload, Mapping):
            continue
        out = month_payload.get("output") or {}
        work = month_payload.get("work") or {}
        intake = month_payload.get("intake") or {}
        mail = month_payload.get("mail") or {}
        location = month_payload.get("location") or {}
        money = month_payload.get("money") or {}
        health = month_payload.get("health") or {}
        context = month_payload.get("context") or {}
        notes = month_payload.get("notes") or {}

        lines.append(f"## {month}")
        lines.append("")
        narrative = month_payload.get("narrative")
        if narrative:
            lines.append(f"> {narrative}")
            lines.append("")
        lines.append("**Snapshot**")
        lines.append("")
        lines.append(
            "- Output: "
            f"Reddit comments {out.get('reddit_comments', 0)}, posts {out.get('reddit_posts', 0)}, messages {out.get('reddit_messages', 0)}; "
            f"Wykop link-comments {out.get('wykop_link_comments', 0)}, entries {out.get('wykop_entries', 0)}, entry-comments {out.get('wykop_entry_comments', 0)}."
        )
        chat_sessions = work.get("chat_session_count", 0)
        chat_work_events = work.get("chat_work_events") or {}
        if chat_sessions:
            lines.append(
                f"- Work: git commits {work.get('git_commits', 0)}; "
                f"chat sessions {chat_sessions}, work events: {_render_counter(list(chat_work_events.items()))}."
            )
        else:
            lines.append(f"- Work: git commits {work.get('git_commits', 0)}.")
        lines.append(
            "- Intake: "
            f"Google searches {intake.get('google_searches', 0)}; "
            f"YouTube watch {intake.get('youtube_watch', 0)}, YouTube searches {intake.get('youtube_searches', 0)}; "
            f"YouTube watch-history {intake.get('youtube_watch_history', 0)}, YouTube search-history {intake.get('youtube_search_history', 0)}; "
            f"Webhistory events {intake.get('webhistory_events', 0)}; "
            f"Chrome MyActivity {intake.get('chrome_myactivity', 0)}; "
            f"Chrome History {intake.get('chrome_history_events', 0)}; "
            f"Maps MyActivity {intake.get('maps_myactivity', 0)}, Image Search MyActivity {intake.get('image_search_myactivity', 0)}, "
            f"Play Store MyActivity {intake.get('play_store_myactivity', 0)}; "
            f"Video Search MyActivity {intake.get('video_search_myactivity', 0)}, Shopping MyActivity {intake.get('shopping_myactivity', 0)}, "
            f"Travel MyActivity {intake.get('travel_myactivity', 0)}; "
            f"Raindrop bookmarks {intake.get('raindrop_bookmarks', 0)}; "
            f"Goodreads read {intake.get('goodreads_books_read', 0)}, added {intake.get('goodreads_books_added', 0)}."
        )
        other_myactivity = intake.get("myactivity_other_categories") or []
        if other_myactivity:
            lines.append(f"- Intake: other MyActivity categories (top): {_render_counter(other_myactivity)}")
        lines.append(f"- Mail: Gmail messages {mail.get('gmail_messages', 0)}.")
        lines.append(
            "- Location: "
            f"records {location.get('records', 0)}; "
            f"semantic place-visits {location.get('semantic_place_visits', 0)}, activity-segments {location.get('semantic_activity_segments', 0)}."
        )
        ledger_expenses = money.get("ledger_expenses_pln")
        if ledger_expenses is not None:
            lines.append(f"- Money: ledger expenses {ledger_expenses} PLN.")
        lines.append(
            "- Money: "
            f"Revolut out {money.get('revolut_out_pln', 0)} / in {money.get('revolut_in_pln', 0)} PLN; "
            f"mBank personal out {money.get('mbank_personal_out_pln', 0)} / in {money.get('mbank_personal_in_pln', 0)} PLN; "
            f"mBank business out {money.get('mbank_business_out_pln', 0)} / in {money.get('mbank_business_in_pln', 0)} PLN."
        )
        if health.get("sleep_sessions"):
            lines.append(
                "- Health: "
                f"Sleep sessions {health.get('sleep_sessions')}; "
                f"avg {health.get('sleep_avg_h')} h; total {health.get('sleep_total_h')} h."
            )
        if health.get("weight_n"):
            lines.append(
                "- Health: "
                f"Weight {health.get('weight_min')}–{health.get('weight_max')} kg (n={health.get('weight_n')})."
            )
        if context.get("days"):
            context_line = (
                f"- Context: "
                f"{context.get('active_hours', 0)}h active / {context.get('recovery_hours', 0)}h recovery across "
                f"{context.get('days', 0)} day(s), {context.get('chain_count', 0)} focus loops, {context.get('signal_count', 0)} focus spans."
            )
            context_chat_sessions = context.get("chat_session_count", 0)
            if context_chat_sessions:
                context_line += f" Chat sessions: {context_chat_sessions}"
                chat_cost = float(context.get("chat_cost_usd") or 0.0)
                if chat_cost > 0:
                    context_line += f", cost: ${chat_cost:.2f}"
                context_line += "."
            if context.get("episode_count"):
                context_line += f" Episodes: {context.get('episode_count')}."
            if context.get("anomaly_count"):
                context_line += f" Anomalies: {context.get('anomaly_count')}."
            bundle_ref = context.get("evidence_bundle")
            if bundle_ref:
                context_line += f" Evidence: `{bundle_ref}`."
            lines.append(context_line)
        lines.append(
            "- Notes: "
            f"OneNote journal entries {notes.get('onenote_journal_entries', 0)}; "
            f"substance log headings {notes.get('substance_log_headings', 0)}."
        )
        lines.append("")

        lines.append("**Output (top)**")
        lines.append("")
        lines.append(f"- Reddit top subs: {_render_counter(out.get('reddit_top_subs') or [])}")
        lines.append(f"- Wykop top tags: {_render_counter(out.get('wykop_top_tags') or [])}")
        lines.append(f"- Wykop entries top tags: {_render_counter(out.get('wykop_entries_top_tags') or [])}")
        lines.append(f"- Output topic tokens: {_render_counter(out.get('output_top_topic_tokens') or [])}")
        lines.append("")

        lines.append("**Work (top)**")
        lines.append("")
        lines.append(f"- Git top repos: {_render_counter(work.get('git_top_repos') or [])}")
        lines.append("")

        if context.get("days"):
            lines.append("**Recent Context**")
            lines.append("")
            lines.append(f"- Dominant modes: {_render_counter(context.get('dominant_modes') or [])}")
            lines.append(f"- Dominant projects: {_render_counter(context.get('dominant_projects') or [])}")
            if context.get("top_repos"):
                lines.append(f"- Top repos: {_render_counter(context.get('top_repos') or [])}")
            if context.get("top_paths"):
                lines.append(f"- Top paths: {_render_counter(context.get('top_paths') or [])}")
            if context.get("top_session_titles"):
                lines.append(f"- Session titles: {_render_counter(context.get('top_session_titles') or [])}")
            if context.get("avg_fragmentation") is not None:
                lines.append(f"- Avg fragmentation: {context.get('avg_fragmentation')}")
            if context.get("episode_count"):
                lines.append(
                    f"- Episodes: {context.get('episode_count')} ({', '.join(context.get('episode_labels') or []) or 'n/a'})"
                )
            if context.get("anomaly_count"):
                lines.append(
                    f"- Anomalies: {context.get('anomaly_count')} ({', '.join(context.get('anomaly_kinds') or []) or 'n/a'})"
                )
            lines.append(f"- Highlights: {', '.join(context.get('highlights') or []) or 'n/a'}")
            lines.append("")

        lines.append("**Intake (top)**")
        lines.append("")
        lines.append(f"- Webhistory top domains: {_render_counter(intake.get('webhistory_top_domains') or [])}")
        lines.append(f"- Webhistory top Reddit subs visited: {_render_counter(intake.get('webhistory_top_reddit_subs') or [])}")
        lines.append(f"- Webhistory title top tokens: {_render_counter(intake.get('webhistory_top_title_tokens') or [])}")
        lines.append(f"- Chrome History top domains: {_render_counter(intake.get('chrome_history_top_domains') or [])}")
        lines.append(f"- Chrome History top Reddit subs visited: {_render_counter(intake.get('chrome_history_top_reddit_subs') or [])}")
        lines.append(f"- Chrome History title top tokens: {_render_counter(intake.get('chrome_history_top_title_tokens') or [])}")
        lines.append(f"- Google search top tokens: {_render_counter(intake.get('google_search_top_tokens') or [])}")
        lines.append(f"- Google search top exact queries: {_render_counter(intake.get('google_search_top_queries') or [])}")
        lines.append(f"- YouTube search top tokens: {_render_counter(intake.get('youtube_search_top_tokens') or [])}")
        lines.append(f"- YouTube search top exact queries: {_render_counter(intake.get('youtube_search_top_queries') or [])}")
        lines.append(f"- YouTube watch-history top video IDs: {_render_counter(intake.get('youtube_watch_history_top_video_ids') or [])}")
        lines.append(f"- YouTube watch-history top channels: {_render_counter(intake.get('youtube_watch_history_top_channels') or [])}")
        lines.append(f"- YouTube watch-history top tokens: {_render_counter(intake.get('youtube_watch_history_top_tokens') or [])}")
        lines.append(f"- YouTube watch-history top titles: {_render_counter(intake.get('youtube_watch_history_top_titles') or [])}")
        lines.append(f"- YouTube search-history top tokens: {_render_counter(intake.get('youtube_search_history_top_tokens') or [])}")
        lines.append(f"- YouTube search-history top queries: {_render_counter(intake.get('youtube_search_history_top_queries') or [])}")
        lines.append(f"- Maps search top queries: {_render_counter(intake.get('maps_search_top_queries') or [])}")
        lines.append(f"- Video search top queries: {_render_counter(intake.get('video_search_top_queries') or [])}")
        lines.append(f"- MyActivity other categories: {_render_counter(intake.get('myactivity_other_categories') or [])}")
        lines.append(f"- Goodreads top authors read: {_render_counter(intake.get('goodreads_top_authors_read') or [])}")
        lines.append(f"- Goodreads top titles read: {_render_counter(intake.get('goodreads_top_titles_read') or [])}")
        lines.append(f"- Intake topic tokens: {_render_counter(intake.get('intake_top_topic_tokens') or [])}")
        spotify_hours = intake.get("spotify_hours")
        if spotify_hours:
            lines.append(
                f"- Spotify hours: {spotify_hours} (top artists: {', '.join(intake.get('spotify_top_artists') or [])})"
            )
        lines.append("")

        lines.append("**Mail (top)**")
        lines.append("")
        lines.append(f"- Gmail top from domains: {_render_counter(mail.get('gmail_top_from_domains') or [])}")
        lines.append(f"- Gmail top subject tokens: {_render_counter(mail.get('gmail_top_subject_tokens') or [])}")
        lines.append("")

        lines.append("**Location (top)**")
        lines.append("")
        lines.append(f"- Semantic top places: {_render_counter(location.get('semantic_top_places') or [])}")
        lines.append(f"- Semantic top activities: {_render_counter(location.get('semantic_top_activities') or [])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
