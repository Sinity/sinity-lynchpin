"""Retrospective presentation layer server for scaffold and narrative browsing."""

from __future__ import annotations

import calendar
import gzip
import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import markdown
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD_ROOT = REPO_ROOT / "retrospective" / "scaffold"
NARRATIVES_ROOT = REPO_ROOT / "retrospective" / "narratives"
ASSETS_ROOT = Path(__file__).with_name("scaffold_browser_assets")
DEFER_JSON_BYTES = 8_000_000

MONTH_NAMES = list(calendar.month_name)  # index 1..12
HALF_FOR_MONTH = {m: "H1" if m <= 6 else "H2" for m in range(1, 13)}
QUARTER_FOR_MONTH = {m: f"Q{(m - 1) // 3 + 1}" for m in range(1, 13)}


@dataclass(frozen=True)
class PeriodLocation:
    scale: str
    key: str
    title: str
    scaffold_dir: Path | None
    narrative_path: Path | None
    period: dict[str, str] | None = None


def _day_dir(d: date) -> Path:
    return (
        SCAFFOLD_ROOT
        / str(d.year)
        / HALF_FOR_MONTH[d.month]
        / QUARTER_FOR_MONTH[d.month]
        / MONTH_NAMES[d.month]
        / d.isoformat()
    )


def _month_dir(key: str) -> Path | None:
    match = re.match(r"(\d{4})-(\d{2})$", key)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    candidate = SCAFFOLD_ROOT / str(year) / HALF_FOR_MONTH[month] / QUARTER_FOR_MONTH[month] / MONTH_NAMES[month]
    return candidate if candidate.is_dir() else None


def _quarter_dir(key: str) -> Path | None:
    match = re.match(r"(\d{4})-Q([1-4])$", key)
    if not match:
        return None
    year, quarter = int(match.group(1)), int(match.group(2))
    half = "H1" if quarter <= 2 else "H2"
    candidate = SCAFFOLD_ROOT / str(year) / half / f"Q{quarter}"
    return candidate if candidate.is_dir() else None


def _year_dir(key: str) -> Path | None:
    candidate = SCAFFOLD_ROOT / key
    return candidate if candidate.is_dir() else None


def _half_dir(key: str) -> Path | None:
    match = re.match(r"(\d{4})-H([12])$", key)
    if not match:
        return None
    year, half = match.group(1), f"H{match.group(2)}"
    candidate = SCAFFOLD_ROOT / year / half
    return candidate if candidate.is_dir() else None


def _week_dir(key: str) -> Path | None:
    match = re.match(r"(\d{4})-W(\d{2})$", key)
    if not match:
        return None
    year, week = int(match.group(1)), int(match.group(2))
    monday = date.fromisocalendar(year, week, 1)
    for anchor in (monday, monday + timedelta(days=6), monday - timedelta(days=7), monday + timedelta(days=7)):
        candidate = (
            SCAFFOLD_ROOT
            / str(anchor.year)
            / HALF_FOR_MONTH[anchor.month]
            / QUARTER_FOR_MONTH[anchor.month]
            / MONTH_NAMES[anchor.month]
            / key
        )
        if candidate.is_dir():
            return candidate
    return None


def _read_json(path: Path) -> object | None:
    if path.exists():
        return json.loads(path.read_bytes())
    gz_path = path.with_suffix(".json.gz")
    if gz_path.exists():
        with gzip.open(gz_path, "rb") as fh:
            return json.loads(fh.read())
    return None


def _deferred_json_stub(path: Path) -> dict[str, object]:
    return {
        "_deferred": True,
        "file": path.stem,
        "bytes": path.stat().st_size,
        "reason": "large_json",
    }


