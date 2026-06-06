"""Personal-source MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    ensure_substrate_materialized_for_read,
    json_safe as _json_safe,
    require_best_materialized_refresh_id,
)


def _ensure_source_materialized_for_read(
    name: str,
    *,
    start: _date | None = None,
    end: _date | None = None,
) -> dict[str, Any]:
    """Ensure one canonical source product before an explicit read."""

    from lynchpin.materialization import ensure_materialized

    window = (start, end) if start is not None and end is not None else None
    return ensure_materialized(name, window=window).to_json()


def _exclusive_end(end: _date | None) -> _date | None:
    return end + _timedelta(days=1) if end is not None else None


@app.tool()
def spotify_daily(
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily Spotify listening stats from the spotify_daily table."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.personal import load_spotify_daily_rows

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_source_materialized_for_read(
            "spotify_daily",
            start=start_d,
            end=materialization_end,
        )
        ensure_substrate_materialized_for_read(
            caller="spotify_daily",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_materialized_refresh_id(
                conn,
                "spotify_daily",
                caller="spotify_daily",
                tool="spotify_daily",
            )

        rows = load_spotify_daily_rows(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
        )

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

    from lynchpin.core.primitives import logical_date
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

    _ensure_source_materialized_for_read(
        "webhistory",
        start=start_d,
        end=_exclusive_end(end_d),
    )

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

    for v in _iter_all_visits(start=start_d, end=end_d, ensure=False):
        d = logical_date(v.timestamp)
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
    _ensure_source_materialized_for_read(
        "google_takeout",
        start=start_d,
        end=_exclusive_end(end_d),
    )
    return [
        _json_safe(asdict(row))
        for row in iter_daily_activity(start=start_d, end=end_d, ensure=False)
    ]


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
    _ensure_source_materialized_for_read(
        "google_takeout",
        start=start_d,
        end=_exclusive_end(end_d),
    )
    needle = query.lower().strip()
    rows: list[dict[str, Any]] = []
    for row in iter_events(product=product, start=start_d, end=_exclusive_end(end_d), ensure=False):
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
def google_takeout_retrospective(
    start: str | None = None,
    end: str | None = None,
    session_gap_min: int = 45,
    top_n: int = 25,
) -> dict[str, Any]:
    """Mine Google Takeout events into sessions, anomalies, searches, and co-occurrences."""
    from datetime import date

    from lynchpin.analysis.google_takeout_mining import (
        google_takeout_retrospective as _retrospective,
    )

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    _ensure_source_materialized_for_read(
        "google_takeout",
        start=start_d,
        end=_exclusive_end(end_d),
    )
    return _retrospective(
        start=start_d,
        end=end_d,
        session_gap_min=session_gap_min,
        top_n=min(max(top_n, 1), 200),
    ).to_json()


@app.tool()
def personal_interest_trace(
    start: str | None = None,
    end: str | None = None,
    top_n: int = 50,
) -> dict[str, Any]:
    """Weak-label interest traces across searches, bookmarks, and web domains."""
    from datetime import date

    from lynchpin.analysis.personal_interest_fusion import (
        personal_interest_trace as _personal_interest_trace,
    )

    return _personal_interest_trace(
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        top_n=min(max(top_n, 1), 500),
    ).to_json()


@app.tool()
def terminal_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily canonical Atuin terminal activity."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.sources.terminal import daily_terminal_activity

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("atuin", start=start_d, end=_exclusive_end(end_d))
    rows = daily_terminal_activity(
        start=start_d,
        end=end_d,
        ensure=False,
    )
    return [_json_safe(asdict(row)) for row in rows]


@app.tool()
def terminal_sessions(start: str, end: str, limit: int = 100) -> list[dict[str, Any]]:
    """Gap-grouped canonical Atuin shell sessions."""
    from dataclasses import asdict
    from datetime import date

    from lynchpin.core.primitives import date_to_dt_range
    from lynchpin.sources.terminal import shell_sessions

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("atuin", start=start_d, end=_exclusive_end(end_d))
    start_dt, end_dt = date_to_dt_range(start_d, end_d)
    rows = shell_sessions(start=start_dt, end=end_dt, ensure=False)
    rows.sort(key=lambda row: row.start)
    capped = rows[: min(max(limit, 1), 1000)]
    return [_json_safe(asdict(row)) for row in capped]


@app.tool()
def keylog_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily keylog activity counts from scribe-tap metadata."""
    from datetime import date

    from lynchpin.sources.keylog import daily_activity

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("keylog", start=start_d, end=_exclusive_end(end_d))
    return [
        _json_safe(row.__dict__)
        for row in daily_activity(start=start_d, end=end_d, ensure=False)
    ]


