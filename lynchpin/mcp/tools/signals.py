"""Cross-source signal MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    ensure_substrate_materialized_for_read,
    json_safe as _json_safe,
    pinned_materialization_for_read,
    require_best_materialized_refresh_id,
)


def source_correlation(
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Cross-source co-occurrence matrix by project-day."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import load_source_co_occurrence

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="source_correlation")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_materialized_refresh_id(
                conn,
                "evidence_node",
                caller="source_correlation",
                tool="source_correlation",
            )

        rows = load_source_co_occurrence(conn, refresh_id=refresh_id)

    return [{"source_a": r[0], "source_b": r[1], "co_occurring_days": r[2]} for r in rows]


def source_observation_bounds() -> list[dict[str, Any]]:
    """Observed source date bounds without age-based stale scoring."""
    from datetime import date

    from lynchpin.sources.source_observations import source_observations
    from lynchpin.substrate.connection import connect, substrate_path

    substrate_dates: dict[str, date] = {}
    queries = {"spotify": "SELECT MAX(date) FROM spotify_daily"}
    try:
        with connect(substrate_path(), read_only=True) as conn:
            for source, sql in queries.items():
                try:
                    row = conn.execute(sql).fetchone()
                except Exception:
                    continue
                value = row[0] if row else None
                if isinstance(value, date):
                    substrate_dates[source] = value
    except Exception:
        substrate_dates = {}

    return [
        {
            "source": item.source,
            "available": item.available,
            "last_known_data": _json_safe(item.last_observed),
            "recommendation": item.recommendation,
            "basis": item.basis,
            "path": item.path,
        }
        for item in source_observations(substrate_dates=substrate_dates)
    ]


def cross_source_lag(
    project: str | None = None,
    refresh_id: str | None = None,
    time_window_hours: int = 24,
) -> dict[str, Any]:
    """AI-to-commit time lag distribution using file-overlap support."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import (
        load_attributed_commit_count,
        load_ai_commit_lag_stats,
    )

    materialization = (
        ensure_substrate_materialized_for_read(caller="cross_source_lag")
        if refresh_id is None
        else pinned_materialization_for_read(caller="cross_source_lag", refresh_id=refresh_id)
    )
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="cross_source_lag")
            if refresh_id is None:
                return {"error": "no data", "materialization": materialization}

        attributed_commits = load_attributed_commit_count(
            conn, refresh_id=refresh_id, project=project
        )
        stats = load_ai_commit_lag_stats(
            conn, refresh_id=refresh_id, time_window_hours=time_window_hours, project=project
        )

    if stats is None:
        stats = (0, None, None, None, None)
    caveats = []
    if attributed_commits and not stats[0]:
        caveats.append(
            "commit_fact has AI-attributed commits, but no suffix path overlap "
            f"with ai_work_event rows inside +/-{time_window_hours}h in this "
            "refresh; lag is unavailable rather than zero"
        )
    return {
        "materialization": materialization,
        "pairs": stats[0],
        "attributed_commits": attributed_commits,
        "time_window_hours": time_window_hours,
        "min_hours": stats[1],
        "median_hours": stats[2],
        "mean_hours": stats[3],
        "max_hours": stats[4],
        "caveats": caveats,
    }