def _read_all_json(directory: Path | None, *, defer_large: bool = True) -> dict[str, object]:
    result: dict[str, object] = {}
    if directory is None or not directory.is_dir():
        return result
    seen_stems: set[str] = set()
    for path in sorted(directory.iterdir()):
        if path.suffix == ".json":
            seen_stems.add(path.stem)
            if defer_large and path.stat().st_size > DEFER_JSON_BYTES:
                result[path.stem] = _deferred_json_stub(path)
                continue
            try:
                result[path.stem] = json.loads(path.read_bytes())
            except Exception:
                continue
        elif path.suffix == ".gz" and path.name.endswith(".json.gz"):
            stem = path.name[: -len(".json.gz")]
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            try:
                if defer_large and path.stat().st_size > DEFER_JSON_BYTES:
                    result[stem] = _deferred_json_stub(path)
                    continue
                with gzip.open(path, "rb") as fh:
                    result[stem] = json.loads(fh.read())
            except Exception:
                continue
    return result


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker == -1:
        return {}, text
    raw_meta = text[4:marker]
    body = text[marker + 5 :]
    try:
        meta = yaml.safe_load(raw_meta) or {}
    except Exception:
        meta = {}
    return meta if isinstance(meta, dict) else {}, body


def _read_markdown_document(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {"exists": False, "path": str(path) if path else None, "meta": {}, "markdown": "", "html": ""}
    text = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(text)
    html = markdown.markdown(
        body,
        extensions=[
            "extra",
            "tables",
            "fenced_code",
            "sane_lists",
        ],
    )
    return {
        "exists": True,
        "path": str(path),
        "meta": meta,
        "markdown": body,
        "html": html,
    }


def _ordinal_day(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _week_folder_name(key: str) -> str:
    return f"W{key.split('-W', 1)[1]}"


def _month_parts(key: str) -> tuple[int, int] | None:
    match = re.match(r"(\d{4})-(\d{2})$", key)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _range_dict(value: object) -> dict[str, str] | None:
    if isinstance(value, dict):
        start = value.get("start")
        end = value.get("end")
        if start or end:
            return {
                "start": str(start) if start else "",
                "end": str(end) if end else "",
            }
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split("/", 1)]
        if len(parts) == 2 and all(parts):
            return {"start": parts[0], "end": parts[1]}
    return None


def _scaffold_period_range(scale: str, key: str, scaffold: dict[str, object]) -> dict[str, str] | None:
    brief = scaffold.get("narrative_brief")
    if isinstance(brief, dict):
        period = _range_dict(brief.get("period"))
        if period:
            return period
    manifest = scaffold.get("manifest")
    if isinstance(manifest, dict):
        period = _range_dict(manifest.get("data_range"))
        if period:
            return period
    if scale == "day":
        return {"start": key, "end": key}
    return None


def _narrative_candidates(scale: str, key: str) -> list[Path]:
    if scale == "overview":
        return [NARRATIVES_ROOT / "overview.md"]
    if scale == "year":
        return [NARRATIVES_ROOT / key / f"{key}.md"]
    if scale == "half":
        match = re.match(r"(\d{4})-H([12])$", key)
        if not match:
            return []
        year, half = int(match.group(1)), f"H{match.group(2)}"
        return [NARRATIVES_ROOT / str(year) / half / "half.md"]
    if scale == "quarter":
        match = re.match(r"(\d{4})-Q([1-4])$", key)
        if not match:
            return []
        year, quarter = int(match.group(1)), int(match.group(2))
        half = "H1" if quarter <= 2 else "H2"
        return [NARRATIVES_ROOT / str(year) / half / f"Q{quarter}" / f"{key}.md"]
    if scale == "month":
        parts = _month_parts(key)
        if parts is None:
            return []
        year, month = parts
        return [
            NARRATIVES_ROOT
            / str(year)
            / HALF_FOR_MONTH[month]
            / QUARTER_FOR_MONTH[month]
            / MONTH_NAMES[month]
            / "month.md"
        ]
    if scale == "week":
        match = re.match(r"(\d{4})-W(\d{2})$", key)
        if not match:
            return []
        year, week = int(match.group(1)), int(match.group(2))
        monday = date.fromisocalendar(year, week, 1)
        candidates = []
        for anchor in (monday, monday + timedelta(days=6), monday - timedelta(days=7), monday + timedelta(days=7)):
            candidates.append(
                NARRATIVES_ROOT
                / str(anchor.year)
                / HALF_FOR_MONTH[anchor.month]
                / QUARTER_FOR_MONTH[anchor.month]
                / MONTH_NAMES[anchor.month]
                / _week_folder_name(key)
                / "week.md"
            )
        return candidates
    if scale == "day":
        try:
            day_value = date.fromisoformat(key)
        except ValueError:
            return []
        week_folder = f"W{day_value.isocalendar().week:02d}"
        ordinal = _ordinal_day(day_value.day) + ".md"
        monday = day_value - timedelta(days=day_value.weekday())
        sunday = monday + timedelta(days=6)
        anchors = [day_value, monday, sunday]
        return [
            NARRATIVES_ROOT
            / str(anchor.year)
            / HALF_FOR_MONTH[anchor.month]
            / QUARTER_FOR_MONTH[anchor.month]
            / MONTH_NAMES[anchor.month]
            / week_folder
            / ordinal
            for anchor in anchors
        ]
    return []


def _narrative_path(scale: str, key: str) -> Path | None:
    candidates = _narrative_candidates(scale, key)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _top_name(entries: list[dict[str, object]] | None, *, field: str = "name") -> str | None:
    if not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    value = first.get(field)
    return str(value) if value else None


def _fmt_hours(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}h"


def _fmt_minutes(value: float | None) -> str:
    return "-" if value is None else f"{round(value):,} min"


def _fmt_number(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}"


def _fmt_money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def _fmt_percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _sleep_hours_from_records(records: list[dict[str, object]] | None) -> float | None:
    if not records:
        return None
    total_min = 0.0
    for record in records:
        if not isinstance(record, dict):
            continue
        total_min += float(record.get("sleep_duration_min") or record.get("bed_duration_min") or 0.0)
    return total_min / 60 if total_min else None


def _build_metric_cards(scale: str, key: str, scaffold: dict[str, object], brief: dict[str, object]) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    if scale == "day":
        metrics = scaffold.get("metrics") or {}
        ai = scaffold.get("ai_activity") or {}
        cards.extend(
            [
                {"label": "Active", "value": _fmt_hours(metrics.get("active_hours")), "detail": "focused time"},
                {"label": "Deep Work", "value": _fmt_minutes(metrics.get("deep_work_min")), "detail": "sustained blocks"},
                {"label": "Commits", "value": _fmt_number(metrics.get("commits")), "detail": "git events"},
                {"label": "Sleep", "value": _fmt_hours(_sleep_hours_from_records(scaffold.get("sleep"))), "detail": "inferred / recorded"},
                {
                    "label": "AI Sessions",
                    "value": _fmt_number(len((ai.get("session_summaries") or []))),
                    "detail": _top_name(brief.get("dominant_threads", {}).get("ai_providers")) or "providers",
                },
                {
                    "label": "Shell",
                    "value": _fmt_number(len(scaffold.get("shell") or [])),
                    "detail": "sessions",
                },
            ]
        )
        return cards

    metrics_name = {
        "week": "week_metrics",
        "month": "month_metrics",
        "quarter": "quarter_metrics",
        "half": "half_metrics",
        "year": "year_metrics",
    }.get(scale)
    if metrics_name:
        metrics = scaffold.get(metrics_name) or {}
        cards.extend(
            [
                {"label": "Active", "value": _fmt_hours(metrics.get("total_active_hours")), "detail": scale},
                {"label": "Commits", "value": _fmt_number(metrics.get("total_commits")), "detail": "shipping volume"},
                {
                    "label": "Sleep Avg",
                    "value": _fmt_hours((metrics.get("sleep") or {}).get("avg_sleep_hours")),
                    "detail": f"{_fmt_number((metrics.get('sleep') or {}).get('n_sleep_records'))} records",
                },
                {
                    "label": "AI Sessions",
                    "value": _fmt_number((metrics.get("ai") or {}).get("session_count")),
                    "detail": _top_name(brief.get("dominant_threads", {}).get("ai_providers")) or "providers",
                },
                {
                    "label": "Top Project",
                    "value": _top_name(brief.get("dominant_threads", {}).get("projects")) or "-",
                    "detail": "dominant thread",
                },
                {
                    "label": "Quality Notes",
                    "value": _fmt_number(len((brief.get("evidence_profile") or {}).get("data_quality_notes") or [])),
                    "detail": "caveats",
                },
            ]
        )
        return cards

    if scale == "overview":
        dominant = brief.get("dominant_threads") or {}
        trend_hooks = (brief.get("analytic_hooks") or {}).get("trend_hooks") or []
        sources_available = ((scaffold.get("manifest") or {}).get("sources_available") or {}) if isinstance(scaffold.get("manifest"), dict) else {}
        source_count = sum(1 for available in sources_available.values() if available) if isinstance(sources_available, dict) else len(dominant.get("source_coverage") or [])
        cards.extend(
            [
                {"label": "Range", "value": f"{brief.get('period', {}).get('start', '?')} → {brief.get('period', {}).get('end', '?')}", "detail": "dataset span"},
                {"label": "Top Project", "value": _top_name(dominant.get("projects")) or "-", "detail": "by commits"},
                {"label": "Top Provider", "value": _top_name(dominant.get("ai_providers")) or "-", "detail": "AI sessions"},
                {
                    "label": "Coverage Sources",
                    "value": _fmt_number(source_count),
                    "detail": "available in scaffold",
                },
                {
                    "label": "Trend Hooks",
                    "value": _fmt_number(len(trend_hooks)),
                    "detail": "long-run signals",
                },
            ]
        )
        return cards

    return cards


def _build_narrative_status(scale: str, key: str, scaffold: dict[str, object], narrative: dict[str, object]) -> dict[str, object]:
    scaffold_manifest = scaffold.get("manifest") if isinstance(scaffold.get("manifest"), dict) else {}
    narrative_meta = narrative.get("meta") if isinstance(narrative.get("meta"), dict) else {}
    scaffold_generated = _coerce_datetime(scaffold_manifest.get("generated_at"))
    narrative_generated = _coerce_datetime(narrative_meta.get("generated"))
    scaffold_range = _scaffold_period_range(scale, key, scaffold)
    narrative_range = _range_dict(narrative_meta.get("range"))
    reasons: list[str] = []
    if not narrative.get("exists"):
        return {
            "state": "missing",
            "reasons": ["narrative_missing"],
            "scaffold_generated_at": scaffold_manifest.get("generated_at"),
            "narrative_generated_at": narrative_meta.get("generated"),
            "scaffold_range": scaffold_range,
            "narrative_range": narrative_range,
        }
    if narrative_generated and scaffold_generated and narrative_generated < scaffold_generated:
        reasons.append("generated_before_scaffold")
    if scale == "day":
        narrative_key = narrative_meta.get("key")
        if narrative_key and str(narrative_key) != key:
            reasons.append("key_mismatch")
    elif narrative_range and scaffold_range and narrative_range != scaffold_range:
        reasons.append("range_mismatch")
    return {
        "state": "stale" if reasons else "fresh",
        "reasons": reasons,
        "scaffold_generated_at": scaffold_manifest.get("generated_at"),
        "narrative_generated_at": narrative_meta.get("generated"),
        "scaffold_range": scaffold_range,
        "narrative_range": narrative_range,
    }


def _build_summary(scale: str, key: str, scaffold: dict[str, object], narrative: dict[str, object], title: str) -> dict[str, object]:
    brief = scaffold.get("narrative_brief") if isinstance(scaffold.get("narrative_brief"), dict) else {}
    evidence_profile = brief.get("evidence_profile") if isinstance(brief.get("evidence_profile"), dict) else {}
    data_quality_notes = evidence_profile.get("data_quality_notes") or []
    if not data_quality_notes:
        analytic_hooks = brief.get("analytic_hooks") if isinstance(brief.get("analytic_hooks"), dict) else {}
        data_quality_notes = analytic_hooks.get("sleep_caveats") or []
    narrative_status = _build_narrative_status(scale, key, scaffold, narrative)
    return {
        "title": title,
        "metric_cards": _build_metric_cards(scale, key, scaffold, brief),
        "dominant_threads": brief.get("dominant_threads") or {},
        "angles": brief.get("angles") or [],
        "carry_forward": brief.get("carry_forward") or [],
        "story_signals": brief.get("story_signals") or [],
        "analytic_hooks": brief.get("analytic_hooks") or {},
        "shape": brief.get("shape") or {},
        "evidence_profile": evidence_profile,
        "data_quality_notes": data_quality_notes,
        "narrative_available": bool(narrative.get("exists")),
        "narrative_status": narrative_status,
    }


def _title_for_scale(scale: str, key: str) -> str:
    if scale == "overview":
        return "Retrospective Overview"
    if scale == "day":
        day_value = date.fromisoformat(key)
        return day_value.strftime("%A, %B %d, %Y")
    if scale == "month":
        year, month = _month_parts(key) or (0, 1)
        return f"{MONTH_NAMES[month]} {year}"
    if scale == "half":
        match = re.match(r"(\d{4})-H([12])$", key)
        if match:
            return f"{match.group(1)} H{match.group(2)}"
    return key


def _resolve_period(scale: str, key: str) -> PeriodLocation:
    title = _title_for_scale(scale, key)
    if scale == "overview":
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=SCAFFOLD_ROOT / "overview", narrative_path=_narrative_path(scale, key))
    if scale == "day":
        day_value = date.fromisoformat(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=_day_dir(day_value), narrative_path=_narrative_path(scale, key))
    if scale == "week":
        scaffold_dir = _week_dir(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=scaffold_dir, narrative_path=_narrative_path(scale, key))
    if scale == "month":
        scaffold_dir = _month_dir(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=scaffold_dir, narrative_path=_narrative_path(scale, key))
    if scale == "half":
        scaffold_dir = _half_dir(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=scaffold_dir, narrative_path=_narrative_path(scale, key))
    if scale == "quarter":
        scaffold_dir = _quarter_dir(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=scaffold_dir, narrative_path=_narrative_path(scale, key))
    if scale == "year":
        scaffold_dir = _year_dir(key)
        return PeriodLocation(scale=scale, key=key, title=title, scaffold_dir=scaffold_dir, narrative_path=_narrative_path(scale, key))
    raise ValueError(f"unsupported scale: {scale}")


