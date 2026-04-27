"""Cross-source evidence events for narrative synthesis.

Joins v2 focus spans with git, terminal, polylogue, sleep, and health data
to produce per-day evidence bundles suitable for LLM consumption.
"""
from __future__ import annotations

import json, os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)


@dataclass(frozen=True)
class EvidenceBundle:
    date: date
    date_label: str         # "Monday March 17, 2025"

    # Focus (from v2 spans)
    focus_hours: float
    top_activities: list[tuple[str, float]]    # (activity, hours)
    top_subjects: list[tuple[str, float]]      # (subject, hours)
    attention_profile: dict[str, float]        # deep/shallow/background/scanning/waiting hours
    high_story_spans: list[dict]               # spans with story_priority=high
    productive_score: float                    # avg across spans
    deep_work_hours: float

    # Git (from baseline/live)
    commits: list[dict]       # [{repo, message, authored_at, files_changed}]
    commit_count: int

    # Terminal
    notable_commands: list[dict]  # [{command, project, duration_s}]
    shell_sessions: int

    # AI (from polylogue)
    ai_chats: list[dict]       # [{provider, model, messages, work_kind, project}]
    ai_cost: float

    # Sleep / Health
    sleep_hours: float | None
    sleep_score: int | None
    steps: int | None
    stress_avg: float | None
    heart_rate_avg: float | None

    # Substance
    substance_doses: int

    # Narrative-ready summary
    brief: str    # 2-3 sentence natural-language summary

    @property
    def to_context_string(self) -> str:
        """Dense LLM-ready context block."""
        lines = [f"## {self.date_label}", f"Focus: {self.focus_hours:.1f}h"]
        if self.top_activities:
            lines.append("Activities: " + ", ".join(
                f"{a}({h:.1f}h)" for a, h in self.top_activities[:5]))
        if self.commit_count:
            lines.append(f"Commits: {self.commit_count} across repos")
            for c in self.commits[:3]:
                lines.append(f"  - {c.get('repo','?')}: {c.get('message','')[:80]}")
        if self.notable_commands:
            lines.append("Shell: " + "; ".join(
                c.get('command', '')[:60] for c in self.notable_commands[:5]))
        if self.ai_chats:
            lines.append(f"AI: {len(self.ai_chats)} sessions"
                         + (f", ${self.ai_cost:.2f}" if self.ai_cost else ""))
        if self.sleep_hours:
            lines.append(f"Sleep: {self.sleep_hours:.1f}h"
                         + (f" (score: {self.sleep_score})" if self.sleep_score else ""))
        if self.high_story_spans:
            lines.append(f"Notable: {len(self.high_story_spans)} high-priority moments")
        return "\n".join(lines)


def day_evidence(d: date) -> EvidenceBundle | None:
    """Build a complete evidence bundle for one day.

    Joins v2 focus spans with git, terminal, AI, sleep, health, substance.
    Returns None if there's no focus data for this day.
    """
    db = duckdb.connect(DB_PATH, read_only=True)

    # Check for data
    has = db.execute(
        "SELECT count(*) FROM focus_spans_v2 WHERE time__local_date = ?",
        [d.isoformat()],
    ).fetchone()[0]
    if not has:
        db.close()
        return None

    # ── Focus spans ────────────────────────────────────────────────────
    rows = db.execute("""
        SELECT * FROM focus_spans_v2
        WHERE time__local_date = ?
        ORDER BY time__start_s ASC
    """, [d.isoformat()]).fetchall()
    cols = [c[0] for c in db.execute("DESCRIBE focus_spans_v2").fetchall()]
    db.close()

    spans = [dict(zip(cols, r)) for r in rows]

    # Activity profile
    act_hours: dict[str, float] = defaultdict(float)
    attn_hours: dict[str, float] = defaultdict(float)
    subj_hours: dict[str, float] = defaultdict(float)
    total_s = 0
    prod_scores = []
    dw_s = 0.0

    for s in spans:
        dur = float(s.get("time__duration_s", 0) or 0)
        total_s += dur
        act = s.get("semantic__activity", "unknown")
        attn = s.get("semantic__attention_level", "shallow")
        subj = s.get("semantic__context_sentence", "")
        act_hours[act] += dur / 3600
        attn_hours[attn] += dur / 3600
        if subj and subj != "None":
            subj_hours[subj] += dur / 3600
        try:
            prod_scores.append(float(s.get("behavior__productive_score", 0) or 0))
        except (ValueError, TypeError):
            pass
        if s.get("behavior__deep_work_candidate") == "True":
            dw_s += dur

    high_story = [
        s for s in spans
        if s.get("memory__story_priority") == "high"
    ]

    # ── Git ────────────────────────────────────────────────────────────
    commits = _get_git_commits(d)

    # ── Terminal ───────────────────────────────────────────────────────
    shell = _get_shell_activity(d)

    # ── AI ─────────────────────────────────────────────────────────────
    ai = _get_ai_activity(d)

    # ── Sleep / Health ─────────────────────────────────────────────────
    sleep_data = _get_sleep(d)
    health_data = _get_health(d)

    # ── Substance ─────────────────────────────────────────────────────
    substance = _get_substance(d)

    # ── Build brief ────────────────────────────────────────────────────
    parts = [f"{d.strftime('%A %B %d')}: {total_s/3600:.1f}h active. "]
    top5 = sorted(act_hours.items(), key=lambda x: -x[1])[:4]
    parts.append("Mostly " + ", ".join(f"{a}" for a, _ in top5) + ". ")
    if commits:
        parts.append(f"{len(commits)} commits. ")
    if ai:
        parts.append(f"{len(ai)} AI sessions. ")
    if sleep_data and sleep_data.get("hours"):
        parts.append(f"Slept {sleep_data['hours']:.1f}h. ")

    return EvidenceBundle(
        date=d,
        date_label=d.strftime("%A %B %d, %Y"),
        focus_hours=total_s / 3600,
        top_activities=sorted(act_hours.items(), key=lambda x: -x[1])[:8],
        top_subjects=sorted(subj_hours.items(), key=lambda x: -x[1])[:10],
        attention_profile=dict(attn_hours),
        high_story_spans=high_story,
        productive_score=sum(prod_scores) / len(prod_scores) if prod_scores else 0.0,
        deep_work_hours=dw_s / 3600,
        commits=commits,
        commit_count=len(commits),
        notable_commands=shell[:10],
        shell_sessions=len(shell),
        ai_chats=ai,
        ai_cost=sum(c.get("cost", 0) or 0 for c in ai),
        sleep_hours=sleep_data.get("hours") if sleep_data else None,
        sleep_score=sleep_data.get("score") if sleep_data else None,
        steps=health_data.get("steps") if health_data else None,
        stress_avg=health_data.get("stress_avg") if health_data else None,
        heart_rate_avg=health_data.get("hr_avg") if health_data else None,
        substance_doses=substance,
        brief="".join(parts),
    )