def project_health(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Project health composite from velocity, review, churn, and symbol activity."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import load_project_health_rows

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="project_health")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_materialized_refresh_id(
                conn,
                "project_day_correlation",
                caller="project_health",
                tool="project_health",
            )

        rows = load_project_health_rows(conn, refresh_id=refresh_id, project=project)

    return [
        {
            "project": r[0],
            "commits": r[1],
            "active_days": r[2],
            "prs": r[3],
            "avg_merge_hours": r[4],
            "symbol_changes": r[5],
            "daily_churn_rate": r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# verify_vs_edit_ratio: per-session greedy-batching adherence metric
# ---------------------------------------------------------------------------

# Tool-name classification. The polylogue work-event grain only carries
# tool *names* (not Bash command bodies), so the classification is purely
# nominal. Documented caveats:
#   * Bash is treated as a verify proxy. It overcounts in sessions that use
#     Bash heavily for non-verification (ls, mv, git, mkdir). For sessions
#     dominated by Rust/Python development this approximation is reasonable.
#   * Edit tools are unambiguous.
#   * Reads/Greps/Globs/TaskCreate/TodoWrite are neither edit nor verify and
#     are ignored from the ratio.
_VERIFY_TOOL_NAMES = frozenset({
    "Bash",  # proxy; see caveat above
    "bash",  # codex-normalized
})
_EDIT_TOOL_NAMES = frozenset({
    "Edit",
    "Write",
    "NotebookEdit",
    "MultiEdit",
    "apply_patch",  # codex
    "update_file",
    "create_file",
})


def verify_vs_edit_ratio(
    since: str | None = None,
    project: str | None = None,
    min_edits: int = 1,
) -> list[dict[str, Any]]:
    """Per-session verify/edit tool-call ratio (greedy-batching adherence).

    Counts verify-class tool calls (Bash as proxy; see caveats) against
    edit-class tool calls (Edit, Write, NotebookEdit, apply_patch, ...) per
    polylogue conversation. Low ratio = batched; high ratio = reactive
    edit-and-test cycling.

    Substrate: lynchpin.sources.polylogue.work_events() — the polylogue
    session-work-event insight product. Tool-name counts are derived from
    the event ``tools_used`` arrays. Per-event tool *counts* are not stored
    upstream (only set membership per event), so each unique tool name in
    an event contributes 1 occurrence. This biases ratios toward 1.0 for
    sessions where the same tool is reused many times within one event; it
    is still useful for ranking sessions relative to each other.

    Parameters:
        since:      ISO date (inclusive). Defaults to 7 days ago.
        project:    optional repo/project filter (matched against any file_path
                    prefix on the event).
        min_edits:  only return sessions with at least this many edit-class
                    tool occurrences. Filters out chat-only / read-only
                    sessions. Default 1.

    Returns rows sorted by ratio DESC (worst-first):
        session_id, provider, repo_hint, edit_count, verify_count, ratio,
        started_at, ended_at, event_count.
    """
    from collections import defaultdict
    from datetime import date as _date, timedelta

    from lynchpin.sources.polylogue import work_events

    since_d: _date
    if since:
        since_d = _date.fromisoformat(since)
    else:
        since_d = _date.today() - timedelta(days=7)

    events = work_events(start=since_d)

    # Aggregate per conversation_id.
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "provider": None,
            "repo_hint": None,
            "edit_count": 0,
            "verify_count": 0,
            "started_at": None,
            "ended_at": None,
            "event_count": 0,
            "_paths": set(),
        }
    )

    for ev in events:
        bucket = agg[ev.conversation_id]
        bucket["provider"] = ev.provider
        bucket["event_count"] += 1
        for tool in ev.tools_used:
            if tool in _EDIT_TOOL_NAMES:
                bucket["edit_count"] += 1
            elif tool in _VERIFY_TOOL_NAMES:
                bucket["verify_count"] += 1
        for fp in ev.file_paths:
            bucket["_paths"].add(fp)
        if ev.start is not None:
            cur = bucket["started_at"]
            if cur is None or ev.start < cur:
                bucket["started_at"] = ev.start
        if ev.end is not None:
            cur = bucket["ended_at"]
            if cur is None or ev.end > cur:
                bucket["ended_at"] = ev.end

    rows: list[dict[str, Any]] = []
    for sid, b in agg.items():
        if b["edit_count"] < int(min_edits):
            continue
        paths = b.pop("_paths")
        repo_hint = _infer_repo_hint(paths)
        if project and (repo_hint is None or project not in repo_hint):
            continue
        ratio = b["verify_count"] / b["edit_count"] if b["edit_count"] else 0.0
        rows.append({
            "session_id": sid,
            "provider": b["provider"],
            "repo_hint": repo_hint,
            "edit_count": b["edit_count"],
            "verify_count": b["verify_count"],
            "ratio": round(ratio, 3),
            "started_at": _json_safe(b["started_at"]) if b["started_at"] else None,
            "ended_at": _json_safe(b["ended_at"]) if b["ended_at"] else None,
            "event_count": b["event_count"],
        })

    rows.sort(key=lambda r: (-r["ratio"], -r["edit_count"]))
    return rows