@app.tool()
def keybind_usage(
    start: str,
    end: str,
    family: str | None = None,
    chord: str | None = None,
    bindings_path: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Hyprland keybind usage inferred from keylog metadata."""
    from datetime import date
    from pathlib import Path

    from lynchpin.analysis.keylog import DEFAULT_HYPRLAND_BINDINGS, analyze_keylog

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("keylog_analysis", start=start_d, end=_exclusive_end(end_d))
    if bindings_path is None:
        payload = _load_keylog_analysis_artifact(start=start_d, end=end_d, require_exact=False)
        if payload is not None:
            return _keybind_usage_from_payload(
                payload,
                start=start_d,
                end=end_d,
                family=family,
                chord=chord,
                limit=limit,
                source="artifact",
            )
    analysis = analyze_keylog(
        start=start_d,
        end=end_d,
        bindings_path=Path(bindings_path) if bindings_path else DEFAULT_HYPRLAND_BINDINGS,
    )
    capped_limit = min(max(limit, 1), 1000)
    usage = [
        row
        for row in analysis.keybind_usage
        if (family is None or row.family == family) and (chord is None or row.chord == chord)
    ][:capped_limit]
    summaries = [
        row
        for row in analysis.keybind_summaries
        if (family is None or row.family == family) and (chord is None or row.chord == chord)
    ][:capped_limit]
    family_summaries = [
        row
        for row in analysis.keybind_family_summaries
        if family is None or row.family == family
    ][:capped_limit]
    temporal_buckets = [
        row
        for row in analysis.keybind_temporal_buckets
        if (family is None or row.family == family) and (chord is None or row.chord == chord)
    ][:capped_limit]
    return {
        "start": analysis.start.isoformat(),
        "end": analysis.end.isoformat(),
        "source_event_count": analysis.source_event_count,
        "keypress_count": analysis.keypress_count,
        "matched_keybind_count": analysis.matched_keybind_count,
        "bind_count": len(analysis.keybinds),
        "filters": {"family": family, "chord": chord},
        "usage": [row.to_json() for row in usage],
        "keybind_summaries": [row.to_json() for row in summaries],
        "keybind_family_summaries": [row.to_json() for row in family_summaries],
        "keybind_temporal_buckets": [row.to_json() for row in temporal_buckets],
        "caveats": list(analysis.caveats),
        "source": "live_analysis",
    }


@app.tool()
def keylog_text_shape(
    start: str,
    end: str,
    bindings_path: str | None = None,
) -> dict[str, Any]:
    """Daily text-shape keylog metadata; text content has a separate tool."""
    from datetime import date
    from pathlib import Path

    from lynchpin.analysis.keylog import DEFAULT_HYPRLAND_BINDINGS, analyze_keylog

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("keylog_analysis", start=start_d, end=_exclusive_end(end_d))
    if bindings_path is None:
        payload = _load_keylog_analysis_artifact(start=start_d, end=end_d, require_exact=False)
        if payload is not None:
            return _keylog_text_shape_from_payload(payload, start=start_d, end=end_d)
    analysis = analyze_keylog(
        start=start_d,
        end=end_d,
        bindings_path=Path(bindings_path) if bindings_path else DEFAULT_HYPRLAND_BINDINGS,
    )
    return {
        "start": analysis.start.isoformat(),
        "end": analysis.end.isoformat(),
        "keypress_count": analysis.keypress_count,
        "changed_keypress_count": sum(row.changed_keypress_count for row in analysis.text_shape_days),
        "commandish_keypress_count": sum(row.commandish_keypress_count for row in analysis.text_shape_days),
        "days": [row.to_json() for row in analysis.text_shape_days],
        "caveats": list(analysis.caveats),
        "source": "live_analysis",
    }


def _load_keylog_analysis_artifact(
    *,
    start: Any,
    end: Any,
    require_exact: bool,
) -> dict[str, Any] | None:
    from datetime import date

    from lynchpin.core.io import load_json_if_exists, resolve_analysis_path

    payload = load_json_if_exists(resolve_analysis_path("keylog_analysis.json"))
    if not isinstance(payload, dict):
        return None
    try:
        artifact_start = date.fromisoformat(str(payload.get("start")))
        artifact_end = date.fromisoformat(str(payload.get("end")))
    except ValueError:
        return None
    if require_exact:
        return payload if artifact_start == start and artifact_end == end else None
    return payload if artifact_start <= start and end <= artifact_end else None


def _keybind_usage_from_payload(
    payload: dict[str, Any],
    *,
    start: Any,
    end: Any,
    family: str | None,
    chord: str | None,
    limit: int,
    source: str,
) -> dict[str, Any]:
    capped_limit = min(max(limit, 1), 1000)
    artifact_start = str(payload.get("start") or "")
    artifact_end = str(payload.get("end") or "")
    request_start = start.isoformat()
    request_end = end.isoformat()
    exact_window = artifact_start == request_start and artifact_end == request_end
    bind_by_chord = {
        str(row.get("chord")): row
        for row in _dict_rows(payload.get("keybinds"))
        if row.get("chord")
    }
    matching_usage = [
        row
        for row in _dict_rows(payload.get("keybind_usage"))
        if request_start <= str(row.get("date") or "") <= request_end
        and (family is None or row.get("family") == family)
        and (chord is None or row.get("chord") == chord)
    ]
    usage = matching_usage[:capped_limit]
    summaries = _summarize_keybind_usage_rows(matching_usage, bind_by_chord)[:capped_limit]
    family_summaries = _summarize_keybind_family_rows(matching_usage)[:capped_limit]
    temporal_buckets = [
        row
        for row in _dict_rows(payload.get("keybind_temporal_buckets"))
        if (family is None or row.get("family") == family) and (chord is None or row.get("chord") == chord)
    ][:capped_limit] if exact_window else []
    text_shape_days = [
        row
        for row in _dict_rows(payload.get("text_shape_days"))
        if request_start <= str(row.get("date") or "") <= request_end
    ]
    keypress_count = sum(int(row.get("keypress_count") or 0) for row in text_shape_days)
    caveats = list(payload.get("caveats") or ())
    if not exact_window:
        caveats.append("temporal buckets omitted because artifact covers a broader window")
    return {
        "start": request_start,
        "end": request_end,
        "source_event_count": keypress_count if text_shape_days else int(payload.get("source_event_count") or 0),
        "keypress_count": keypress_count if text_shape_days else int(payload.get("keypress_count") or 0),
        "matched_keybind_count": sum(int(row.get("count") or 0) for row in matching_usage),
        "bind_count": len(bind_by_chord),
        "filters": {"family": family, "chord": chord},
        "usage": usage,
        "keybind_summaries": summaries,
        "keybind_family_summaries": family_summaries,
        "keybind_temporal_buckets": temporal_buckets,
        "caveats": caveats,
        "source": source,
    }


def _summarize_keybind_usage_rows(
    rows: list[dict[str, Any]],
    bind_by_chord: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_chord: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row_chord = row.get("chord")
        if not row_chord:
            continue
        by_chord.setdefault(str(row_chord), []).append(row)
    summaries = []
    for row_chord, chord_rows in by_chord.items():
        dates = sorted({str(row.get("date")) for row in chord_rows if row.get("date")})
        if not dates:
            continue
        bind = bind_by_chord.get(row_chord, {})
        first = chord_rows[0]
        summaries.append(
            {
                "chord": row_chord,
                "dispatcher": first.get("dispatcher") or bind.get("dispatcher"),
                "argument": first.get("argument") or bind.get("argument"),
                "family": first.get("family") or bind.get("family"),
                "total_count": sum(int(row.get("count") or 0) for row in chord_rows),
                "active_days": len(dates),
                "first_date": dates[0],
                "last_date": dates[-1],
            }
        )
    return sorted(summaries, key=lambda row: (-int(row["total_count"]), str(row["chord"])))


def _summarize_keybind_family_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row_family = row.get("family")
        if not row_family:
            continue
        by_family.setdefault(str(row_family), []).append(row)
    summaries = []
    for row_family, family_rows in by_family.items():
        dates = sorted({str(row.get("date")) for row in family_rows if row.get("date")})
        if not dates:
            continue
        summaries.append(
            {
                "family": row_family,
                "total_count": sum(int(row.get("count") or 0) for row in family_rows),
                "unique_chords": len({str(row.get("chord")) for row in family_rows if row.get("chord")}),
                "active_days": len(dates),
                "first_date": dates[0],
                "last_date": dates[-1],
            }
        )
    return sorted(summaries, key=lambda row: (-int(row["total_count"]), str(row["family"])))


def _keylog_text_shape_from_payload(
    payload: dict[str, Any],
    *,
    start: Any,
    end: Any,
) -> dict[str, Any]:
    days = [
        row
        for row in _dict_rows(payload.get("text_shape_days"))
        if start.isoformat() <= str(row.get("date") or "") <= end.isoformat()
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "keypress_count": sum(int(row.get("keypress_count") or 0) for row in days),
        "changed_keypress_count": sum(int(row.get("changed_keypress_count") or 0) for row in days),
        "commandish_keypress_count": sum(int(row.get("commandish_keypress_count") or 0) for row in days),
        "days": days,
        "caveats": list(payload.get("caveats") or ()),
        "source": "artifact",
    }


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


@app.tool()
def keylog_text_content(
    start: str,
    end: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Text-content metrics from explicit keylog snapshot text records."""
    from datetime import date

    from lynchpin.analysis.keylog import analyze_keylog_text_content

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("keylog_analysis", start=start_d, end=_exclusive_end(end_d))
    payload = _load_keylog_analysis_artifact(start=start_d, end=end_d, require_exact=True)
    if payload is not None:
        content = payload.get("text_content")
        if isinstance(content, dict):
            return _keylog_text_content_from_payload(content, limit=limit)
    analysis = analyze_keylog_text_content(start=start_d, end=end_d, top_n=min(max(limit, 0), 1000))
    payload = analysis.to_json()
    payload["source"] = "live_analysis"
    return payload


def _keylog_text_content_from_payload(
    payload: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    top_terms = _dict_rows(payload.get("top_terms"))[: min(max(limit, 0), 1000)]
    return {
        "start": str(payload.get("start") or ""),
        "end": str(payload.get("end") or ""),
        "snapshot_count": int(payload.get("snapshot_count") or 0),
        "char_count": int(payload.get("char_count") or 0),
        "word_count": int(payload.get("word_count") or 0),
        "line_count": int(payload.get("line_count") or 0),
        "days": _dict_rows(payload.get("days")),
        "top_terms": top_terms,
        "caveats": list(payload.get("caveats") or ()),
        "source": "artifact",
    }


@app.tool()
def bookmarks_search(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Search canonical browser bookmarks by title, URL, domain, or folder.

    Query is split on whitespace; every term must appear (case-insensitive)
    somewhere in the bookmark's title + URL + domain + folder concatenation.
    Empty query returns the first ``limit`` rows.
    """
    from dataclasses import asdict

    from lynchpin.sources.bookmarks import iter_bookmarks

    _ensure_source_materialized_for_read("browser_bookmarks")
    terms = [t for t in query.lower().split() if t]
    rows: list[dict[str, Any]] = []
    for row in iter_bookmarks(ensure=False):
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

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read(
        "browser_bookmarks",
        start=start_d,
        end=_exclusive_end(end_d),
    )
    return [_json_safe(row.__dict__) for row in daily_bookmark_activity(start=start_d, end=end_d, ensure=False)]


@app.tool()
def communication_events(start: str | None = None, end: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Canonical communication events from Messenger and parseable Outlook exports."""
    from dataclasses import asdict
    from datetime import date

    start_date = date.fromisoformat(start) if start else None
    end_date = date.fromisoformat(end) if end else None
    _ensure_source_materialized_for_read(
        "communications",
        start=start_date,
        end=_exclusive_end(end_date),
    )
    from lynchpin.sources.communications import iter_communication_events

    rows: list[dict[str, Any]] = []
    for row in iter_communication_events(
        start=start_date,
        end=_exclusive_end(end_date),
        ensure=False,
    ):
        rows.append(_json_safe(asdict(row)))
        if len(rows) >= limit:
            break
    return rows


@app.tool()
def communication_daily(start: str, end: str) -> list[dict[str, Any]]:
    """Daily canonical communication-event activity."""
    from datetime import date

    from lynchpin.sources.communications import daily_communication_activity

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read(
        "communications",
        start=start_d,
        end=_exclusive_end(end_d),
    )
    return [_json_safe(row.__dict__) for row in daily_communication_activity(start=start_d, end=end_d, ensure=False)]


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

    from lynchpin.sources.activitywatch_derived import iter_derived_daily_activity
    from lynchpin.sources.arbtt import daily_arbtt_activity

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    materialization_end = _exclusive_end(end_d)
    _ensure_source_materialized_for_read("activitywatch_derived", start=start_d, end=materialization_end)
    _ensure_source_materialized_for_read("arbtt", start=start_d, end=materialization_end)
    rows: list[dict[str, Any]] = []
    for row in iter_derived_daily_activity(start=start_d, end=end_d, ensure=False):
        rows.append({**_json_safe(row.__dict__), "source": "activitywatch"})
    for row in daily_arbtt_activity(start=start_d, end=end_d, ensure=False):
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

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    _ensure_source_materialized_for_read("arbtt", start=start_d, end=_exclusive_end(end_d))
    return [
        _json_safe(row.__dict__)
        for row in daily_arbtt_activity(
            start=start_d,
            end=end_d,
            ensure=False,
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
    from lynchpin.substrate.personal import load_personal_daily_signals

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_source_materialized_for_read(
            "personal_daily_signals",
            start=start_d,
            end=materialization_end,
        )
        ensure_substrate_materialized_for_read(
            caller="personal_daily_signals",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = require_best_materialized_refresh_id(
                conn,
                "personal_daily_signal",
                caller="personal_daily_signals",
                tool="personal_daily_signals",
            )

        rows = load_personal_daily_signals(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            source=source,
            metric=metric,
            limit=limit,
        )

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
def title_metadata_status() -> dict[str, Any]:
    """Readiness and provenance for canonical title/window classification metadata."""
    import json

    from lynchpin.sources.title_metadata import title_metadata_manifest_path, title_metadata_path

    _ensure_source_materialized_for_read("title_metadata")
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

    _ensure_source_materialized_for_read("title_metadata")
    source_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    activity_counts: Counter[str] = Counter()
    confidence_bands: Counter[str] = Counter()
    total = 0
    missing_confidence = 0
    for row in iter_title_classifications(ensure=False):
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
    end_exclusive = end_d + date.resolution if end_d is not None else None
    _ensure_source_materialized_for_read("activity_content", start=start_d, end=end_exclusive)
    _ensure_source_materialized_for_read("title_metadata")
    rows: list[dict[str, Any]] = []
    for row in iter_activity_content_days(start=start_d, end=end_exclusive, ensure=False):
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
    end_exclusive = end_d + date.resolution if end_d is not None else None
    _ensure_source_materialized_for_read("activity_content", start=start_d, end=end_exclusive)
    _ensure_source_materialized_for_read("title_metadata")
    rows: list[dict[str, Any]] = []
    for row in iter_activity_title_usage(start=start_d, end=end_exclusive, ensure=False):
        if matched is not None and row.matched != matched:
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
    end_exclusive = end_d + date.resolution if end_d is not None else None
    _ensure_source_materialized_for_read("activity_content", start=start_d, end=end_exclusive)
    _ensure_source_materialized_for_read("title_metadata")
    focused = 0.0
    matched = 0.0
    gpt_matched = 0.0
    days = 0
    for row in iter_activity_content_days(start=start_d, end=end_exclusive, ensure=False):
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
    from lynchpin.core.io import materialize_analysis_artifacts
    from lynchpin.sources.analysis_artifacts import artifact_inventory

    materialize_analysis_artifacts()
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
    from lynchpin.analysis.operator_rhythm import compute_operator_rhythm, render_rhythm_summary
    from lynchpin.sources.activitywatch import circadian
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import (
        load_ai_session_timestamps_in_range,
        load_ai_work_event_timestamps_in_range,
        load_commit_timestamps_in_range,
        load_pressure_timestamps_in_range,
    )

    start_date = _date.fromisoformat(start)
    end_date = _date.fromisoformat(end)
    materialization_end = _exclusive_end(end_date)

    _ensure_source_materialized_for_read(
        "activitywatch",
        start=start_date,
        end=materialization_end,
    )
    if refresh_id is None:
        ensure_substrate_materialized_for_read(
            caller="operator_rhythm",
            window=(start_date, materialization_end),
        )

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
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="operator_rhythm")
        if refresh_id is not None:
            try:
                commit_ts = load_commit_timestamps_in_range(
                    conn, refresh_id=refresh_id, start=start_date, end=end_date, project=project
                )
            except Exception:
                pass

            # Prefer the typed ai_work_event table; fall back to the
            # evidence graph's ai_session nodes when the typed table is
            # empty (substrate-promote step may be partial — the graph
            # build still has the rows). Without this fallback, rhythm
            # silently reported zero AI activity on otherwise-busy
            # graph builds.
            try:
                ai_ts = load_ai_work_event_timestamps_in_range(
                    conn, refresh_id=refresh_id, start=start_date, end=end_date, project=project
                )
            except Exception:
                pass

            if not ai_ts and refresh_id:
                try:
                    ai_ts = load_ai_session_timestamps_in_range(
                        conn, refresh_id=refresh_id, start=start_date, end=end_date, project=project
                    )
                except Exception:
                    pass

            try:
                pressure_ts = load_pressure_timestamps_in_range(
                    conn, refresh_id=refresh_id, start=start_date, end=end_date
                )
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
        "summary": render_rhythm_summary(rhythm),
    }


@app.tool()
def operator_retrospective_readiness(
    start: str,
    end: str,
    require_polylogue: bool = False,
) -> dict[str, Any]:
    """Gate whether a retrospective window supports behavioral explanation.

    Returns ``behavioral_explanation_allowed=false`` when core continuous
    sources such as ActivityWatch, terminal, machine telemetry, or xtask
    history do not fully cover the window. Structural/git-only analysis can
    still proceed. Polylogue chat semantics are a caveat by default and only
    block when ``require_polylogue`` is true.
    """
    from lynchpin.analysis.operator_retrospective_readiness import (
        operator_retrospective_readiness as _readiness,
    )

    report = _readiness(
        start=_date.fromisoformat(start),
        end=_date.fromisoformat(end),
        require_polylogue=require_polylogue,
    )
    return report.to_dict()


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
    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)

    # Validate dimension
    valid_dims = {"topic_category", "attention_level", "activity", "platform", "mode"}
    if dimension not in valid_dims:
        raise ValueError(f"dimension must be one of {valid_dims}, got {dimension!r}")

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_signals import load_activity_title_usage_by_dimension

    with connect(substrate_path(), read_only=True) as conn:
        # Interval-intersect: title's observed lifetime [first_date, last_date]
        # must overlap the requested window [start_d, end_d]. The previous
        # ``first_date >= start AND last_date <= end`` filter required the
        # entire title lifetime to be contained in the window — excluded
        # long-running titles even when they were active during the window.
        rows = load_activity_title_usage_by_dimension(
            conn, start=start_d, end=end_d, dimension=dimension
        )

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
    (available / partial / out_of_range / missing / untracked), ``last_date`` ISO,
    ``reason``. Sources not represented in ``coverage_report`` (wykop,
    gmail at the moment) are reported as ``untracked`` because Lynchpin
    cannot yet distinguish real silence from stale data for those sources.
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
