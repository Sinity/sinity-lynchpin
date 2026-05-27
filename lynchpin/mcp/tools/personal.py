"""Personal-source MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from datetime import date as _date
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_refresh_id,
    json_safe as _json_safe,
    require_best_refresh_id,
)


@app.tool()
def spotify_daily(
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily Spotify listening stats from the spotify_daily table."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_refresh_id(conn, "spotify_daily", tool="spotify_daily")

        sql = (
            "SELECT date, track_count, minutes_played, unique_artists, "
            "unique_tracks, top_artists, top_tracks FROM spotify_daily "
            "WHERE refresh_id = ?"
        )
        params: list[Any] = [refresh_id]
        if start:
            sql += " AND date >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND date <= ?"
            params.append(_date.fromisoformat(end))
        sql += " ORDER BY date"

        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "date": _json_safe(r[0]),
            "track_count": r[1],
            "minutes_played": r[2],
            "unique_artists": r[3],
            "unique_tracks": r[4],
            "top_artists": r[5],
            "top_tracks": r[6],
        }
        for r in rows
    ]


@app.tool()
def web_daily(start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Daily canonical webhistory rollup with weak host/path buckets.

    Reads the canonical webhistory NDJSON (the same product
    ``daily_browsing`` reads), but additionally classifies each visit into
    weak host/path buckets so an agent can answer questions like "how much
    search/research vs social/youtube on day X" without treating those buckets
    as semantic classification. When ``start`` / ``end`` are omitted, defaults to the last 7
    days ending today (local).

    Per-day fields:
        date, total_visits, unique_domains, top_5_domains (list of
        [domain, share]), weak_search_query_count, weak_github_visits,
        weak_docs_visits, weak_social_visits, weak_video_visits.
    """
    from collections import Counter
    from datetime import date as _date_type, timedelta
    from urllib.parse import urlparse

    from lynchpin.sources.web import _iter_all_visits
    from lynchpin.sources.web_urls import _normalize_domain

    if end is None:
        end_d = _date_type.today()
    else:
        end_d = _date_type.fromisoformat(end)
    if start is None:
        start_d = end_d - timedelta(days=6)
    else:
        start_d = _date_type.fromisoformat(start)

    # buckets per day
    per_day_total: Counter[_date_type] = Counter()
    per_day_domains: dict[_date_type, Counter[str]] = {}
    per_day_search: Counter[_date_type] = Counter()
    per_day_github: Counter[_date_type] = Counter()
    per_day_docs: Counter[_date_type] = Counter()
    per_day_social: Counter[_date_type] = Counter()
    per_day_video: Counter[_date_type] = Counter()

    _DOCS_DOMAINS = {
        "docs.python.org", "docs.rs", "doc.rust-lang.org", "developer.mozilla.org",
        "nixos.org", "wiki.nixos.org", "nix.dev", "kernel.org", "man7.org",
        "stackoverflow.com", "stackexchange.com",
    }
    _SOCIAL_DOMAINS = {
        "twitter.com", "x.com", "reddit.com", "old.reddit.com", "news.ycombinator.com",
        "lobste.rs", "mastodon.social", "facebook.com", "instagram.com",
        "tiktok.com", "linkedin.com", "bsky.app",
    }
    _VIDEO_DOMAINS = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}
    _SEARCH_HOSTS = {
        "www.google.com", "google.com", "duckduckgo.com", "www.duckduckgo.com",
        "www.bing.com", "bing.com", "search.brave.com", "kagi.com",
    }

    for v in _iter_all_visits(start=start_d, end=end_d):
        d = v.timestamp.date()
        per_day_total[d] += 1
        url = v.url or ""
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        domain = _normalize_domain(host)
        if domain:
            per_day_domains.setdefault(d, Counter())[domain] += 1

        path = (parsed.path or "").lower()
        if host in _SEARCH_HOSTS and (path == "/search" or path.startswith("/search")):
            per_day_search[d] += 1
        if domain == "github.com":
            per_day_github[d] += 1
        if domain in _DOCS_DOMAINS:
            per_day_docs[d] += 1
        if domain in _SOCIAL_DOMAINS:
            per_day_social[d] += 1
        if domain in _VIDEO_DOMAINS:
            per_day_video[d] += 1

    rows: list[dict[str, Any]] = []
    for d in sorted(per_day_total):
        total = per_day_total[d]
        domains = per_day_domains.get(d, Counter())
        top5 = domains.most_common(5)
        rows.append(
            {
                "date": d.isoformat(),
                "total_visits": total,
                "unique_domains": len(domains),
                "top_5_domains": [
                    {"domain": dom, "visits": cnt, "share": round(cnt / total, 4) if total else 0.0}
                    for dom, cnt in top5
                ],
                "classification_basis": "weak_host_path",
                "weak_search_query_count": per_day_search[d],
                "weak_github_visits": per_day_github[d],
                "weak_docs_visits": per_day_docs[d],
                "weak_social_visits": per_day_social[d],
                "weak_video_visits": per_day_video[d],
            }
        )
    return rows