def _infer_repo_hint(paths: set[str]) -> str | None:
    """Pick the most-common /realm/project/<name>/ slug from the event paths."""
    from collections import Counter

    counter: Counter[str] = Counter()
    for p in paths:
        if not p.startswith("/realm/project/"):
            continue
        parts = p.split("/", 4)
        # ["", "realm", "project", "<name>", "rest..."]
        if len(parts) >= 4 and parts[3]:
            counter[parts[3]] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def daily_rhythm_fingerprint(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily rhythm fingerprint per project from commit timestamps."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import load_commit_rhythm_fingerprint

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="daily_rhythm_fingerprint")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="daily_rhythm_fingerprint")
            if refresh_id is None:
                return []

        rows = load_commit_rhythm_fingerprint(conn, refresh_id=refresh_id, project=project)

    results = []
    for r in rows:
        proj = r[0]
        morning, evening, night = r[1], r[3], r[4]
        weekend, total = r[5], r[7]
        mpct = morning * 100.0 / max(total, 1)
        epct = evening * 100.0 / max(total, 1)
        npct = night * 100.0 / max(total, 1)
        wpct = weekend * 100.0 / max(total, 1)

        if wpct > 30:
            pattern = "weekend-warrior"
        elif npct > 25:
            pattern = "night-owl"
        elif mpct > 45:
            pattern = "morning-person"
        elif epct > 40:
            pattern = "evening-coder"
        else:
            pattern = "9-to-5"

        results.append({
            "project": proj,
            "total_commits": total,
            "morning_pct": round(mpct, 1),
            "afternoon_pct": round(r[2] * 100.0 / max(total, 1), 1),
            "evening_pct": round(epct, 1),
            "night_pct": round(npct, 1),
            "weekend_pct": round(wpct, 1),
            "pattern": pattern,
        })

    return results


# Numeric daily metrics in the materialized operator_day matrix that are
# meaningful to correlate. Whitelisted so the column name can be safely
# interpolated into the correlation SQL.
_OPERATOR_DAY_METRICS = frozenset({
    "aw_active_hours", "aw_deep_work_min", "aw_fragmentation",
    "git_commits", "git_lines_added", "git_lines_deleted", "svn_commits",
    "stress_mean", "hr_mean_bpm", "hr_resting_bpm", "hrv_sdnn", "hrv_rmssd",
    "sleep_hours", "sleep_score", "steps",
    "substance_doses",
    "wykop_comments", "reddit_comments", "sms_sent", "messenger_sent",
    "outlook_inbox", "polylogue_sessions", "polylogue_engaged_minutes",
    "web_visits", "web_social_visits", "shell_commands", "spotify_hours",
    "keylog_keypresses", "clipboard_entries", "irc_lines", "raw_log_entries",
    "substance_unique_count", "stress_min", "stress_max",
    "web_unique_domains", "polylogue_messages",
    "weather_temp_mean", "weather_precip_mm", "weather_sunshine_hours", "weather_cloud_pct",
    "mood_sentiment", "mood_message_count",
    "web_nsfw_share", "web_distraction_ratio",
    "audio_energy", "audio_valence", "audio_danceability",
    "aw_outage_hours", "svn_files_changed",
    "keylog_sessions", "keylog_keybind_uses",
    "spo2_pct", "skin_temp_c",
})


def operator_day_metrics() -> list[str]:
    """List the correlatable daily metrics available in the operator_day matrix."""
    return sorted(_OPERATOR_DAY_METRICS)


