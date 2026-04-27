"""Narrative synthesis — combine all narrative modules into a cohesive retrospective.

One function: week_retrospective(date) → a complete markdown report ready for chat.
Calls: day_brief, week_story, day_episodes, day_flow, week_diet, behavioral_snapshot,
        interruption_pattern, evidence bundles.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from . import day_brief, week_story
from .episode import episode_timeline, top_episodes
from .flow import day_flow, interruption_pattern
from .diet import content_diet
from .trends import behavioral_snapshot


def week_retrospective(d: date | None = None) -> str:
    """Complete weekly retrospective as markdown — ready for chat.

    Calls all narrative modules and produces a comprehensive report
    with executive summary, daily breakdown, behavioral trends,
    content diet, and notable episodes.
    """
    if d is None:
        d = date.today()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)

    lines = [f"# Week of {mon.strftime('%B %d, %Y')} Retrospective", ""]

    # ── Executive summary ─────────────────────────────────────────────
    lines.append("## Executive Summary")
    lines.append("")

    week = week_story(mon)
    lines.append(week.summary)
    lines.append("")

    # Behavioral context
    bs = behavioral_snapshot(d)
    lines.append(bs.summary)
    lines.append("")

    # ── Daily breakdown ──────────────────────────────────────────────
    lines.append("## Daily Breakdown")
    lines.append("")

    for i in range(7):
        day = mon + timedelta(days=i)
        brief = day_brief(day)
        if brief.spans:
            lines.append(f"- **{brief.title}**: {brief.summary}")
    lines.append("")

    # ── Highlights ──────────────────────────────────────────────────
    lines.append("## Highlights & Notable Moments")
    lines.append("")

    if week.evidence:
        for e in week.evidence[:8]:
            lines.append(f"- **{e.get('date', '?')}**: {e.get('claim', '')}")
    lines.append("")

    # ── Attention flow ──────────────────────────────────────────────
    lines.append("## Attention Flow")
    lines.append("")

    flow = day_flow(d)
    if flow.spans:
        lines.append(flow.flow_narrative)
        lines.append(f"\nDominant pattern: {flow.dominant_flow}")
        lines.append(f"Interruptions: {flow.interruptions} work→distraction switches")
    lines.append("")

    # ── Episodes ────────────────────────────────────────────────────
    lines.append("## Top Deep Work Episodes")
    lines.append("")

    eps = top_episodes(mon, sun, n=10, deep_work_only=True)
    if eps:
        for ep in eps[:8]:
            lines.append(f"- {ep.date} {ep.start_time}-{ep.end_time}: {ep.one_liner}")
    else:
        lines.append("No deep work episodes detected this week.")
    lines.append("")

    # ── Content diet ───────────────────────────────────────────────
    lines.append("## Content Diet")
    lines.append("")

    diet = content_diet(mon, sun)
    lines.append(diet.brief)
    if diet.top_technologies:
        lines.append(f"\nTechnologies: {', '.join(t for t,_ in diet.top_technologies[:8])}")
    if diet.top_domains:
        lines.append(f"Domains: {', '.join(d for d,_ in diet.top_domains[:5])}")
    lines.append("")

    # ── Trends ──────────────────────────────────────────────────────
    lines.append("## Behavioral Trends")
    lines.append("")

    for s in bs.scores.values():
        lines.append(f"- {s.narrative}")
    lines.append("")

    # ── Interruption analysis ──────────────────────────────────────
    lines.append("## Focus Hygiene")
    lines.append("")

    ip = interruption_pattern(d)
    lines.append(f"Interruption rate: {ip['avg_pct']:.1f}% of spans ({ip['trend']})")
    lines.append("")

    return "\n".join(lines)


def day_retrospective(d: date | None = None) -> str:
    """Single-day retrospective."""
    if d is None: d = date.today()

    lines = [f"# {d.strftime('%A %B %d, %Y')} Retrospective", ""]
    brief = day_brief(d)
    lines.append(brief.summary)
    lines.append("")

    lines.append("## Episode Timeline")
    lines.append(episode_timeline(d))
    lines.append("")

    flow = day_flow(d)
    if flow.spans:
        lines.append("## Attention Flow")
        lines.append(flow.flow_narrative)
    lines.append("")

    return "\n".join(lines)
