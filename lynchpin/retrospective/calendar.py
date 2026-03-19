from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

from ..core.dates import iter_dates
from ..core.io import write_text_if_changed
from ..system import sinex, sinnix
from ..trajectory.coverage import SignalCoverage, compute_coverage
from ..trajectory.day import TrajectoryDay
from ..trajectory.month import TrajectoryMonth
from ..trajectory.quarter import TrajectoryQuarter
from ..trajectory.week import TrajectoryWeek, summarize_weeks
from ..trajectory.window import (
    load_date_window,
    summarize_window_months,
    summarize_window_quarters,
    summarize_window_years,
)
from ..trajectory.year import TrajectoryYear


class CalendarScale(str, Enum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


@dataclass(frozen=True)
class CalendarArtifact:
    scale: CalendarScale
    key: str
    markdown: str
    payload: dict[str, object]
    output_path: Path | None
    wrote: bool


def format_hours(seconds: float) -> str:
    return f"{seconds / 3600:.2f}"


def format_top(items: tuple[tuple[str, float], ...], *, as_hours: bool = False) -> str:
    if not items:
        return "n/a"
    if as_hours:
        return ", ".join(f"{name} ({seconds / 3600:.1f}h)" for name, seconds in items)
    return ", ".join(f"{name} ({seconds / 60:.1f}m)" for name, seconds in items)


def format_coverage(cov: Optional[SignalCoverage]) -> str:
    if cov is None:
        return "n/a"
    planes = []
    if cov.has_activitywatch:
        planes.append("AW")
    if cov.has_terminal:
        planes.append("terminal")
    if cov.has_polylogue:
        planes.append("chat")
    if cov.has_git:
        planes.append("git")
    if cov.has_atuin:
        planes.append("atuin")
    if cov.has_web:
        planes.append("web")
    return f"{cov.quality} ({cov.plane_count} planes: {', '.join(planes) or 'none'})"


def build_calendar_views(
    start: date,
    end: date,
    *,
    scale: CalendarScale | str = CalendarScale.day,
    output_dir: Path | None = Path("artefacts/calendar/views"),
    write_files: bool = True,
) -> list[CalendarArtifact]:
    if end < start:
        raise ValueError("end must be on or after start")

    resolved_scale = CalendarScale(scale)
    target_dir = output_dir if write_files else None

    if resolved_scale is CalendarScale.day:
        return _build_day_views(start, end, output_dir=target_dir)
    if resolved_scale is CalendarScale.week:
        return _build_week_views(start, end, output_dir=target_dir)
    if resolved_scale is CalendarScale.month:
        return _build_month_views(start, end, output_dir=target_dir)
    if resolved_scale is CalendarScale.quarter:
        return _build_quarter_views(start, end, output_dir=target_dir)
    if resolved_scale is CalendarScale.year:
        return _build_year_views(start, end, output_dir=target_dir)

    raise ValueError(f"Unsupported calendar scale: {scale!r}")


def _build_day_views(start: date, end: date, *, output_dir: Path | None) -> list[CalendarArtifact]:
    window = load_date_window(start, end, annotate_anomalies=True)
    day_map = window.day_map()
    sinex_state = next(sinex.iter_repo_state(), None)
    sinnix_host = next(sinnix.iter_hosts(), None)
    artifacts: list[CalendarArtifact] = []
    for dt in iter_dates(start, end):
        day = day_map.get(dt)
        if day is None:
            continue
        artifacts.append(
            _render_day(
                dt,
                day,
                output_dir=output_dir,
                sinex_state=sinex_state,
                sinnix_host=sinnix_host,
            )
        )
    return artifacts


def _build_week_views(start: date, end: date, *, output_dir: Path | None) -> list[CalendarArtifact]:
    window = load_date_window(start, end)
    return [_render_week(week, output_dir=output_dir) for week in summarize_weeks(list(window.days))]


def _build_month_views(start: date, end: date, *, output_dir: Path | None) -> list[CalendarArtifact]:
    window = load_date_window(start, end)
    return [_render_month(month, output_dir=output_dir) for month in summarize_window_months(window)]


def _build_quarter_views(start: date, end: date, *, output_dir: Path | None) -> list[CalendarArtifact]:
    window = load_date_window(start, end)
    return [_render_quarter(quarter, output_dir=output_dir) for quarter in summarize_window_quarters(window)]


def _build_year_views(start: date, end: date, *, output_dir: Path | None) -> list[CalendarArtifact]:
    window = load_date_window(start, end)
    return [_render_year(year, output_dir=output_dir) for year in summarize_window_years(window)]


def _render_day(
    dt: date,
    day: TrajectoryDay,
    *,
    output_dir: Path | None,
    sinex_state,
    sinnix_host,
) -> CalendarArtifact:
    cov = day.signal_coverage or compute_coverage(day)

    lines = [
        f"# {dt.isoformat()}",
        "",
        "## Trajectory",
        f"- Signal spans: {day.signal_count}",
        f"- Activity chains: {day.chain_count}",
        f"- Active hours: {format_hours(day.active_seconds)}",
        f"- Recovery hours: {format_hours(day.recovery_seconds)}",
        f"- Dominant mode: {day.dominant_mode or 'n/a'}",
        f"- Dominant project: {day.dominant_project or 'n/a'}",
        f"- Dominant topic: {day.dominant_topic or 'n/a'}",
        f"- Top modes: {format_top(day.top_modes)}",
        f"- Top projects: {format_top(day.top_projects)}",
        f"- Top topics: {format_top(day.top_topics)}",
        f"- Highlights: {', '.join(day.highlights) or 'n/a'}",
        "",
        "## Coverage",
        f"- Quality: {format_coverage(cov)}",
        f"- Sources: {', '.join(sorted(day.source_counts.keys())) or 'n/a'}",
        "",
        "## Commands & Chats",
        f"- Shell commands: {day.command_count}",
        f"- Transcripts: {day.transcript_count}",
        f"- Git commits: {day.commit_count}",
        "",
    ]

    if day.projects:
        lines += ["## Projects"]
        for proj in day.projects:
            lines.append(f"- {proj.project}: {proj.duration_seconds / 3600:.1f}h, {proj.chain_count} chains")
        lines.append("")

    if day.anomalies:
        lines += ["## Anomalies"]
        for anomaly_kind in day.anomalies:
            lines.append(f"- {anomaly_kind}")
        lines.append("")

    if sinex_state:
        lines += [
            "## Sinex",
            f"- Branch {sinex_state.branch or 'n/a'} @ {sinex_state.head or 'unknown'}",
            f"- Latest commit: {sinex_state.latest_commit or 'n/a'}",
            "",
        ]
    if sinnix_host:
        lines += [
            "## Sinnix",
            f"- Host {sinnix_host.name} toggles: {', '.join(t.key for t in sinnix_host.toggles if t.enabled) or 'none recorded'}",
            "",
        ]

    rendered = "\n".join(lines)
    output_path = output_dir / f"day-{dt.isoformat()}.md" if output_dir else None
    wrote = write_text_if_changed(output_path, rendered) if output_path else False
    return CalendarArtifact(
        scale=CalendarScale.day,
        key=dt.isoformat(),
        markdown=rendered,
        payload=day.to_dict(),
        output_path=output_path,
        wrote=wrote,
    )


def _render_week(week: TrajectoryWeek, *, output_dir: Path | None) -> CalendarArtifact:
    delta_str = ""
    if week.active_delta_vs_prior is not None:
        sign = "+" if week.active_delta_vs_prior >= 0 else ""
        delta_str = f" ({sign}{week.active_delta_vs_prior / 3600:.1f}h vs prior week)"

    lines = [
        f"# {week.iso_week} ({week.start_date.isoformat()} → {week.end_date.isoformat()})",
        "",
        "## Activity",
        f"- Active hours: {format_hours(week.active_seconds)}{delta_str}",
        f"- Recovery hours: {format_hours(week.recovery_seconds)}",
        f"- Activity chains: {week.chain_count}",
        f"- Shell commands: {week.command_count}",
        f"- Git commits: {week.commit_count}",
        f"- Transcripts: {week.transcript_count}",
        "",
        "## Focus",
        f"- Dominant mode: {week.dominant_mode or 'n/a'}",
        f"- Dominant project: {week.dominant_project or 'n/a'}",
        f"- Dominant topic: {week.dominant_topic or 'n/a'}",
        f"- Top modes: {format_top(week.top_modes, as_hours=True)}",
        f"- Top projects: {format_top(week.top_projects, as_hours=True)}",
        f"- Top topics: {format_top(week.top_topics, as_hours=True)}",
        "",
        "## Pattern",
        f"- Day pattern: {week.day_pattern}",
        f"- Busiest day: {week.busiest_day.isoformat() if week.busiest_day else 'n/a'}",
        f"- Quietest day: {week.quietest_day.isoformat() if week.quietest_day else 'n/a'}",
        "",
    ]
    rendered = "\n".join(lines)
    output_path = output_dir / f"week-{week.iso_week}.md" if output_dir else None
    wrote = write_text_if_changed(output_path, rendered) if output_path else False
    return CalendarArtifact(
        scale=CalendarScale.week,
        key=week.iso_week,
        markdown=rendered,
        payload=week.to_dict(),
        output_path=output_path,
        wrote=wrote,
    )


def _render_month(month: TrajectoryMonth, *, output_dir: Path | None) -> CalendarArtifact:
    lines = [
        f"# {month.month} ({month.start_date.isoformat()} → {month.end_date.isoformat()})",
        "",
        "## Summary",
        f"- Active days: {month.active_days} / {month.total_days}",
        f"- Active hours: {format_hours(month.active_seconds)}",
        f"- Recovery hours: {format_hours(month.recovery_seconds)}",
        f"- Activity chains: {month.chain_count}",
        f"- Shell commands: {month.command_count}",
        f"- Git commits: {month.commit_count}",
        f"- Chat sessions: {month.chat_session_count}",
        f"- Transcripts: {month.transcript_count}",
        "",
        "## Focus",
        f"- Dominant mode: {month.dominant_mode or 'n/a'}",
        f"- Dominant project: {month.dominant_project or 'n/a'}",
        f"- Dominant topic: {month.dominant_topic or 'n/a'}",
        f"- Top modes: {format_top(month.top_modes, as_hours=True)}",
        f"- Top projects: {format_top(month.top_projects, as_hours=True)}",
        f"- Top topics: {format_top(month.top_topics, as_hours=True)}",
        "",
    ]
    if month.episode_count:
        episode_str = ", ".join(str(label) for label in month.episode_labels[:5])
        lines += [
            "## Episodes",
            f"- {month.episode_count} episode(s): {episode_str}",
            "",
        ]
    if month.highlights:
        lines += [
            "## Highlights",
            *[f"- {h}" for h in month.highlights],
            "",
        ]
    rendered = "\n".join(lines)
    output_path = output_dir / f"month-{month.month}.md" if output_dir else None
    wrote = write_text_if_changed(output_path, rendered) if output_path else False
    return CalendarArtifact(
        scale=CalendarScale.month,
        key=month.month,
        markdown=rendered,
        payload=month.to_dict(),
        output_path=output_path,
        wrote=wrote,
    )


def _render_quarter(quarter: TrajectoryQuarter, *, output_dir: Path | None) -> CalendarArtifact:
    delta_str = ""
    if quarter.active_delta_vs_prior is not None:
        sign = "+" if quarter.active_delta_vs_prior >= 0 else ""
        delta_str = f" ({sign}{quarter.active_delta_vs_prior / 3600:.1f}h vs prior quarter)"

    lines = [
        f"# {quarter.quarter} ({quarter.start_date.isoformat()} → {quarter.end_date.isoformat()})",
        "",
        "## Summary",
        f"- Active days: {quarter.active_days} / {quarter.total_days}",
        f"- Active hours: {format_hours(quarter.active_seconds)}{delta_str}",
        f"- Recovery hours: {format_hours(quarter.recovery_seconds)}",
        f"- Activity chains: {quarter.chain_count}",
        f"- Shell commands: {quarter.command_count}",
        f"- Git commits: {quarter.commit_count}",
        f"- Chat sessions: {quarter.chat_session_count}",
        f"- Transcripts: {quarter.transcript_count}",
        "",
        "## Focus",
        f"- Dominant mode: {quarter.dominant_mode or 'n/a'}",
        f"- Dominant project: {quarter.dominant_project or 'n/a'}",
        f"- Dominant topic: {quarter.dominant_topic or 'n/a'}",
        f"- Top modes: {format_top(quarter.top_modes, as_hours=True)}",
        f"- Top projects: {format_top(quarter.top_projects, as_hours=True)}",
        f"- Top topics: {format_top(quarter.top_topics, as_hours=True)}",
        "",
    ]
    if quarter.episode_count:
        lines += [
            "## Episodes",
            f"- {quarter.episode_count} episode(s) across {quarter.month_count} month(s)",
            "",
        ]
    rendered = "\n".join(lines)
    output_path = output_dir / f"quarter-{quarter.quarter}.md" if output_dir else None
    wrote = write_text_if_changed(output_path, rendered) if output_path else False
    return CalendarArtifact(
        scale=CalendarScale.quarter,
        key=quarter.quarter,
        markdown=rendered,
        payload=quarter.to_dict(),
        output_path=output_path,
        wrote=wrote,
    )


def _render_year(year: TrajectoryYear, *, output_dir: Path | None) -> CalendarArtifact:
    delta_str = ""
    if year.active_delta_vs_prior is not None:
        sign = "+" if year.active_delta_vs_prior >= 0 else ""
        delta_str = f" ({sign}{year.active_delta_vs_prior / 3600:.1f}h vs prior year)"

    lines = [
        f"# {year.year} ({year.start_date.isoformat()} → {year.end_date.isoformat()})",
        "",
        "## Summary",
        f"- Active days: {year.active_days} / {year.total_days}",
        f"- Active hours: {format_hours(year.active_seconds)}{delta_str}",
        f"- Recovery hours: {format_hours(year.recovery_seconds)}",
        f"- Quarters: {year.quarter_count}",
        f"- Activity chains: {year.chain_count}",
        f"- Git commits: {year.commit_count}",
        f"- Chat sessions: {year.chat_session_count}",
        f"- Transcripts: {year.transcript_count}",
        "",
        "## Focus",
        f"- Dominant mode: {year.dominant_mode or 'n/a'}",
        f"- Dominant project: {year.dominant_project or 'n/a'}",
        f"- Dominant topic: {year.dominant_topic or 'n/a'}",
        f"- Top modes: {format_top(year.top_modes, as_hours=True)}",
        f"- Top projects: {format_top(year.top_projects, as_hours=True)}",
        f"- Top topics: {format_top(year.top_topics, as_hours=True)}",
        "",
    ]
    if year.episode_count:
        lines += [
            "## Episodes",
            f"- {year.episode_count} episode(s) across {year.quarter_count} quarter(s)",
            "",
        ]
    rendered = "\n".join(lines)
    output_path = output_dir / f"year-{year.year}.md" if output_dir else None
    wrote = write_text_if_changed(output_path, rendered) if output_path else False
    return CalendarArtifact(
        scale=CalendarScale.year,
        key=str(year.year),
        markdown=rendered,
        payload=year.to_dict(),
        output_path=output_path,
        wrote=wrote,
    )
