"""Temporal knowledge-graph materialization from context rollups."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _month_to_date_str(month_key: str) -> Optional[str]:
    try:
        year, month = month_key.split("-")
        from datetime import date as _date

        return _date(int(year), int(month), 1).isoformat()
    except (ValueError, AttributeError):
        return None


def _date_ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end


def build_episode_nodes_temporal(days: Optional[int] = None) -> List[Dict]:
    from ..context.patterns import detect_episodes
    from ..context.signal_rollups import summarize_days

    day_list = summarize_days(days=days if days is not None else 90)
    episodes = detect_episodes(day_list)
    now = datetime.now(timezone.utc).isoformat()

    nodes = []
    for episode in episodes:
        nodes.append(
            {
                "id": f"episode:{episode.episode_id}",
                "kind": "episode",
                "label": episode.label,
                "valid_from": episode.start_date.isoformat(),
                "valid_until": episode.end_date.isoformat(),
                "confidence": episode.confidence,
                "dominant_project": episode.dominant_project,
                "dominant_topic": episode.dominant_topic,
                "source_timestamp": now,
                "evidence_count": episode.day_count_with_dominant,
                "properties": {
                    "dominant_mode": episode.dominant_mode,
                    "trigger": episode.trigger,
                    "day_count": (episode.end_date - episode.start_date).days + 1,
                },
            }
        )
    return nodes


def build_theme_nodes_temporal(days: Optional[int] = None) -> List[Dict]:
    from ..context.period_rollups import summarize_months as _summarize_months
    from ..context.period_rollups import summarize_weeks
    from ..context.signal_rollups import summarize_days
    from ..context.themes import detect_themes

    day_list = summarize_days(days=days if days is not None else 90)
    months = _summarize_months(day_list)
    weeks = summarize_weeks(day_list)
    themes = detect_themes(months, weeks)
    now = datetime.now(timezone.utc).isoformat()

    nodes = []
    for theme in themes:
        nodes.append(
            {
                "id": f"theme:{theme.kind}:{theme.name}",
                "kind": "theme",
                "label": theme.name,
                "valid_from": _month_to_date_str(theme.first_seen),
                "valid_until": _month_to_date_str(theme.last_seen),
                "confidence": min(0.5 + theme.month_count * 0.1, 0.95),
                "dominant_project": theme.name if theme.kind == "project" else None,
                "dominant_topic": theme.name if theme.kind == "topic" else None,
                "source_timestamp": now,
                "evidence_count": theme.month_count,
                "properties": {
                    "kind": theme.kind,
                    "total_hours": theme.total_hours,
                    "trend": theme.trend,
                    "months_active": theme.month_count,
                },
            }
        )
    return nodes


def build_temporal_edges(episode_nodes: List[Dict], theme_nodes: List[Dict]) -> List[Dict]:
    edges = []
    sorted_episodes = sorted(episode_nodes, key=lambda node: node.get("valid_from") or "")

    for index in range(len(sorted_episodes) - 1):
        current = sorted_episodes[index]
        nxt = sorted_episodes[index + 1]
        edges.append(
            {
                "source": current["id"],
                "target": nxt["id"],
                "kind": "precedes",
                "confidence": round((current["confidence"] + nxt["confidence"]) / 2, 3),
                "evidence": f"{current['valid_until']} → {nxt['valid_from']}",
            }
        )

    for episode in episode_nodes:
        episode_start, episode_end = episode.get("valid_from"), episode.get("valid_until")
        if not episode_start or not episode_end:
            continue
        for theme in theme_nodes:
            theme_start, theme_end = theme.get("valid_from"), theme.get("valid_until")
            if not theme_start or not theme_end:
                continue
            if not _date_ranges_overlap(episode_start, episode_end, theme_start, theme_end):
                continue
            edge_kind = "contains" if episode_start <= theme_start and episode_end >= theme_end else "overlaps"
            edges.append(
                {
                    "source": episode["id"],
                    "target": theme["id"],
                    "kind": edge_kind,
                    "confidence": round(episode["confidence"] * theme["confidence"], 3),
                    "evidence": f"ep {episode_start}–{episode_end} {edge_kind} theme {theme_start}–{theme_end}",
                }
            )

    return edges


def build_temporal_snapshot(*, days: Optional[int], output_dir: Path) -> dict[str, object]:
    episode_nodes = build_episode_nodes_temporal(days=days)
    theme_nodes = build_theme_nodes_temporal(days=days)
    all_nodes = episode_nodes + theme_nodes
    edges = build_temporal_edges(episode_nodes, theme_nodes)

    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = output_dir / "temporal-nodes.json"
    edges_path = output_dir / "temporal-edges.json"

    nodes_path.write_text(
        json.dumps(all_nodes, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    edges_path.write_text(
        json.dumps(edges, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    return {
        "node_count": len(all_nodes),
        "episode_count": len(episode_nodes),
        "theme_count": len(theme_nodes),
        "edge_count": len(edges),
        "nodes_path": nodes_path,
        "edges_path": edges_path,
    }