def _assemble_period(scale: str, key: str) -> dict[str, object]:
    location = _resolve_period(scale, key)
    scaffold = _read_all_json(location.scaffold_dir)
    narrative = _read_markdown_document(location.narrative_path)
    return {
        "kind": scale,
        "key": key,
        "title": location.title,
        "scaffold_path": str(location.scaffold_dir) if location.scaffold_dir else None,
        "narrative_path": str(location.narrative_path) if location.narrative_path else None,
        "files": sorted(scaffold.keys()),
        "narrative": narrative,
        "summary": _build_summary(scale, key, scaffold, narrative, location.title),
        "data": scaffold,
    }


def _read_period_file(scale: str, key: str, file_name: str) -> object:
    location = _resolve_period(scale, key)
    if location.scaffold_dir is None or not location.scaffold_dir.is_dir():
        raise FileNotFoundError(f"scaffold directory not found for {scale}:{key}")
    safe_name = file_name.removesuffix(".json")
    if not re.match(r"^[A-Za-z0-9_.-]+$", safe_name):
        raise ValueError(f"invalid scaffold file name: {file_name}")
    path = location.scaffold_dir / f"{safe_name}.json"
    payload = _read_json(path)
    if payload is None:
        raise FileNotFoundError(f"scaffold file not found: {safe_name}.json")
    return payload


