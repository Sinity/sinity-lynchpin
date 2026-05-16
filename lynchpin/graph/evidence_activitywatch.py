"""ActivityWatch source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import CostClass, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import date_to_dt_range, logical_date
from .evidence_projects import include_project, normalize_project


def attention(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import attention as impl

    return impl(*args, **kwargs)


def circadian(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import circadian as impl

    return impl(*args, **kwargs)


def deep_work(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import deep_work as impl

    return impl(*args, **kwargs)


def focus_timeline(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import focus_timeline as impl

    return impl(*args, **kwargs)


def fragmentation(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import fragmentation as impl

    return impl(*args, **kwargs)


def loops(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import loops as impl

    return impl(*args, **kwargs)


def project_focus_days(*args: Any, **kwargs: Any) -> Any:
    from ..sources.activitywatch import project_focus_days as impl

    return impl(*args, **kwargs)


def add_focus(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
    mode: CostClass,
) -> None:
    start_dt, end_dt = date_to_dt_range(start, end)
    if mode != "local-fast":
        for idx, span in enumerate(
            focus_timeline(start=start_dt, end=end_dt, min_duration_s=60.0)
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
                        "span_source": span.source,
                        "keypress_count": span.keypress_count,
                        "keylog_state": span.keylog_state,
                    },
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )

        for idx, block in enumerate(deep_work(start=start_dt, end=end_dt)):
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
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )

        for profile in circadian(start=start, end=end):
            project = normalize_project(profile.dominant_project)
            if not include_project(project, selected):
                continue
            nodes.append(
                EvidenceNode(
                    id=f"aw-circadian:{profile.date.isoformat()}:{project}",
                    kind="circadian_profile",
                    source="activitywatch",
                    date=profile.date,
                    project=project,
                    summary=f"circadian: peak hour={profile.hour}, dominant={profile.dominant_mode}",
                    payload={
                        "peak_hour": profile.hour,
                        "active_min": profile.active_min,
                        "dominant_mode": profile.dominant_mode,
                    },
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )

        for idx, loop in enumerate(loops(start=start_dt, end=end_dt)):
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
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )

        for frag in fragmentation(start=start, end=end):
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
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )

        for attn in attention(start=start, end=end):
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
                    provenance=EvidenceProvenance("activitywatch", "local-heavy"),
                )
            )
        return

    for focus in project_focus_days(start=start_dt, end=end_dt):
        project = normalize_project(focus.project)
        if not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"aw-focus:{focus.date}:{project}",
                kind="focus_day",
                source="activitywatch",
                date=focus.date,
                project=project,
                summary=f"{project} focus {focus.duration_s / 3600:.2f}h",
                payload={"duration_s": focus.duration_s},
                provenance=EvidenceProvenance("activitywatch", "local-fast"),
            )
        )
