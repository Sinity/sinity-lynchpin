"""ActivityWatch source-node builders for the evidence graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import date_to_dt_range, logical_date
from .evidence_projects import include_project, normalize_project


def ensure_activitywatch_derived(*, start: date, end: date) -> None:
    from ..materialization import ensure_materialized

    ensure_materialized(
        "activitywatch_derived",
        window=(start, end + timedelta(days=1)),
        budget="manual",
    )


def attention(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_attention

    return tuple(iter_derived_attention(*args, **kwargs))


def circadian(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_circadian

    return tuple(iter_derived_circadian(*args, **kwargs))


def deep_work(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_deep_work

    return tuple(iter_derived_deep_work(*args, **kwargs))


def focus_timeline(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import focus_timeline as impl

    return impl(*args, **kwargs)


def focus_spans(*args: Any, **kwargs: Any) -> Any:
    product_kwargs = dict(kwargs)
    product_kwargs.pop("enrich_polylogue", None)
    from ..sources.activitywatch_derived import iter_derived_focus_spans

    return tuple(iter_derived_focus_spans(*args, **product_kwargs))


def fragmentation(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_fragmentation

    return tuple(iter_derived_fragmentation(*args, **kwargs))


def loops(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_loops

    return tuple(iter_derived_loops(*args, **kwargs))


def project_focus_days(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch_derived import iter_derived_project_focus_days

    return tuple(iter_derived_project_focus_days(*args, **kwargs))


def add_focus(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
) -> None:
    ensure_activitywatch_derived(start=start, end=end)

    start_dt, end_dt = date_to_dt_range(start, end)
    for idx, span in enumerate(
        focus_spans(
            start=start_dt,
            end=end_dt,
            min_duration_s=60.0,
            enrich_polylogue=False,
            ensure=False,
        )
    ):
        project = normalize_project(span.project)
        if span.kind != "focused" or not include_project(project, selected):
            continue
        title = str(span.title or "").strip()
        app = str(span.app or "").strip()
        summary_bits = [f"{span.duration_s / 60:.0f}m focus"]
        if app:
            summary_bits.append(app)
        if title:
            summary_bits.append(title[:120])
        nodes.append(
            EvidenceNode(
                id=f"aw-focus-span:{span.start.isoformat()}:{idx}:{project}",
                kind="focus_span",
                source="activitywatch",
                date=logical_date(span.start),
                project=project,
                start=span.start,
                end=span.end,
                summary=" - ".join(summary_bits),
                payload={
                    "duration_s": span.duration_s,
                    "app": span.app,
                    "title": span.title,
                    "mode": span.mode,
                    "span_source": getattr(span, "source", "aw_trimmed"),
                    "keypress_count": span.keypress_count,
                    "keylog_state": span.keylog_state,
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )

    for idx, block in enumerate(deep_work(start=start_dt, end=end_dt, ensure=False)):
        project = normalize_project(block.project)
        if block.focus_ratio < 0.5 or not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"aw-deep-work:{block.start.isoformat()}:{idx}",
                kind="deep_work_block",
                source="activitywatch",
                date=logical_date(block.start),
                project=project,
                start=block.start,
                end=block.end,
                summary=f"deep work {block.duration_min:.0f}m ({block.mode}, ratio={block.focus_ratio:.2f})",
                payload={
                    "duration_min": round(block.duration_min, 1),
                    "focus_ratio": round(block.focus_ratio, 2),
                    "mode": block.mode,
                    "app_switches": block.app_switches,
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )

    for peak in _circadian_peaks(circadian(start=start, end=end, ensure=False)):
        project = normalize_project(peak.dominant_project)
        if not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"aw-circadian:{peak.date.isoformat()}",
                kind="circadian_profile",
                source="activitywatch",
                date=peak.date,
                project=project,
                summary=f"circadian: peak hour={peak.hour}, dominant={peak.dominant_mode}",
                payload={
                    "peak_hour": peak.hour,
                    "peak_active_min": peak.active_min,
                    "active_min": round(peak.day_active_min, 1),
                    "hours_observed": peak.hours_observed,
                    "dominant_mode": peak.dominant_mode,
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )

    for idx, loop in enumerate(loops(start=start_dt, end=end_dt, ensure=False)):
        project = normalize_project(loop.dominant_project)
        if loop.span_count < 2 or not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"aw-loop:{loop.date.isoformat()}:{idx}",
                kind="focus_loop",
                source="activitywatch",
                date=loop.date,
                project=project,
                summary=f"focus loop: {loop.switch_count} switches {loop.context_a}<->{loop.context_b}, {loop.duration_min:.0f}m",
                payload={
                    "switch_count": loop.switch_count,
                    "span_count": loop.span_count,
                    "context_a": loop.context_a,
                    "context_b": loop.context_b,
                    "duration_min": round(loop.duration_min, 1),
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )

    for frag in fragmentation(start=start, end=end, ensure=False):
        nodes.append(
            EvidenceNode(
                id=f"aw-frag:{frag.date.isoformat()}",
                kind="fragmentation_day",
                source="activitywatch",
                date=frag.date,
                project=None,
                summary=f"fragmentation: {frag.total_switches} switches, avg focus={frag.avg_focus_min:.0f}m, longest={frag.longest_focus_min:.0f}m",
                payload={
                    "total_switches": frag.total_switches,
                    "avg_focus_min": round(frag.avg_focus_min, 1),
                    "longest_focus_min": round(frag.longest_focus_min, 1),
                    "fragmentation_index": round(frag.fragmentation, 2),
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )

    for attn in attention(start=start, end=end, ensure=False):
        project = normalize_project(attn.top_project)
        if not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"aw-attn:{attn.date.isoformat()}:{project}",
                kind="attention_day",
                source="activitywatch",
                date=attn.date,
                project=project,
                summary=f"attention: entropy={attn.entropy:.2f}, gini={attn.gini:.2f}, top={attn.top_project}",
                payload={
                    "entropy": round(attn.entropy, 2),
                    "gini": round(attn.gini, 2),
                    "top_project": attn.top_project,
                    "project_count": attn.project_count,
                },
                provenance=EvidenceProvenance("activitywatch", "materialized"),
            )
        )
    for focus in project_focus_days(start=start_dt, end=end_dt, ensure=False):
        _append_project_focus_day(nodes, focus=focus, selected=selected)


@dataclass(frozen=True)
class _CircadianPeak:
    date: date
    hour: int
    active_min: float
    dominant_mode: str | None
    dominant_project: str | None
    day_active_min: float
    hours_observed: int


def _circadian_peaks(profiles: Iterable[Any]) -> tuple[_CircadianPeak, ...]:
    """Reduce hourly circadian rows to one computed peak per day.

    The product holds one row per hour; the peak is the hour with the most
    active minutes, ties broken by the earlier hour, so input order cannot
    change it.
    """
    by_day: dict[date, list[Any]] = {}
    for profile in profiles:
        by_day.setdefault(profile.date, []).append(profile)
    peaks = []
    for day in sorted(by_day):
        hours = by_day[day]
        top = min(hours, key=lambda row: (-row.active_min, row.hour))
        peaks.append(
            _CircadianPeak(
                date=day,
                hour=top.hour,
                active_min=top.active_min,
                dominant_mode=top.dominant_mode,
                dominant_project=top.dominant_project,
                day_active_min=sum(row.active_min for row in hours),
                hours_observed=len({row.hour for row in hours}),
            )
        )
    return tuple(peaks)


def _append_project_focus_day(
    nodes: list[EvidenceNode],
    *,
    focus: Any,
    selected: set[str],
) -> None:
    project = normalize_project(focus.project)
    if not include_project(project, selected):
        return
    nodes.append(
        EvidenceNode(
            id=f"aw-focus:{focus.date}:{project}",
            kind="focus_day",
            source="activitywatch",
            date=focus.date,
            project=project,
            summary=f"{project} focus {focus.duration_s / 3600:.2f}h",
            payload={"duration_s": focus.duration_s},
            provenance=EvidenceProvenance("activitywatch", "materialized"),
        )
    )