def _api_years() -> list[str]:
    years = []
    for path in sorted(SCAFFOLD_ROOT.iterdir()):
        if path.is_dir() and re.match(r"\d{4}$", path.name):
            years.append(path.name)
    return years


def _build_year_tree(year: str) -> dict[str, object]:
    year_dir = SCAFFOLD_ROOT / year
    if not year_dir.is_dir():
        return {"year": year, "halves": []}
    halves = []
    for half in ("H1", "H2"):
        half_key = f"{year}-{half}"
        half_dir = year_dir / half
        if not half_dir.is_dir():
            continue
        half_node = {
            "key": half_key,
            "label": half,
            "has_narrative": bool((_narrative_path("half", half_key) or Path()).exists()),
            "quarters": [],
        }
        quarter_indexes = (1, 2) if half == "H1" else (3, 4)
        for quarter_index in quarter_indexes:
            quarter_key = f"{year}-Q{quarter_index}"
            quarter_dir = half_dir / f"Q{quarter_index}"
            if not quarter_dir.is_dir():
                continue
            quarter_node = {
                "key": quarter_key,
                "label": quarter_key,
                "has_narrative": bool((_narrative_path("quarter", quarter_key) or Path()).exists()),
                "months": [],
            }
            for month_index in range(1, 13):
                if QUARTER_FOR_MONTH[month_index] != f"Q{quarter_index}":
                    continue
                month_name = MONTH_NAMES[month_index]
                month_key = f"{year}-{month_index:02d}"
                month_dir = quarter_dir / month_name
                if not month_dir.is_dir():
                    continue
                month_node = {
                    "key": month_key,
                    "label": month_name,
                    "has_narrative": bool((_narrative_path("month", month_key) or Path()).exists()),
                    "weeks": [],
                    "days": [],
                }
                for child in sorted(month_dir.iterdir()):
                    if not child.is_dir():
                        continue
                    if re.match(r"\d{4}-W\d{2}$", child.name):
                        week_key = child.name
                        month_node["weeks"].append(
                            {
                                "key": week_key,
                                "label": _week_folder_name(week_key),
                                "has_narrative": bool((_narrative_path("week", week_key) or Path()).exists()),
                            }
                        )
                    elif re.match(r"\d{4}-\d{2}-\d{2}$", child.name):
                        day_key = child.name
                        day_value = date.fromisoformat(day_key)
                        month_node["days"].append(
                            {
                                "key": day_key,
                                "label": f"{day_value.day:02d}",
                                "weekday": day_value.strftime("%a"),
                                "has_narrative": bool((_narrative_path("day", day_key) or Path()).exists()),
                            }
                        )
                quarter_node["months"].append(month_node)
            half_node["quarters"].append(quarter_node)
        halves.append(half_node)
    return {
        "year": year,
        "has_narrative": bool((_narrative_path("year", year) or Path()).exists()),
        "halves": halves,
    }