@app.tool()
def google_takeout_daily(start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Daily timestamped Google Takeout product activity from canonical products."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.sources.google_takeout_products import iter_daily_activity

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    return [_json_safe(asdict(row)) for row in iter_daily_activity(start=start_d, end=end_d)]


@app.tool()
def google_takeout_events(
    start: str | None = None,
    end: str | None = None,
    product: str | None = None,
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search timestamped Google Takeout product events."""
    from datetime import date

    from lynchpin.sources.google_takeout_products import iter_events

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    needle = query.lower().strip()
    rows: list[dict[str, Any]] = []
    for row in iter_events(product=product):
        day = row.timestamp.date()
        if start_d and day < start_d:
            continue
        if end_d and day >= end_d:
            continue
        haystack = " ".join(
            part
            for part in (
                row.product,
                row.service or "",
                row.title,
                row.source_member,
            )
            if part
        ).lower()
        if needle and needle not in haystack:
            continue
        rows.append(
            _json_safe(
                {
                    "product": row.product,
                    "timestamp": row.timestamp.isoformat(),
                    "title": row.title,
                    "service": row.service,
                    "source_member": row.source_member,
                }
            )
        )
        if len(rows) >= min(max(limit, 1), 10_000):
            break
    return rows


@app.tool()
def terminal_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily canonical Atuin terminal activity."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.sources.terminal import daily_terminal_activity

    rows = daily_terminal_activity(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )
    return [_json_safe(asdict(row)) for row in rows]


@app.tool()
def terminal_sessions(start: str, end: str, limit: int = 100) -> list[dict[str, Any]]:
    """Gap-grouped canonical Atuin shell sessions."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.core.primitives import date_to_dt_range
    from lynchpin.sources.terminal import shell_sessions

    start_dt, end_dt = date_to_dt_range(date.fromisoformat(start), date.fromisoformat(end))
    rows = shell_sessions(start=start_dt, end=end_dt)
    rows.sort(key=lambda row: row.start)
    capped = rows[: min(max(limit, 1), 1000)]
    return [_json_safe(asdict(row)) for row in capped]


@app.tool()
def bookmarks_search(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Search canonical browser bookmarks by title, URL, domain, or folder.

    Query is split on whitespace; every term must appear (case-insensitive)
    somewhere in the bookmark's title + URL + domain + folder concatenation.
    Empty query returns the first ``limit`` rows.
    """
    from dataclasses import asdict

    from lynchpin.sources.bookmarks import iter_bookmarks

    terms = [t for t in query.lower().split() if t]
    rows: list[dict[str, Any]] = []
    for row in iter_bookmarks():
        haystack = " ".join([row.title, row.url, row.domain, row.folder]).lower()
        if terms and not all(t in haystack for t in terms):
            continue
        rows.append(_json_safe(asdict(row)))
        if len(rows) >= limit:
            break
    return rows


@app.tool()
def bookmark_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily canonical browser bookmark activity."""
    from datetime import date

    from lynchpin.sources.bookmarks import daily_bookmark_activity

    return [_json_safe(row.__dict__) for row in daily_bookmark_activity(start=date.fromisoformat(start), end=date.fromisoformat(end))]


@app.tool()
def communication_events(start: str | None = None, end: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Canonical communication events from Messenger and parseable Outlook exports."""
    from dataclasses import asdict
    from datetime import date

    start_date = date.fromisoformat(start) if start else None
    end_date = date.fromisoformat(end) if end else None
    from lynchpin.sources.communications import iter_communication_events

    rows: list[dict[str, Any]] = []
    for row in iter_communication_events():
        if row.timestamp is not None:
            day = row.timestamp.date()
            if start_date and day < start_date:
                continue
            if end_date and day >= end_date:
                continue
        rows.append(_json_safe(asdict(row)))
        if len(rows) >= limit:
            break
    return rows


@app.tool()
def communication_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily canonical communication-event activity."""
    from datetime import date

    from lynchpin.sources.communications import daily_communication_activity

    return [_json_safe(row.__dict__) for row in daily_communication_activity(start=date.fromisoformat(start), end=date.fromisoformat(end))]


@app.tool()
def focus_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily focus activity from whichever capture covered each date.

    Dispatches per-date to ActivityWatch (2024-10 → present) or ARBTT
    (2022-07 → 2022-09); the two coverages don't overlap so each requested
    date gets at most one row. Every row carries ``source`` (``"activitywatch"``
    or ``"arbtt"``) so callers can tell them apart. AW rows carry their full
    schema (``deep_work_min``, ``fragmentation_score``, ``hourly_active`` …);
    ARBTT rows carry a smaller schema and the AW-only fields are absent.
    Dates outside both coverage intervals (e.g., 2022-10 → 2024-09) simply
    produce no row, distinguishable from in-coverage zero-activity days only
    by calling ``coverage_report``.
    """
    from datetime import date

    from lynchpin.sources.activitywatch import daily_activity
    from lynchpin.sources.arbtt import daily_arbtt_activity

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    rows: list[dict[str, Any]] = []
    for row in daily_activity(start=start_d, end=end_d):
        rows.append({**_json_safe(row.__dict__), "source": "activitywatch"})
    for row in daily_arbtt_activity(start=start_d, end=end_d):
        rows.append({**_json_safe(row.__dict__), "source": "arbtt"})
    rows.sort(key=lambda r: r["date"])
    return rows


@app.tool()
def arbtt_focus_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily focus activity from the historical ARBTT capture.

    ARBTT covers 2022-07-12 → 2022-09-26 only. For current focus use
    ``focus_daily`` (ActivityWatch).
    """
    from datetime import date

    from lynchpin.sources.arbtt import daily_arbtt_activity

    return [
        _json_safe(row.__dict__)
        for row in daily_arbtt_activity(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
        )
    ]


@app.tool()
def personal_daily_signals(
    start: str | None = None,
    end: str | None = None,
    source: str | None = None,
    metric: str | None = None,
    refresh_id: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Normalized daily signals promoted from canonical personal products."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_refresh_id(
                conn,
                "personal_daily_signal",
                tool="personal_daily_signals",
            )

        sql = (
            "SELECT source, date, metric, value, dimensions "
            "FROM personal_daily_signal WHERE refresh_id = ?"
        )
        params: list[Any] = [refresh_id]
        if start:
            sql += " AND date >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND date < ?"
            params.append(_date.fromisoformat(end))
        if source:
            sql += " AND source = ?"
            params.append(source)
        if metric:
            sql += " AND metric = ?"
            params.append(metric)
        sql += " ORDER BY date, source, metric LIMIT ?"
        params.append(min(max(limit, 1), 10_000))
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "source": row[0],
            "date": _json_safe(row[1]),
            "metric": row[2],
            "value": row[3],
            "dimensions": row[4],
        }
        for row in rows
    ]


@app.tool()
def materialization_status() -> list[dict[str, Any]]:
    """Strict canonical materialization status for Lynchpin datasets."""
    from lynchpin.materialization import audit_materialization

    return [row.to_json() for row in audit_materialization()]


@app.tool()
def contract_status() -> list[dict[str, Any]]:
    """Dataset contract readiness, coverage, and repair commands."""
    return materialization_status()


@app.tool()
def derived_product_status() -> list[dict[str, Any]]:
    """Readiness rows for canonical derived products consumed by substrate promotion."""
    from lynchpin.materialization import audit_materialization

    names = {"title_metadata", "activity_content", "spotify_daily", "personal_daily_signals"}
    return [row.to_json() for row in audit_materialization() if row.name in names]


@app.tool()
def title_metadata_status() -> dict[str, Any]:
    """Readiness and provenance for canonical title/window classification metadata."""
    import json

    from lynchpin.sources.title_metadata import title_metadata_manifest_path, title_metadata_path

    path = title_metadata_path()
    manifest = title_metadata_manifest_path()
    if not path.exists() or not manifest.exists():
        return {"status": "missing", "path": str(path), "manifest": str(manifest)}
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"status": "malformed", "path": str(path), "manifest": str(manifest)}
    return {"status": "ready", **_json_safe(payload)}


@app.tool()
def title_metadata_audit(limit: int = 20) -> dict[str, Any]:
    """Auditable shape of canonical title/window classification metadata."""
    from collections import Counter

    from lynchpin.sources.title_metadata import iter_title_classifications

    source_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    activity_counts: Counter[str] = Counter()
    confidence_bands: Counter[str] = Counter()
    total = 0
    missing_confidence = 0
    for row in iter_title_classifications():
        total += 1
        source_counts[row.classification_source or "(missing)"] += 1
        model_counts[row.model_version or "(missing)"] += 1
        activity_counts[row.activity or "(missing)"] += 1
        if row.confidence is None:
            missing_confidence += 1
            confidence_bands["missing"] += 1
        elif row.confidence >= 0.8:
            confidence_bands["high"] += 1
        elif row.confidence >= 0.5:
            confidence_bands["medium"] += 1
        else:
            confidence_bands["low"] += 1
    cap = min(max(limit, 1), 100)
    return {
        "row_count": total,
        "missing_confidence_count": missing_confidence,
        "classification_sources": dict(source_counts.most_common(cap)),
        "model_versions": dict(model_counts.most_common(cap)),
        "activities": dict(activity_counts.most_common(cap)),
        "confidence_bands": dict(confidence_bands),
    }


@app.tool()
def activity_content_daily(start: str | None = None, end: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    """Daily ActivityWatch content rollup joined to title metadata."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.sources.activity_content import iter_activity_content_days

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    rows: list[dict[str, Any]] = []
    for row in iter_activity_content_days():
        if start_d and row.date < start_d:
            continue
        if end_d and row.date >= end_d:
            continue
        rows.append(_json_safe(asdict(row)))
        if len(rows) >= min(max(limit, 1), 10_000):
            break
    return rows


@app.tool()
def activity_title_usage(
    start: str | None = None,
    end: str | None = None,
    matched: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Title-level ActivityWatch usage joined to title classifications."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.sources.activity_content import iter_activity_title_usage

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    rows: list[dict[str, Any]] = []
    for row in iter_activity_title_usage():
        if matched is not None and row.matched != matched:
            continue
        if start_d and (row.last_date is None or row.last_date < start_d):
            continue
        if end_d and (row.first_date is None or row.first_date >= end_d):
            continue
        rows.append(_json_safe(asdict(row)))
        if len(rows) >= min(max(limit, 1), 10_000):
            break
    return rows


@app.tool()
def activity_unmatched_titles(start: str | None = None, end: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Top unmatched ActivityWatch titles by focused seconds."""
    rows = activity_title_usage(start=start, end=end, matched=False, limit=10_000)
    rows.sort(key=lambda row: float(row.get("focused_seconds") or 0.0), reverse=True)
    return rows[: min(max(limit, 1), 1000)]


@app.tool()
def activity_content_coverage(start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Coverage ratios for ActivityWatch title metadata matches over a date range."""
    from datetime import date

    from lynchpin.sources.activity_content import iter_activity_content_days

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    focused = 0.0
    matched = 0.0
    gpt_matched = 0.0
    days = 0
    for row in iter_activity_content_days():
        if start_d and row.date < start_d:
            continue
        if end_d and row.date >= end_d:
            continue
        days += 1
        focused += row.focused_seconds
        matched += row.matched_seconds
        gpt_matched += row.gpt_matched_seconds
    return {
        "days": days,
        "focused_seconds": round(focused, 3),
        "matched_seconds": round(matched, 3),
        "gpt_matched_seconds": round(gpt_matched, 3),
        "matched_ratio": round(matched / focused, 6) if focused else 0.0,
        "gpt_matched_ratio": round(gpt_matched / focused, 6) if focused else 0.0,
    }


@app.tool()
def webhistory_provenance() -> dict[str, Any]:
    """Canonical webhistory manifest with source counts and duplicate diagnostics."""
    import json

    from lynchpin.ingest.webhistory import full_history_manifest_path

    path = full_history_manifest_path()
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"status": "malformed", "path": str(path)}
    return {"status": "ready", "path": str(path), **payload}


@app.tool()
def activitywatch_archive_audit() -> dict[str, Any]:
    """Schema audit for processed historical ActivityWatch backup databases."""
    from lynchpin.cli.process_activitywatch_archives import audit_activitywatch_archive_dbs

    return audit_activitywatch_archive_dbs()


@app.tool()
def analysis_artifact_status() -> list[dict[str, Any]]:
    """Inventory generated analysis artifacts with availability and shape status."""
    from lynchpin.sources.analysis_artifacts import artifact_inventory

    return [
        {
            "name": artifact.name,
            "path": str(artifact.path),
            "kind": artifact.kind,
            "projects": list(artifact.projects),
            "size_bytes": artifact.size_bytes,
            "modified_at": artifact.modified_at.isoformat(),
            "generated_at": artifact.generated_at.isoformat() if artifact.generated_at else None,
            "status": artifact.status,
            "reason": artifact.reason,
        }
        for artifact in artifact_inventory()
    ]


@app.tool()
def operator_rhythm(
    start: str,
    end: str,
    project: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Cross-source (hour-of-day, day-of-week) rhythm matrix.

    Composes ActivityWatch focus minutes, commit_fact timestamps,
    ai_work_event timestamps, and machine_episode pressure timestamps
    into one matrix. Lets the operator see deep-work hours vs noise
    hours without four separate queries.

    Parameters:
        start, end: ISO dates (inclusive).
        project:    optional canonical project filter applied to commit_fact
                    and ai_work_event; ActivityWatch focus stays whole-system.
        refresh_id: substrate snapshot. Defaults to latest commit_fact build.

    Returns:
        {
            "start": "YYYY-MM-DD",
            "end": "YYYY-MM-DD",
            "project": str | None,
            "buckets": [
                {"dow": 0-6 (Mon=0), "hour": 0-23, "focus_min", "commit_count",
                 "ai_session_count", "pressure_episode_count"},
                ...
            ],
            "partial_sources": [str, ...],
            "peak_focus_hour": [dow, hour] | None,
            "peak_commit_hour": [dow, hour] | None,
            "peak_combined_hour": [dow, hour] | None,
            "summary": str,
        }
    """
    from lynchpin.analysis.operator_rhythm import compute_operator_rhythm
    from lynchpin.sources.activitywatch import circadian
    from lynchpin.substrate.connection import connect, substrate_path

    start_date = _date.fromisoformat(start)
    end_date = _date.fromisoformat(end)

    focus_rows: list[tuple[_date, int, float]] = []
    try:
        for row in circadian(start_date, end_date):
            focus_rows.append((row.date, row.hour, row.active_min))
    except Exception:
        pass

    commit_ts: list = []
    ai_ts: list = []
    pressure_ts: list = []

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "commit_fact")
        if refresh_id is not None:
            proj_filter = " AND project = ?" if project else ""
            params: list[Any] = [refresh_id, start_date, end_date]
            if project:
                params.append(project)

            try:
                commit_ts = [
                    r[0]
                    for r in conn.execute(
                        f"SELECT authored_at FROM commit_fact "
                        f"WHERE refresh_id = ? AND authored_at::DATE BETWEEN ? AND ?"
                        f"{proj_filter}",
                        params,
                    ).fetchall()
                ]
            except Exception:
                pass

            # Prefer the typed ai_work_event table; fall back to the
            # evidence graph's ai_session nodes when the typed table is
            # empty (substrate-promote step may be partial — the graph
            # build still has the rows). Without this fallback, rhythm
            # silently reported zero AI activity on otherwise-busy
            # graph builds.
            try:
                ai_params: list[Any] = [refresh_id, start_date, end_date]
                if project:
                    ai_params.append(project)
                ai_ts = [
                    r[0]
                    for r in conn.execute(
                        f"SELECT start_ts FROM ai_work_event "
                        f"WHERE refresh_id = ? AND start_ts::DATE BETWEEN ? AND ? "
                        f"AND start_ts IS NOT NULL{proj_filter}",
                        ai_params,
                    ).fetchall()
                ]
            except Exception:
                pass

            if not ai_ts and refresh_id:
                try:
                    node_params: list[Any] = [refresh_id, start_date, end_date]
                    node_filter = ""
                    if project:
                        node_filter = " AND project = ?"
                        node_params.append(project)
                    ai_ts = [
                        r[0]
                        for r in conn.execute(
                            "SELECT start_ts FROM evidence_node "
                            "WHERE refresh_id = ? AND kind = 'ai_session' "
                            "AND start_ts IS NOT NULL "
                            "AND start_ts::DATE BETWEEN ? AND ?"
                            + node_filter,
                            node_params,
                        ).fetchall()
                    ]
                except Exception:
                    pass

            try:
                pressure_ts = [
                    r[0]
                    for r in conn.execute(
                        "SELECT start_ts FROM evidence_node "
                        "WHERE refresh_id = ? AND kind = 'machine_episode' "
                        "AND start_ts IS NOT NULL AND start_ts::DATE BETWEEN ? AND ?",
                        [refresh_id, start_date, end_date],
                    ).fetchall()
                ]
            except Exception:
                pass

    rhythm = compute_operator_rhythm(
        start=start_date,
        end=end_date,
        project=project,
        focus_rows=focus_rows,
        commit_timestamps=commit_ts,
        ai_session_timestamps=ai_ts,
        pressure_timestamps=pressure_ts,
    )

    return {
        "start": _json_safe(rhythm.start),
        "end": _json_safe(rhythm.end),
        "project": rhythm.project,
        "buckets": [
            {
                "dow": b.dow,
                "hour": b.hour,
                "focus_min": b.focus_min,
                "commit_count": b.commit_count,
                "ai_session_count": b.ai_session_count,
                "pressure_episode_count": b.pressure_episode_count,
            }
            for b in rhythm.buckets
        ],
        "partial_sources": list(rhythm.partial_sources),
        "peak_focus_hour": list(rhythm.peak_focus_hour) if rhythm.peak_focus_hour else None,
        "peak_commit_hour": list(rhythm.peak_commit_hour) if rhythm.peak_commit_hour else None,
        "peak_combined_hour": list(rhythm.peak_combined_hour) if rhythm.peak_combined_hour else None,
        "summary": rhythm.summary,
    }


@app.tool()
def activity_semantic_daily(
    start: str,
    end: str,
    dimension: str = "topic_category",
) -> list[dict[str, Any]]:
    """Distribution of activity-title classifications along a semantic dimension
    for titles active within the requested window.

    Uses title_classification GPT-Pro enrichment joined against actual
    ActivityWatch usage. Each row is a (first_observed_date, dimension_value)
    bucket aggregating the LIFETIME focused_seconds of titles whose
    observation interval [first_date, last_date] intersects the requested
    window. ``focused_seconds`` is therefore an upper bound on time spent
    on that bucket during the window — finer-grained per-day attribution
    isn't available because activity_title_usage rolls each (title_hash, app)
    into a single row across its entire history.

    Dimensions supported:
    - topic_category: what topics were active (work, social, health, etc.)
    - attention_level: scanning, shallow, deep, background, engaged
    - activity: activity type label
    - platform: platform classification
    - mode: mode classification (e.g., active, passive, etc.)

    Returns rows: {date, dimension_value, focused_minutes, focused_seconds}
    ordered by date and focused_seconds (DESC per date).
    """
    from lynchpin.substrate.connection import connect, substrate_path

    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)

    # Validate dimension
    valid_dims = {"topic_category", "attention_level", "activity", "platform", "mode"}
    if dimension not in valid_dims:
        raise ValueError(f"dimension must be one of {valid_dims}, got {dimension!r}")

    with connect(substrate_path(), read_only=True) as conn:
        # Interval-intersect: title's observed lifetime [first_date, last_date]
        # must overlap the requested window [start_d, end_d]. The previous
        # ``first_date >= start AND last_date <= end`` filter required the
        # entire title lifetime to be contained in the window — excluded
        # long-running titles even when they were active during the window.
        sql = f"""
            SELECT
                CAST(first_date AS DATE) as date,
                COALESCE({dimension}, 'unknown') as dim_value,
                SUM(focused_seconds) as focused_seconds
            FROM activity_title_usage
            WHERE first_date <= ? AND last_date >= ?
            GROUP BY date, dim_value
            ORDER BY date, focused_seconds DESC
        """
        params: list[Any] = [end_d, start_d]
        rows = conn.execute(sql, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        focused_mins = round((row[2] or 0) / 60, 2)
        result.append({
            "date": _json_safe(row[0]),
            "dimension_value": row[1],
            "focused_seconds": row[2],
            "focused_minutes": focused_mins,
        })

    return result


@app.tool()
def operator_public_text_daily(
    start: str,
    end: str,
    sources: str = "irc,reddit,wykop,messenger,gmail",
    monthly: bool = False,
) -> list[dict[str, Any]]:
    """Operator-authored public-text per day across human-facing channels.

    Aggregates the volume of text the operator produced to other humans:
    IRC operator messages, reddit comment own_text (>-blockquotes
    excluded), reddit posts, wykop entries/comments, messenger outbound.
    Polylogue (AI chat) is intentionally not included — AI conversations
    are not 'public-text-to-humans' and conflating them would mask the
    cross-platform 2025 decline visible in this surface.

    Parameters:
    - ``start``/``end``: ISO date range (inclusive).
    - ``sources``: comma-separated subset of
      ``irc,reddit,wykop,messenger``. Default = all.
    - ``monthly``: if True, return monthly rollups instead of daily rows.

    Daily rows: ``date``, ``total_chars``, ``message_count``,
    ``channel_count``, ``by_channel`` (map of channel→{chars,messages}).
    Days with zero activity across selected sources are omitted.

    Monthly rows: ``month``, ``total_chars``, ``message_count``,
    ``active_days``.
    """
    from datetime import date

    from lynchpin.analysis.operator_public_text import (
        monthly_rollup,
        operator_public_text_daily as _build,
    )

    src_set = {s.strip() for s in sources.split(",") if s.strip()}
    rows = _build(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        sources=src_set,
    )
    if monthly:
        return [
            {
                "month": m,
                "total_chars": chars,
                "message_count": msgs,
                "active_days": days,
            }
            for m, chars, msgs, days in monthly_rollup(rows)
        ]
    return [
        {
            "date": r.date.isoformat(),
            "total_chars": r.total_chars,
            "message_count": r.message_count,
            "channel_count": r.channel_count,
            "by_channel": r.by_channel,
        }
        for r in rows
    ]


@app.tool()
def operator_public_text_coverage(
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Per-source coverage for ``operator_public_text_daily`` query windows.

    Distinguishes "operator wrote nothing through this source" (real
    silence) from "this source's data doesn't cover the window" (missing
    data). Call this alongside ``operator_public_text_daily`` to interpret
    zero-contribution channels correctly.

    Returns per-source rows: ``source``, ``status``
    (available / partial / out_of_range / missing), ``last_date`` ISO,
    ``reason``. Sources not represented in ``coverage_report`` (wykop,
    gmail at the moment) are reported as ``available`` with reason noted —
    they may still have stale data but lynchpin doesn't currently track it.
    """
    from datetime import date

    from lynchpin.analysis.operator_public_text import coverage_summary

    rows = coverage_summary(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )
    return [
        {
            "source": r.source,
            "status": r.status,
            "last_date": r.last_date.isoformat() if r.last_date else None,
            "reason": r.reason,
        }
        for r in rows
    ]