def week_evidence(d: date | None = None) -> list[EvidenceBundle]:
    """Evidence bundles for every day in the week containing `d`."""
    if d is None:
        d = date.today()
    mon = d - timedelta(days=d.weekday())
    bundles = []
    for i in range(7):
        day = mon + timedelta(days=i)
        eb = day_evidence(day)
        if eb:
            bundles.append(eb)
    return bundles


# ── Cross-source fetchers (lightweight) ─────────────────────────────────

def _get_git_commits(d: date) -> list[dict]:
    """Get git commits for a date from existing sources module."""
    try:
        from lynchpin.sources.git import commits_in_range
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        commits = commits_in_range(start, end)
        return [
            {
                "repo": c.repo_name or "?",
                "message": c.message or "",
                "authored_at": c.authored_at.isoformat() if c.authored_at else "",
                "files_changed": getattr(c, 'files_changed', 0) or 0,
            }
            for c in commits
        ]
    except Exception:
        return []


def _get_shell_activity(d: date) -> list[dict]:
    """Get notable shell commands for a date."""
    try:
        from lynchpin.sources.terminal import commands
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        cmds = commands(start, end)
        return [
            {
                "command": c.command or "",
                "project": c.project or "",
                "duration_s": c.duration_s or 0,
                "cwd": c.cwd or "",
            }
            for c in cmds
        ]
    except Exception:
        return []


def _get_ai_activity(d: date) -> list[dict]:
    """Get AI chat sessions for a date."""
    try:
        from lynchpin.sources.polylogue import daily_activity
        act = daily_activity(d, d + timedelta(days=1))
        if act:
            return [
                {
                    "provider": getattr(p, 'provider', '?'),
                    "model": getattr(p, 'model', '?'),
                    "messages": getattr(p, 'message_count', 0) or 0,
                    "work_kind": getattr(p, 'primary_work_kind', '?'),
                    "cost": getattr(p, 'cost_usd', 0) or 0,
                }
                for p in getattr(act, 'sessions', [])
            ]
    except Exception:
        pass
    return []


def _get_sleep(d: date) -> dict | None:
    """Get sleep data for a date."""
    try:
        from lynchpin.sources.sleep import sleep_for_date
        sleep = sleep_for_date(d)
        if sleep:
            return {
                "hours": sleep.total_minutes / 60 if sleep.total_minutes else 0,
                "score": sleep.avg_score,
            }
    except Exception:
        pass
    return None


def _get_health(d: date) -> dict | None:
    """Get health metrics for a date."""
    try:
        from lynchpin.sources.health import daily_health_summary
        summary = daily_health_summary(d, d + timedelta(days=1))
        if summary:
            return {
                "steps": getattr(summary, 'steps', None),
                "stress_avg": getattr(summary, 'stress_avg', None),
                "hr_avg": getattr(summary, 'hr_avg', None),
            }
    except Exception:
        pass
    return None


def _get_substance(d: date) -> int:
    """Get substance doses for a date."""
    try:
        from lynchpin.sources.substance import daily_summary
        s = daily_summary(d, d + timedelta(days=1))
        if s:
            return s.dose_count
    except Exception:
        pass
    return 0