def _serve_asset(path: Path) -> tuple[bytes, str]:
    content_type, _ = mimetypes.guess_type(path.name)
    if content_type is None:
        if path.suffix == ".js":
            content_type = "application/javascript"
        elif path.suffix == ".css":
            content_type = "text/css"
        else:
            content_type = "application/octet-stream"
    return path.read_bytes(), f"{content_type}; charset=utf-8"


def serve_scaffold_browser(*, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Start the retrospective presentation server."""
    if not SCAFFOLD_ROOT.is_dir():
        raise RuntimeError(f"Scaffold root not found: {SCAFFOLD_ROOT}")
    if not ASSETS_ROOT.is_dir():
        raise RuntimeError(f"Browser assets not found: {ASSETS_ROOT}")

    class Handler(BaseHTTPRequestHandler):
        def _write(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, *, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
            self._write(status, body, "application/json; charset=utf-8")

        def _json_or_error(self, loader, *, status: int = HTTPStatus.OK, error_status: int = HTTPStatus.BAD_REQUEST, context: dict[str, object] | None = None) -> None:
            try:
                self._json(loader(), status=status)
            except Exception as exc:  # pragma: no cover - defensive HTTP surface
                payload = {"error": str(exc)}
                if context:
                    payload.update(context)
                self._json(payload, status=error_status)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            if parsed.path == "/":
                body, content_type = _serve_asset(ASSETS_ROOT / "index.html")
                self._write(HTTPStatus.OK, body, content_type)
                return

            if parsed.path.startswith("/assets/"):
                asset = ASSETS_ROOT / parsed.path.removeprefix("/assets/")
                if asset.exists() and asset.is_file():
                    body, content_type = _serve_asset(asset)
                    self._write(HTTPStatus.OK, body, content_type)
                    return
                self._write(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
                return

            if parsed.path == "/api/years":
                self._json(_api_years())
                return

            if parsed.path == "/api/tree":
                year = query.get("year", [""])[0]
                if not year:
                    self._json({"error": "year parameter required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json_or_error(lambda: _build_year_tree(year), context={"year": year})
                return

            if parsed.path == "/api/period":
                scale = query.get("kind", [""])[0]
                key = query.get("key", [""])[0] or ("overview" if scale == "overview" else "")
                if not scale or not key:
                    self._json({"error": "kind and key parameters required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json_or_error(lambda: _assemble_period(scale, key), context={"kind": scale, "key": key})
                return

            if parsed.path == "/api/file":
                scale = query.get("kind", [""])[0]
                key = query.get("key", [""])[0] or ("overview" if scale == "overview" else "")
                file_name = query.get("file", [""])[0]
                if not scale or not key or not file_name:
                    self._json({"error": "kind, key, and file parameters required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json_or_error(
                    lambda: _read_period_file(scale, key, file_name),
                    context={"kind": scale, "key": key, "file": file_name},
                )
                return

            if parsed.path == "/api/day":
                key = query.get("date", [""])[0]
                self._json_or_error(lambda: _assemble_period("day", key), context={"kind": "day", "key": key})
                return

            if parsed.path == "/api/week":
                key = query.get("key", [""])[0]
                self._json_or_error(lambda: _assemble_period("week", key), context={"kind": "week", "key": key})
                return

            if parsed.path == "/api/month":
                key = query.get("key", [""])[0]
                self._json_or_error(lambda: _assemble_period("month", key), context={"kind": "month", "key": key})
                return

            if parsed.path == "/api/quarter":
                key = query.get("key", [""])[0]
                self._json_or_error(lambda: _assemble_period("quarter", key), context={"kind": "quarter", "key": key})
                return

            if parsed.path == "/api/half":
                key = query.get("key", [""])[0]
                self._json_or_error(lambda: _assemble_period("half", key), context={"kind": "half", "key": key})
                return

            if parsed.path == "/api/year":
                key = query.get("key", [""])[0]
                self._json_or_error(lambda: _assemble_period("year", key), context={"kind": "year", "key": key})
                return

            if parsed.path == "/api/overview":
                self._json_or_error(
                    lambda: _assemble_period("overview", "overview"),
                    context={"kind": "overview", "key": "overview"},
                )
                return

            if parsed.path == "/healthz":
                self._write(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
                return

            self._write(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"retrospective browser listening on http://{host}:{port}/")
    print(f"scaffold root: {SCAFFOLD_ROOT}")
    print(f"narratives root: {NARRATIVES_ROOT}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Retrospective presentation layer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    serve_scaffold_browser(host=args.host, port=args.port)
