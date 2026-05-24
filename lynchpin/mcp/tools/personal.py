"""Personal-source MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
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
def bookmarks_search(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Search canonical browser bookmarks by title, URL, domain, or folder."""
    from dataclasses import asdict

    from lynchpin.sources.bookmarks import iter_bookmarks

    needle = query.lower()
    rows: list[dict[str, Any]] = []
    for row in iter_bookmarks():
        haystack = " ".join([row.title, row.url, row.domain, row.folder]).lower()
        if needle and needle not in haystack:
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
    """Daily canonical ARBTT focus activity."""
    from datetime import date

    from lynchpin.sources.arbtt import daily_arbtt_activity

    return [_json_safe(row.__dict__) for row in daily_arbtt_activity(start=date.fromisoformat(start), end=date.fromisoformat(end))]


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