def operator_day_correlation(
    metric_a: str,
    metric_b: str,
    max_lag_days: int = 3,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Lagged Pearson correlation between two operator_day metrics:
    metric_a[day] vs metric_b[day + lag]. Positive lag means metric_b follows
    metric_a.

    Reads the pre-materialized operator_day substrate table (fast), not the
    live matrix. NULL/absent days are excluded pairwise (missing != zero,
    never coerced to 0). p-values are Benjamini-Hochberg FDR-corrected across
    the lag family. Results are lagged ASSOCIATION, not causation.
    """
    import math

    if metric_a not in _OPERATOR_DAY_METRICS or metric_b not in _OPERATOR_DAY_METRICS:
        return {
            "error": "unknown metric; call operator_day_metrics() for valid names",
            "metric_a": metric_a,
            "metric_b": metric_b,
        }
    max_lag = max(0, min(int(max_lag_days), 14))

    from lynchpin.core.analytics import _benjamini_hochberg, _t_test_p
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import (
        load_operator_day_window,
        load_operator_day_lag_correlation,
    )

    materialization = (
        ensure_substrate_materialized_for_read(caller="operator_day_correlation")
        if refresh_id is None
        else pinned_materialization_for_read(caller="operator_day_correlation", refresh_id=refresh_id)
    )
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "operator_day", caller="operator_day_correlation")
            if refresh_id is None:
                return {
                    "error": "operator_day is not materialized; run the substrate refresh",
                    "metric_a": metric_a,
                    "metric_b": metric_b,
                    "materialization": materialization,
                }
        span = load_operator_day_window(conn, refresh_id=refresh_id)
        lags: list[dict[str, Any]] = []
        for lag in range(0, max_lag + 1):
            row = load_operator_day_lag_correlation(
                conn, refresh_id=refresh_id, metric_a=metric_a, metric_b=metric_b, lag=lag
            )
            if row is None:
                continue
            r, n = row[0], row[1]
            if r is None or n is None or n < 3:
                continue
            if abs(r) >= 0.99999:
                p = 0.0
            else:
                t_stat = r * math.sqrt((n - 2) / (1.0 - r * r))
                p = _t_test_p(t_stat, n - 2)
            lags.append({"lag_days": lag, "r": round(float(r), 4), "n": int(n), "_p": p})

    if lags:
        q_by_idx = _benjamini_hochberg({i: x["_p"] for i, x in enumerate(lags)})
        for i, x in enumerate(lags):
            x["p_value"] = round(x.pop("_p"), 4)
            x["q_value"] = round(q_by_idx[i], 4)
            x["significant"] = q_by_idx[i] < 0.05

    return {
        "metric_a": metric_a,
        "metric_b": metric_b,
        "materialization": materialization,
        "interpretation": (
            f"{metric_a}[day] vs {metric_b}[day+lag]; positive lag = "
            f"{metric_b} follows {metric_a}"
        ),
        "covered_start": str(span[0]) if span and span[0] is not None else None,
        "covered_end": str(span[1]) if span and span[1] is not None else None,
        "days_materialized": int(span[2]) if span and span[2] is not None else 0,
        "lags": lags,
        "caveats": [
            "Lagged association, NOT causation.",
            "NULL/absent days excluded pairwise (missing != zero).",
            "p-values are FDR-corrected across the lag family; n is the "
            "complete-pair count at each lag.",
        ],
    }


def operator_day_rows(
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
    columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Date-filtered rows from the operator_day cross-source matrix.

    Returns one dict per day. ``columns`` narrows to a subset of available
    fields (see rhythm?view=metrics for the full numeric list; also valid:
    ``date``, ``sources_present``, ``mood_dominant_emotion``, ``web_top_category``).
    NULL values are preserved as None so missing stays distinct from zero.

    Parameters:
        start:      ISO date (inclusive), e.g. ``"2026-01-01"``.
        end:        ISO date (inclusive), e.g. ``"2026-03-31"``.
        refresh_id: pin a specific substrate refresh; defaults to latest.
        columns:    list of column names to return; all columns when omitted.
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import load_operator_day_rows

    materialization = (
        ensure_substrate_materialized_for_read(caller="operator_day_rows")
        if refresh_id is None
        else pinned_materialization_for_read(caller="operator_day_rows", refresh_id=refresh_id)
    )
    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "operator_day", caller="operator_day_rows")
            if refresh_id is None:
                return []
        try:
            rows = load_operator_day_rows(
                conn, refresh_id=refresh_id, start=start_d, end=end_d, columns=columns
            )
        except ValueError as exc:
            return [{"error": str(exc), "materialization": materialization}]

    for row in rows:
        for k, v in row.items():
            if v is not None:
                row[k] = _json_safe(v)
    return rows


def sources(
    view: str = "correlation",
    refresh_id: str | None = None,
) -> Any:
    """Source signal data. view: correlation (cross-source co-occurrence matrix), bounds (observed source date bounds without stale scoring)."""
    if view == "correlation":
        return source_correlation(refresh_id=refresh_id)
    if view == "bounds":
        return source_observation_bounds()
    return {"error": f"unknown view {view!r}. choices: correlation, bounds"}


def rhythm(
    view: str = "fingerprint",
    project: str | None = None,
    refresh_id: str | None = None,
    metric_a: str | None = None,
    metric_b: str | None = None,
    max_lag_days: int = 3,
) -> Any:
    """Rhythm and daily correlation data. view: fingerprint (commit timing patterns per project), metrics (list correlatable daily metric names), correlation (lagged Pearson between two metrics; requires metric_a and metric_b)."""
    if view == "fingerprint":
        return daily_rhythm_fingerprint(project=project, refresh_id=refresh_id)
    if view == "metrics":
        return operator_day_metrics()
    if view == "correlation":
        if metric_a is None or metric_b is None:
            return {"error": "metric_a and metric_b are required for view=correlation"}
        return operator_day_correlation(metric_a=metric_a, metric_b=metric_b, max_lag_days=max_lag_days, refresh_id=refresh_id)
    return {"error": f"unknown view {view!r}. choices: fingerprint, metrics, correlation"}
