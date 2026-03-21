"""Hierarchical narrative synthesis with bidirectional passes and enhancement workflows.

Core synthesis (3-pass convergent):
  Pass 1 (bottom-up): day → week → month → quarter, each level synthesizing
    its children's full narrative text.
  Pass 2 (top-down): higher-level patterns re-contextualize lower-level
    narratives with arc awareness.
  Pass 3 (refinement): re-synthesize parent from enriched children.

Enhancement passes (composable, specialized):
  - fact_checker: verify claims against raw data
  - dependency_tracer: find causal chains between repos
  - energy_analyst: physiological assessment from activity patterns
  - delegation_auditor: classify human vs AI-driven work
  - continuity_editor: ensure cross-scale narrative consistency

Workflows compose these into named pipelines.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .narrative import (
    Narrative,
    NarrativeBackend,
    NarrativeKind,
    _NARRATIVE_LOG_DIR,
    _coalesce_aw_spans,
    _parse_date_range,
    _query_atuin_commands,
    _query_duckdb_context,
    _query_git_commits,
    _query_git_detailed,
    _query_sleep_data,
    build_scale_prompts,
    generate_narrative,
)

log = logging.getLogger(__name__)


def _progress(msg: str, *args: object) -> None:
    """Print progress to stderr so it's visible in background task output."""
    import sys
    from datetime import datetime as _dt

    ts = _dt.now().strftime("%H:%M:%S")
    text = msg % args if args else msg
    print(f"[{ts}] {text}", file=sys.stderr, flush=True)

# ---------------------------------------------------------------------------
# Scale hierarchy
# ---------------------------------------------------------------------------

SCALE_HIERARCHY: list[NarrativeKind] = [
    NarrativeKind.day,
    NarrativeKind.week,
    NarrativeKind.month,
    NarrativeKind.quarter,
]


def child_scale(scale: NarrativeKind) -> Optional[NarrativeKind]:
    """Return the next finer scale, or ``None`` for day."""
    try:
        idx = SCALE_HIERARCHY.index(scale)
    except ValueError:
        return None
    return SCALE_HIERARCHY[idx - 1] if idx > 0 else None


def child_keys(scale: NarrativeKind, key: str) -> list[str]:
    """Return constituent keys at the child scale.

    Examples::

        child_keys(week, "2026-W11")  → ["2026-03-09", ..., "2026-03-15"]
        child_keys(month, "2026-03")  → ["2026-W09", "2026-W10", ...]
        child_keys(quarter, "2026-Q1") → ["2026-01", "2026-02", "2026-03"]
    """
    if scale is NarrativeKind.week:
        year, week_num = int(key[:4]), int(key.split("W")[1])
        return [
            date.fromisocalendar(year, week_num, d).isoformat()
            for d in range(1, 8)
        ]

    if scale is NarrativeKind.month:
        year, month = int(key[:4]), int(key[5:7])
        # Find all ISO weeks that overlap this month
        first = date(year, month, 1)
        last_day = (
            date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
        )
        weeks: list[str] = []
        d = first
        seen: set[str] = set()
        while d <= last_day:
            iso = d.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
            if wk not in seen:
                seen.add(wk)
                weeks.append(wk)
            d += timedelta(days=1)
        return weeks

    if scale is NarrativeKind.quarter:
        year, q = int(key[:4]), int(key[-1])
        return [f"{year}-{(q - 1) * 3 + m:02d}" for m in range(1, 4)]

    return []


def _prior_key(scale: NarrativeKind, key: str) -> Optional[str]:
    """Return the key for the period immediately preceding *key*."""
    try:
        if scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            if week_num > 1:
                return f"{year}-W{week_num - 1:02d}"
            prior = date.fromisocalendar(year - 1, 52, 1)
            return f"{prior.isocalendar()[0]}-W{prior.isocalendar()[1]:02d}"
        if scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            if month > 1:
                return f"{year}-{month - 1:02d}"
            return f"{year - 1}-12"
        if scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            if q > 1:
                return f"{year}-Q{q - 1}"
            return f"{year - 1}-Q4"
    except (ValueError, IndexError):
        pass
    return None


def _next_key(scale: NarrativeKind, key: str) -> Optional[str]:
    """Return the key for the period immediately following *key*."""
    try:
        if scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            if week_num < 52:
                return f"{year}-W{week_num + 1:02d}"
            next_year = year + 1
            return f"{next_year}-W01"
        if scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            if month < 12:
                return f"{year}-{month + 1:02d}"
            return f"{year + 1}-01"
        if scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            if q < 4:
                return f"{year}-Q{q + 1}"
            return f"{year + 1}-Q1"
    except (ValueError, IndexError):
        pass
    return None


# ---------------------------------------------------------------------------
# Narrative storage
# ---------------------------------------------------------------------------


def load_narratives(kind: str, keys: list[str]) -> dict[str, str]:
    """Load the most recent narrative text for each *(kind, key)* pair.

    Reads JSONL logs from ``_NARRATIVE_LOG_DIR``.  For duplicate entries the
    latest by ``generated_at`` wins.
    """
    if not keys:
        return {}
    target_keys = set(keys)
    results: dict[str, tuple[str, str]] = {}  # key → (generated_at, text)

    log_dir = _NARRATIVE_LOG_DIR
    if not log_dir.exists():
        return {}

    for log_path in sorted(log_dir.glob("narrative_*.jsonl")):
        try:
            with log_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("kind") != kind:
                        continue
                    entry_key = entry.get("key", "")
                    if entry_key not in target_keys:
                        continue
                    gen_at = entry.get("generated_at", "")
                    text = entry.get("text", "")
                    if not text:
                        continue
                    prev = results.get(entry_key)
                    if prev is None or gen_at > prev[0]:
                        results[entry_key] = (gen_at, text)
        except OSError:
            continue

    return {k: v[1] for k, v in results.items()}


# ---------------------------------------------------------------------------
# Synthesis prompts
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = """\
You are Sinity's narrative synthesizer. You receive completed retrospective
narratives for constituent periods and trajectory data for the target period.

Your job is to synthesize a higher-order narrative that:
1. Identifies arcs and themes that span multiple constituent periods
2. Traces project phase transitions and energy patterns across the range
3. Highlights what changed between periods and why
4. Surfaces cross-cutting patterns invisible at the lower scale
5. References specific findings from constituent narratives by date/period

Do NOT restate constituent narratives. Synthesize: find the story that only
emerges when you see all the parts together. Be specific — cite dates,
projects, commit counts, mode shifts.
"""

TOP_DOWN_SYSTEM_PROMPT = """\
You are re-contextualizing a {child_scale} narrative with knowledge from the
{parent_scale} synthesis.

The {parent_scale} narrative for {parent_key} identified these cross-scale
patterns and arcs:

{parent_text}

---

Below is the existing {child_scale} narrative for {child_key}:

{child_text}

---

Re-write this {child_scale} narrative, enriching it with awareness of its
role in the larger arc. What looked unremarkable in isolation may be the
start of a significant trend. What looked like an anomaly may be part of a
pattern. Preserve the factual detail but add the cross-scale context.
"""


def _build_git_only_prompt(
    scale: NarrativeKind,
    key: str,
) -> Optional[str]:
    """Build a prompt for periods with no trajectory warehouse data.

    Uses git commit history as the primary evidence source.  Returns None
    if no git activity exists for the period either.
    """
    # Parse date range from the key
    start: date | None = None
    end: date | None = None
    try:
        if scale is NarrativeKind.day:
            start = end = date.fromisoformat(key)
        elif scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            start = date.fromisocalendar(year, week_num, 1)
            end = date.fromisocalendar(year, week_num, 7)
        elif scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            start = date(year, month, 1)
            next_month = date(year + (month // 12), (month % 12) + 1, 1)
            end = next_month - timedelta(days=1)
        elif scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            start = date(year, (q - 1) * 3 + 1, 1)
            end_month = q * 3
            next_start = date(year + (end_month // 12), (end_month % 12) + 1, 1)
            end = next_start - timedelta(days=1)
    except (ValueError, IndexError):
        return None

    if start is None or end is None:
        return None

    git_context = _query_git_commits(start, end)
    duckdb_context = _query_duckdb_context(start, end)
    aw_spans = _coalesce_aw_spans(start, end)
    atuin_cmds = _query_atuin_commands(start, end)

    parts = [
        f"Generate a retrospective for: {key}",
        f"Period: {start} to {end}",
        "",
        "Note: No trajectory/ActivityWatch data is available for this period.",
        "Use the git commit evidence below as the primary data source.",
        "You may also use your tools to investigate further.",
        "",
    ]

    if aw_spans:
        parts.extend(["## Desktop activity", "", aw_spans, ""])

    if atuin_cmds:
        parts.extend(["## Shell commands", "", atuin_cmds, ""])

    if git_context and git_context != "No commits found.":
        parts.extend([f"## Git commits ({start} to {end})", "", git_context])

    if duckdb_context:
        parts.extend(["", "## Warehouse data", "", duckdb_context])

    if len(parts) <= 5:  # Only the header and "note" text
        return None

    return "\n".join(parts)


def _duckdb_query(db_path: Path, sql: str) -> str:
    """Run a DuckDB query and return the output text, or empty string on failure."""
    import shutil

    duckdb_cli = shutil.which("duckdb")
    if not duckdb_cli:
        return ""
    try:
        result = subprocess.run(
            [duckdb_cli, str(db_path), "-c", sql],
            capture_output=True, text=True, check=False, timeout=15,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _build_scale_enrichment(scale: NarrativeKind, key: str) -> str:
    """Build aggregate data appropriate to the scale level.

    Hierarchy principle: each level gets metrics and distributions, NOT raw
    events. Raw events belong at the day level (via _enrich_prompt_for_sdk).
    Synthesis levels get aggregate views that help identify cross-period patterns.
    """
    start, end = _parse_date_range(scale, key)
    if start is None or end is None:
        return ""

    db_path = Path("artefacts/lynchpin/warehouse.duckdb")
    if not db_path.exists():
        return ""

    parts: list[str] = []

    if scale is NarrativeKind.week:
        # Per-day trajectory summary
        out = _duckdb_query(db_path,
            f"SELECT date, round(active_seconds/3600.0,1) as active_h, "
            f"round(recovery_seconds/3600.0,1) as recovery_h, "
            f"dominant_mode, dominant_project, command_count, commit_count "
            f"FROM trajectory_day WHERE date BETWEEN '{start}' AND '{end}' ORDER BY date")
        if out:
            parts.append(f"### Per-day trajectory\n{out}")

        # Per-project time across the week
        out = _duckdb_query(db_path,
            f"SELECT project, round(SUM(duration_seconds)/3600.0,1) as hours, COUNT(*) as days_active "
            f"FROM trajectory_day_project WHERE date BETWEEN '{start}' AND '{end}' "
            f"AND duration_seconds > 300 GROUP BY project ORDER BY hours DESC")
        if out:
            parts.append(f"### Project time (week total)\n{out}")

        # AW app-time distribution with top window titles per app
        try:
            from ..sources.captures.activitywatch import window_events
            dt_start = datetime(start.year, start.month, start.day)
            dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)
            events = list(window_events(start=dt_start, end=dt_end))
            if events:
                from collections import Counter, defaultdict
                # Build spans to compute duration
                spans: list[tuple[str, str, float]] = []
                for i, e in enumerate(events):
                    app = e.data.get("app", "")
                    title = e.data.get("title", "")[:80]
                    next_ts = events[i + 1].start if i + 1 < len(events) else e.start
                    dur = (next_ts - e.start).total_seconds()
                    if 0 < dur < 3600:  # skip >1h gaps
                        spans.append((app, title, dur))
                # Aggregate by app
                app_time: Counter[str] = Counter()
                app_titles: dict[str, Counter[str]] = defaultdict(Counter)
                for app, title, dur in spans:
                    app_time[app] += dur
                    app_titles[app][title] += dur
                lines = ["### App time distribution"]
                for app, secs in app_time.most_common(15):
                    top_titles = app_titles[app].most_common(5)
                    title_str = "; ".join(f"{t[:60]} ({s/60:.0f}m)" for t, s in top_titles)
                    lines.append(f"  {app}: {secs/3600:.1f}h — {title_str}")
                parts.append("\n".join(lines))
        except Exception:
            pass

        # Git oneline commits (medium density — messages but no diffstat)
        from .narrative import _GIT_REPOS
        git_sections: list[str] = []
        for repo in _GIT_REPOS:
            repo_path = Path(f"/realm/project/{repo}")
            if not repo_path.exists():
                continue
            try:
                after = (start - timedelta(days=1)).isoformat()
                before = (end + timedelta(days=1)).isoformat()
                r = subprocess.run(
                    ["git", "-C", str(repo_path), "log", "--oneline",
                     f"--after={after}", f"--before={before}"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                if r.stdout.strip():
                    git_sections.append(f"#### {repo}\n{r.stdout.strip()}")
            except (subprocess.TimeoutExpired, OSError):
                continue
        if git_sections:
            parts.append("### Git commits\n\n" + "\n\n".join(git_sections))

        # Shell command summary
        try:
            from ..sources.captures.atuin import iter_commands
            dt_start = datetime(start.year, start.month, start.day)
            dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)
            cmds = list(iter_commands(start=dt_start, end=dt_end))
            if cmds:
                from collections import Counter
                cmd_prefixes = Counter(
                    c.command.split()[0] for c in cmds if c.command
                )
                top = cmd_prefixes.most_common(15)
                top_str = ", ".join(f"{cmd} ({n}×)" for cmd, n in top)
                parts.append(f"### Shell activity\n{len(cmds)} commands. Top: {top_str}")
        except Exception:
            pass

        # Episodes overlapping
        out = _duckdb_query(db_path,
            f"SELECT label, start_date, end_date, trigger, dominant_mode, dominant_project "
            f"FROM trajectory_episode WHERE end_date >= '{start}' AND start_date <= '{end}'")
        if out:
            parts.append(f"### Episodes\n{out}")

    elif scale is NarrativeKind.month:
        # Per-week summary from warehouse
        out = _duckdb_query(db_path,
            f"SELECT iso_week, start_date, end_date, days, "
            f"round(active_seconds/3600.0,1) as active_h, "
            f"round(recovery_seconds/3600.0,1) as recovery_h, "
            f"commit_count, day_pattern "
            f"FROM trajectory_week "
            f"WHERE start_date >= '{start}' AND end_date <= '{end}' ORDER BY iso_week")
        if out:
            parts.append(f"### Per-week summary\n{out}")

        # Per-project time across the month
        out = _duckdb_query(db_path,
            f"SELECT project, round(SUM(duration_seconds)/3600.0,1) as hours, COUNT(*) as days_active "
            f"FROM trajectory_day_project WHERE date BETWEEN '{start}' AND '{end}' "
            f"AND duration_seconds > 300 GROUP BY project ORDER BY hours DESC LIMIT 15")
        if out:
            parts.append(f"### Project time (month total)\n{out}")

        # Git oneline per repo (medium density — commit messages, no diffstat)
        from .narrative import _GIT_REPOS
        git_sections: list[str] = []
        for repo in _GIT_REPOS:
            repo_path = Path(f"/realm/project/{repo}")
            if not repo_path.exists():
                continue
            try:
                after = (start - timedelta(days=1)).isoformat()
                before = (end + timedelta(days=1)).isoformat()
                r = subprocess.run(
                    ["git", "-C", str(repo_path), "log", "--oneline",
                     f"--after={after}", f"--before={before}"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                if r.stdout.strip():
                    git_sections.append(f"#### {repo} ({len(r.stdout.strip().splitlines())} commits)\n{r.stdout.strip()}")
            except (subprocess.TimeoutExpired, OSError):
                continue
        if git_sections:
            parts.append("### Git commits\n\n" + "\n\n".join(git_sections))

        # Per-day trajectory table (compact overview)
        out = _duckdb_query(db_path,
            f"SELECT date, round(active_seconds/3600.0,1) as active_h, "
            f"dominant_mode, dominant_project, commit_count "
            f"FROM trajectory_day WHERE date BETWEEN '{start}' AND '{end}' ORDER BY date")
        if out:
            parts.append(f"### Per-day overview\n{out}")

        # Episodes
        out = _duckdb_query(db_path,
            f"SELECT label, start_date, end_date, trigger, confidence, dominant_project "
            f"FROM trajectory_episode WHERE end_date >= '{start}' AND start_date <= '{end}' "
            f"ORDER BY start_date")
        if out:
            parts.append(f"### Episodes\n{out}")

    elif scale is NarrativeKind.quarter:
        # Per-month summary from warehouse
        out = _duckdb_query(db_path,
            f"SELECT month, start_date, end_date, total_days, active_days, "
            f"round(active_seconds/3600.0,1) as active_h, "
            f"round(recovery_seconds/3600.0,1) as recovery_h, "
            f"commit_count, dominant_mode, dominant_project "
            f"FROM trajectory_month "
            f"WHERE start_date >= '{start}' AND end_date <= '{end}' ORDER BY month")
        if out:
            parts.append(f"### Per-month summary\n{out}")

        # Per-week trajectory (compact — the month-level detail)
        out = _duckdb_query(db_path,
            f"SELECT iso_week, round(active_seconds/3600.0,1) as active_h, "
            f"commit_count, day_pattern, dominant_project "
            f"FROM trajectory_week "
            f"WHERE start_date >= '{start}' AND end_date <= '{end}' ORDER BY iso_week")
        if out:
            parts.append(f"### Per-week trajectory\n{out}")

        # Per-project time across the quarter
        out = _duckdb_query(db_path,
            f"SELECT project, round(SUM(duration_seconds)/3600.0,1) as hours, COUNT(*) as days_active "
            f"FROM trajectory_day_project WHERE date BETWEEN '{start}' AND '{end}' "
            f"AND duration_seconds > 300 GROUP BY project ORDER BY hours DESC LIMIT 15")
        if out:
            parts.append(f"### Project time (quarter total)\n{out}")

        # Git commit distribution per repo per month
        from .narrative import _GIT_REPOS
        git_lines = ["### Git per-repo per-month"]
        for repo in _GIT_REPOS:
            repo_path = Path(f"/realm/project/{repo}")
            if not repo_path.exists():
                continue
            try:
                after = (start - timedelta(days=1)).isoformat()
                before = (end + timedelta(days=1)).isoformat()
                r = subprocess.run(
                    ["git", "-C", str(repo_path), "log", "--format=%ad", "--date=format:%Y-%m",
                     f"--after={after}", f"--before={before}"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                dates = [d.strip() for d in r.stdout.strip().split("\n") if d.strip()]
                if dates:
                    from collections import Counter
                    month_counts = Counter(dates)
                    month_str = ", ".join(f"{m}: {c}" for m, c in sorted(month_counts.items()))
                    git_lines.append(f"  {repo}: {len(dates)} total — {month_str}")
            except (subprocess.TimeoutExpired, OSError):
                continue
        if len(git_lines) > 1:
            parts.append("\n".join(git_lines))

        # Episode summary
        out = _duckdb_query(db_path,
            f"SELECT label, start_date, end_date, trigger, confidence, dominant_project "
            f"FROM trajectory_episode WHERE end_date >= '{start}' AND start_date <= '{end}' "
            f"ORDER BY start_date")
        if out:
            parts.append(f"### Episodes\n{out}")

    return "\n\n".join(parts)


def _build_synthesis_prompt(
    scale: NarrativeKind,
    key: str,
    children: dict[str, str],
) -> str:
    """Build a synthesis prompt from child narrative texts + trajectory data.

    Children are included at full resolution.  The prior and next periods (if
    narratives exist) are included as abbreviated context for temporal continuity.
    Aggregate data at the parent scale is included to enrich synthesis.
    """
    cs = child_scale(scale)
    cs_label = cs.value if cs else "period"

    # Parent-level trajectory data
    trajectory_prompts = build_scale_prompts([key], scale=scale)
    trajectory_context = trajectory_prompts[0][0] if trajectory_prompts else ""

    # Aggregate enrichment (week/month/quarter-specific data)
    enrichment = _build_scale_enrichment(scale, key)

    # Full child texts
    child_blocks = []
    for ck in sorted(children.keys()):
        text = children[ck]
        child_blocks.append(f"### {ck}\n\n{text}")
    children_section = "\n\n---\n\n".join(child_blocks)

    # Temporal context: prior and next period narratives (abbreviated)
    temporal_context_parts = []

    prior = _prior_key(scale, key)
    if prior:
        prior_narratives = load_narratives(scale.value, [prior])
        if prior in prior_narratives:
            prior_text = prior_narratives[prior][:1500]
            temporal_context_parts.append(
                f"## Prior period ({prior}, abbreviated)\n\n{prior_text}..."
            )

    next_key = _next_key(scale, key)
    if next_key:
        next_narratives = load_narratives(scale.value, [next_key])
        if next_key in next_narratives:
            next_text = next_narratives[next_key][:1500]
            temporal_context_parts.append(
                f"## Next period ({next_key}, abbreviated)\n\n{next_text}..."
            )

    temporal_section = ""
    if temporal_context_parts:
        temporal_section = "\n\n" + "\n\n".join(temporal_context_parts)

    parts = [
        trajectory_context,
    ]

    if enrichment:
        parts.append(f"\n\n## Aggregate data\n\n{enrichment}")

    parts.extend([
        f"\n\n## Constituent {cs_label} narratives\n\n{children_section}",
        temporal_section,
        f"\n\n---\n\nSynthesize a {scale.value}-level retrospective from the "
        f"above. Identify cross-{cs_label} arcs, phase transitions, and "
        f"patterns that only emerge at this scale.",
    ])

    result = "".join(parts)

    # Log prompt size and sections included
    estimated_tokens = len(result) // 4
    sections_included = ["trajectory"]
    if enrichment:
        sections_included.append("enrichment")
    sections_included.append("children")
    if temporal_section:
        sections_included.append("temporal_context")

    _progress(
        "synthesis_prompt[%s %s]: ~%d tokens, sections: %s",
        scale.value, key, estimated_tokens, ", ".join(sections_included)
    )

    return result


def _build_top_down_prompt(
    parent_text: str,
    parent_scale: NarrativeKind,
    child_scale_kind: NarrativeKind,
    child_key: str,
    child_text: str,
    parent_key: str,
) -> str:
    """Build a top-down enrichment prompt."""
    return TOP_DOWN_SYSTEM_PROMPT.format(
        child_scale=child_scale_kind.value,
        parent_scale=parent_scale.value,
        parent_key=parent_key,
        parent_text=parent_text,
        child_key=child_key,
        child_text=child_text,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesisConfig:
    """Controls the hierarchical synthesis process."""

    passes: int = 1
    force_regenerate: bool = False


# ---------------------------------------------------------------------------
# Synthesis runner
# ---------------------------------------------------------------------------


async def synthesize_narrative(
    scale: NarrativeKind,
    key: str,
    *,
    config: SynthesisConfig = SynthesisConfig(),
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
) -> Narrative:
    """Hierarchical narrative synthesis.

    Pass 1 (bottom-up): ensure children exist, load them, generate parent.
    Pass 2 (top-down): re-generate children with parent context.
    Pass 3 (refinement): re-generate parent from enriched children.
    """
    cs = child_scale(scale)

    # --- Pass 1: Bottom-up ---
    if cs is not None:
        keys = child_keys(scale, key)
        _progress(
            f"synthesis[{scale.value} {key}] pass 1: ensuring {len(keys)} {cs.value} narratives"
        )

        # Recursively ensure children exist (skip those without data)
        for i, ck in enumerate(keys, 1):
            existing = load_narratives(cs.value, [ck])
            if existing.get(ck) and not config.force_regenerate:
                _progress(
                    f"synthesis[{scale.value} {key}] pass 1: {cs.value} {ck} ({i}/{len(keys)}) cached"
                )
                continue
            _progress(
                f"synthesis[{scale.value} {key}] pass 1: generating {cs.value} {ck} ({i}/{len(keys)})"
            )
            try:
                await synthesize_narrative(
                    cs, ck, config=config, backend=backend, model=model,
                )
            except ValueError:
                _progress(
                    "synthesis[%s %s]: no data for child %s %s, skipping",
                    scale.value, key, cs.value, ck,
                )

        # Load all child narratives
        children = load_narratives(cs.value, keys)
        missing = [k for k in keys if k not in children]
        if missing:
            _progress(
                "synthesis[%s %s]: %d children missing after generation: %s",
                scale.value, key, len(missing), missing,
            )

        # Build synthesis prompt
        prompt = _build_synthesis_prompt(scale, key, children)
        system_prompt = SYNTHESIS_SYSTEM_PROMPT
    else:
        # Base case: day-level analytical generation.
        # Try trajectory data first; fall back to git-only prompt if no
        # warehouse data exists for this period.
        prompts = build_scale_prompts([key], scale=scale)
        if prompts:
            prompt = prompts[0][0]
        else:
            # No trajectory data — build a git-only prompt for the agent
            git_prompt = _build_git_only_prompt(scale, key)
            if git_prompt is None:
                raise ValueError(f"No data (trajectory or git) for {scale.value} {key}")
            prompt = git_prompt
        system_prompt = None  # use default (analytical or narrative)

    _progress("synthesis[%s %s] pass 1: generating", scale.value, key)
    parent_narrative = await generate_narrative(
        prompt, scale, key, backend=backend, model=model,
    )

    if config.passes < 2 or cs is None:
        return parent_narrative

    # --- Pass 2: Top-down enrichment ---
    _progress(
        "synthesis[%s %s] pass 2: top-down enrichment of %d children",
        scale.value, key, len(child_keys(scale, key)),
    )
    for ck in child_keys(scale, key):
        existing_child = load_narratives(cs.value, [ck]).get(ck, "")
        if not existing_child:
            continue
        td_prompt = _build_top_down_prompt(
            parent_narrative.text, scale, cs, ck, existing_child, key,
        )
        await generate_narrative(td_prompt, cs, ck, backend=backend, model=model)

    if config.passes < 3:
        return parent_narrative

    # --- Pass 3: Refinement ---
    _progress("synthesis[%s %s] pass 3: refinement", scale.value, key)
    refreshed_children = load_narratives(cs.value, child_keys(scale, key))
    refined_prompt = _build_synthesis_prompt(scale, key, refreshed_children)
    return await generate_narrative(
        refined_prompt, scale, key, backend=backend, model=model,
    )


# ---------------------------------------------------------------------------
# Enhancement passes
# ---------------------------------------------------------------------------


class EnhancementPass(str, Enum):
    """Named enhancement passes that can be composed into workflows."""

    fact_checker = "fact-checker"
    dependency_tracer = "dependency-tracer"
    energy_analyst = "energy-analyst"
    delegation_auditor = "delegation-auditor"
    continuity_editor = "continuity-editor"
    anomaly_detector = "anomaly-detector"
    dashboard_builder = "dashboard-builder"
    question_generator = "question-generator"


_ENHANCEMENT_PROMPTS: dict[EnhancementPass, str] = {
    EnhancementPass.fact_checker: """\
You are an adversarial fact-checker for personal retrospective narratives.

You receive a retrospective narrative and have tools to verify its claims
against raw data. Your job:

1. Identify every quantitative claim (commit counts, hours, dates, ratios,
   speedup factors, "X commits in Y hours", etc.)
2. Verify each against the actual data:
   - Git: `git -C /realm/project/<repo> log --oneline --after=X --before=Y | wc -l`
   - DuckDB: `duckdb artefacts/lynchpin/warehouse.duckdb -c "SELECT ..."`
   - Direct file reads for specific artifacts
3. Identify causal claims ("X caused Y", "after X, Y happened") and check
   temporal ordering + plausibility
4. Flag: CONFIRMED, INFLATED, DEFLATED, UNVERIFIABLE, WRONG

Output a structured errata report:
- Each finding: claim → source → verification → verdict
- Summary: % confirmed, list of corrections needed
- Revised text for any section with material errors

Be thorough but fair — approximations within 10% are fine. Focus on claims
that would materially change the narrative's conclusions if wrong.
""",
    EnhancementPass.dependency_tracer: """\
You are a cross-project dependency tracer for a multi-repository ecosystem.

You receive retrospective narratives covering multiple projects and have tools
to investigate causal chains between repositories. Your job:

1. Identify events in one repo that triggered or enabled work in another:
   - API changes that forced consumers to adapt
   - Infrastructure teardowns (decommissions) rippling across repos
   - Patterns discovered in one repo and applied to another
   - Blocking dependencies (X couldn't proceed until Y landed)

2. Verify temporal ordering via git:
   - `git -C /realm/project/<repo> log --oneline --after=X --before=Y`
   - Check if commit A in repo1 preceded commit B in repo2

3. Trace specific dependency chains:
   - Config/schema changes that propagated
   - Shared technique adoption (e.g., same optimization in different repos)
   - Coordinated multi-repo operations (same-day commits across repos)

Known repos: sinex (Rust), polylogue (Python), sinnix (Nix), sinity-lynchpin (Python)

Output:
- Dependency graph as a list of edges: (source_repo, event, target_repo, effect, confidence)
- Notable chains: multi-hop sequences where A→B→C
- Isolated work: projects/periods with no cross-repo dependencies
- Revised narrative section highlighting the cross-project causality
""",
    EnhancementPass.energy_analyst: """\
You are a personal energy and sustainability analyst.

You receive retrospective narratives with activity data and have tools to
investigate physiological patterns. Your job:

1. Analyze the active-to-recovery ratio at every scale:
   - Per-day: sustainable threshold ~12-14h active, >16h is a red flag
   - Per-week: look for compensatory recovery after intense days
   - Across weeks: sprint-recovery oscillation period and amplitude

2. Check time-of-day patterns via DuckDB:
   - `duckdb artefacts/lynchpin/warehouse.duckdb -c "SELECT ..."`
   - Look at trajectory_signal for work start/end times
   - Identify night work, schedule regularity, weekend patterns

3. Correlate with sleep data if available:
   - `sqlite3 artefacts/lynchpin/cache/samsung_sleep_sessions.sqlite "SELECT ..."`

4. Assess sustainability:
   - Is the current pace maintainable for 4+ weeks?
   - Where are the crash risks?
   - What does the recovery debt look like?

Output:
- Energy profile: daily rhythm, weekly cycle, monthly arc
- Risk assessment: specific days/periods of concern
- Recommendations: concrete schedule adjustments
- Revised narrative section with energy/sustainability analysis integrated
""",
    EnhancementPass.delegation_auditor: """\
You are an AI delegation auditor analyzing human-AI collaboration patterns.

You receive retrospective narratives and have tools to classify work as
human-driven vs AI-delegated. Your job:

1. For each day/period, compute the delegation ratio:
   - Commits per tracked hour (>10/hour strongly suggests AI batch work)
   - Co-authorship tags in git: `git -C /realm/project/<repo> log --format="%b" --after=X --before=Y | grep -c "Co-Authored-By"`
   - Commit message patterns: systematic prefixes, batch-like repetition

2. Classify each work session:
   - DEEP_WORK: high time, few commits, complex changes
   - PAIR_PROGRAMMING: proportional time/commits, interactive style
   - BATCH_DELEGATION: low time, many commits, systematic patterns
   - FLEET_ORCHESTRATION: minimal time, high-volume AI execution

3. Track the collaboration model evolution over time:
   - When did delegation patterns first appear?
   - How did the human role shift (coder → architect → director)?
   - Which model variants were used for which tasks?

4. Assess efficiency:
   - Human hours per shipped outcome
   - AI token cost per shipped outcome (estimate from credit formulas)
   - Quality correlation: do AI-heavy days produce more fixes later?

Output:
- Per-day delegation classification table
- Collaboration model evolution timeline
- Efficiency metrics
- Revised narrative section with delegation analysis integrated
""",
    EnhancementPass.continuity_editor: """\
You are a narrative continuity editor ensuring cross-scale coherence.

You receive the full hierarchy of narratives (days, weeks, month) and your
job is to ensure they read as a coherent document, not isolated summaries.

1. Terminology consistency:
   - If the month narrative names an arc ("The Testing Revolution"), do
     week/day narratives use the same term or a conflicting one?
   - Standardize terminology across scales

2. Cross-reference integrity:
   - If the month says "Mar 11 had 98 polylogue commits", does the Mar 11
     day narrative agree?
   - If a week narrative identifies a pattern, does the month narrative
     reference it?

3. Narrative flow:
   - Does reading day→week→month tell a coherent escalating story?
   - Are there contradictions between scales?
   - Are insights at one scale repeated verbatim at another (bad) vs
     built upon (good)?

4. Information hierarchy:
   - Day narratives should have the most detail
   - Week narratives should synthesize, not restate
   - Month narratives should identify arcs, not list facts

Output:
- Consistency issues found (with specific quotes)
- Recommended terminology standardization
- Revised month narrative with continuity fixes applied
- A brief "reading guide" suggesting which narratives to read for what purpose
""",
    EnhancementPass.anomaly_detector: """\
You are a temporal anomaly detector for personal retrospective narratives.

You receive retrospective narratives covering a time period and have tools
to investigate patterns and their violations. Your job:

1. Identify the dominant rhythms and patterns in the data:
   - Sprint-recovery cycles (period, amplitude)
   - Project rotation patterns
   - Mode distribution norms
   - Commit velocity baselines

2. Find periods that BREAK these patterns:
   - Days that don't fit the established rhythm
   - Weeks where the project mix shifts unexpectedly
   - Anomalous commit density (too high or too low)
   - Mode switches that deviate from the cycle
   - Gaps or silences that interrupt active streaks

3. For each anomaly, investigate WHY:
   - Check git logs for what was happening
   - Check if external events (deploys, incidents, infrastructure) explain it
   - Check if the anomaly started a new pattern or was a one-off

4. Classify each anomaly:
   - PHASE_TRANSITION: marks the boundary between two distinct patterns
   - EXTERNAL_DISRUPTION: caused by something outside normal workflow
   - CREATIVE_BURST: extraordinary productivity spike
   - CRASH: energy/sustainability failure
   - EXPLORATION: departure from routine to try something new
   - MYSTERY: genuinely unexplained

Output:
- Pattern baselines identified (with evidence)
- Each anomaly: date/period, expected pattern, actual observation, classification, explanation
- Which anomalies were precursors to lasting changes vs one-offs
- Revised narrative section integrating anomaly analysis
""",
    EnhancementPass.dashboard_builder: """\
You are a quantitative dashboard builder for personal retrospective narratives.

You receive retrospective narratives and have tools to extract and verify
every quantitative claim. Your job is to produce a structured, machine-readable
artifact — NOT prose, but a JSON dashboard.

1. Extract every number mentioned in any narrative at any scale:
   - Commit counts (per repo, per day, per period)
   - Hours (active, recovery, per-project, per-mode)
   - Ratios (commits/hour, active/recovery, AI co-authorship %)
   - Counts (chat sessions, debugging events, test counts)
   - Performance metrics (speedups, response times)
   - Costs (credit estimates, API equivalents)

2. Cross-reference and deduplicate:
   - If the day narrative says "36 commits" and the week says "267 total",
     verify the sum
   - Resolve conflicts between scales (prefer most granular source)

3. Verify against raw data where possible:
   - `git -C /realm/project/<repo> log --oneline --after=X --before=Y | wc -l`
   - `duckdb artefacts/lynchpin/warehouse.duckdb -c "SELECT ..."`

4. Output ONLY a JSON object with this structure:
```json
{
  "period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "scale": "..."},
  "totals": {"active_hours": N, "recovery_hours": N, "commits": N, ...},
  "per_repo": {"sinex": {"commits": N, "hours": N, "ai_pct": N}, ...},
  "per_week": [{"key": "W09", "commits": N, "hours": N}, ...],
  "ratios": {"commits_per_hour": N, "active_recovery": N, ...},
  "performance": [{"what": "...", "before": N, "after": N, "factor": N}],
  "ai_collaboration": {"total_ai_commits": N, "by_model": {...}},
  "verified": N,
  "unverified": N,
  "corrections": [{"claim": "...", "was": N, "actual": N}]
}
```

Be exhaustive. Every number in every narrative should appear in this dashboard.
""",
    EnhancementPass.question_generator: """\
You are a forward-looking question generator for personal retrospective analysis.

You receive the full hierarchy of narratives (days, weeks, month/quarter) and
your job is to identify the most interesting, actionable, unanswered questions
that should guide the NEXT period's analysis and work.

Categories of questions:

1. **Sustainability**: Is the current pace maintainable? What early warning
   signs should be monitored? When is the next crash risk?

2. **Strategic**: Are the right projects getting attention? What's being
   neglected? Is the infrastructure investment converting to user value?

3. **Process**: Is the AI delegation model improving or plateauing? What
   work categories still resist delegation? What would the next delegation
   efficiency breakthrough look like?

4. **Technical**: What architectural decisions from this period will most
   constrain or enable future work? What technical debt was created?

5. **Meta**: Is the retrospective system itself capturing the right things?
   What signals are missing? What would make the next retrospective better?

6. **Personal**: What patterns in energy, focus, and interest suggest about
   alignment between what's being built and what matters?

Output:
- 10-15 ranked questions, each with:
  - The question itself (specific, answerable)
  - Why it matters (what decision it would inform)
  - What data would answer it (specific queries, observations, experiments)
  - Urgency: IMMEDIATE (answer before next sprint), PERIODIC (check monthly),
    STRATEGIC (revisit quarterly)
- A brief "state of understanding" summary: what do we know well, what are
  we guessing about, what are we blind to?
""",
}


async def run_enhancement_pass(
    pass_kind: EnhancementPass,
    scale: NarrativeKind,
    key: str,
    *,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
) -> Narrative:
    """Run a single enhancement pass over an existing narrative.

    Loads the current narrative + its children, sends them to a specialized
    agent with the enhancement system prompt, and logs the result.
    """
    from ..core.claude_sdk import run_claude_sdk

    system_prompt = _ENHANCEMENT_PROMPTS[pass_kind]
    cs = child_scale(scale)

    # Load the target narrative
    target = load_narratives(scale.value, [key])
    if key not in target:
        raise ValueError(
            f"No existing {scale.value} narrative for {key}. "
            f"Run synthesis first."
        )

    # Build context: target narrative + children (if any)
    parts = [f"# {scale.value.title()} narrative for {key}\n\n{target[key]}"]

    if cs is not None:
        children = load_narratives(cs.value, child_keys(scale, key))
        if children:
            child_section = "\n\n---\n\n".join(
                f"## {ck}\n\n{text}" for ck, text in sorted(children.items())
            )
            parts.append(
                f"\n\n# Constituent {cs.value} narratives\n\n{child_section}"
            )

    prompt = "\n".join(parts)

    # Enhancement passes get tools for verification
    tools = ["Read", "Bash", "Glob", "Grep"]
    if pass_kind in (
        EnhancementPass.continuity_editor,
        EnhancementPass.question_generator,
    ):
        tools = []  # pure text-to-text, no tool calls needed

    result = await run_claude_sdk(
        prompt,
        system_prompt=system_prompt,
        model=model,
        allowed_tools=tools,
    )

    # Log as a narrative with kind = "enhancement:{pass_kind.value}"
    enhanced_kind = f"enhancement:{pass_kind.value}"
    narrative = Narrative(
        kind=enhanced_kind,
        key=key,
        text=result.text,
        generated_at=_now_iso(),
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        backend="claude-agent-sdk",
    )
    from .narrative import _log_narrative

    _log_narrative(narrative)
    _progress(
        "enhancement[%s %s %s]: %d in + %d out tokens",
        pass_kind.value,
        scale.value,
        key,
        result.input_tokens,
        result.output_tokens,
    )
    return narrative


def _now_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class Workflow(str, Enum):
    """Named synthesis workflows composing passes into pipelines."""

    standard = "standard"       # bottom-up only (pass 1)
    bidirectional = "bidirectional"  # bottom-up + top-down + refine (passes 1-3)
    analytical = "analytical"   # bidirectional + fact-checker + dependency-tracer
    comprehensive = "comprehensive"  # all passes in sequence
    quality = "quality"         # fact-checker + continuity-editor (post-hoc)
    meta = "meta"               # delegation-auditor + energy-analyst (self-analysis)
    deep = "deep"               # comprehensive + anomaly + dashboard + questions


WORKFLOW_DEFINITIONS: dict[Workflow, dict[str, Any]] = {
    Workflow.standard: {
        "passes": 1,
        "enhancements": [],
        "description": "Bottom-up synthesis only. Fast, cheap.",
    },
    Workflow.bidirectional: {
        "passes": 3,
        "enhancements": [],
        "description": "Bottom-up → top-down → refinement. Better cross-scale coherence.",
    },
    Workflow.analytical: {
        "passes": 3,
        "enhancements": [
            EnhancementPass.fact_checker,
            EnhancementPass.dependency_tracer,
        ],
        "description": "Bidirectional + fact verification + cross-repo tracing.",
    },
    Workflow.comprehensive: {
        "passes": 3,
        "enhancements": [
            EnhancementPass.fact_checker,
            EnhancementPass.dependency_tracer,
            EnhancementPass.energy_analyst,
            EnhancementPass.delegation_auditor,
            EnhancementPass.continuity_editor,
        ],
        "description": "Bidirectional + all 5 core enhancement passes.",
    },
    Workflow.quality: {
        "passes": 1,
        "enhancements": [
            EnhancementPass.fact_checker,
            EnhancementPass.continuity_editor,
        ],
        "description": "Post-hoc quality pass on existing narratives.",
    },
    Workflow.meta: {
        "passes": 1,
        "enhancements": [
            EnhancementPass.delegation_auditor,
            EnhancementPass.energy_analyst,
        ],
        "description": "Self-analysis: delegation patterns + sustainability.",
    },
    Workflow.deep: {
        "passes": 3,
        "enhancements": [
            EnhancementPass.fact_checker,
            EnhancementPass.dependency_tracer,
            EnhancementPass.energy_analyst,
            EnhancementPass.delegation_auditor,
            EnhancementPass.anomaly_detector,
            EnhancementPass.continuity_editor,
            EnhancementPass.dashboard_builder,
            EnhancementPass.question_generator,
        ],
        "description": "All passes including anomaly detection, dashboard, and forward questions.",
    },
}


async def run_workflow(
    workflow: Workflow,
    scale: NarrativeKind,
    key: str,
    *,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Narrative]:
    """Execute a named workflow, returning all produced narratives.

    Returns a dict keyed by pass name: "synthesis", "fact-checker", etc.
    """
    defn = WORKFLOW_DEFINITIONS[workflow]
    results: dict[str, Narrative] = {}

    # Phase 1: Core synthesis (skip if already exists and not forcing)
    existing = load_narratives(scale.value, [key])
    if existing.get(key) and not force:
        _progress(
            "workflow[%s] synthesis cached for %s %s, skipping to enhancements",
            workflow.value, scale.value, key,
        )
        results["synthesis"] = Narrative(
            kind=scale.value, key=key, text=existing[key],
            generated_at=_now_iso(), model="cached", input_tokens=0,
            output_tokens=0, cost_usd=0.0, backend="cached",
        )
    else:
        _progress("workflow[%s] starting synthesis phase (%d passes)", workflow.value, defn["passes"])
        config = SynthesisConfig(passes=defn["passes"], force_regenerate=force)
        synthesis_result = await synthesize_narrative(
            scale, key, config=config, backend=backend, model=model,
        )
        results["synthesis"] = synthesis_result

    # Phase 2: Enhancement passes (skip already-completed ones)
    enhancements = defn["enhancements"]
    for i, pass_kind in enumerate(enhancements, 1):
        enhanced_kind = f"enhancement:{pass_kind.value}"
        existing_enh = load_narratives(enhanced_kind, [key])
        if existing_enh.get(key) and not force:
            _progress(
                "workflow[%s] enhancement %d/%d: %s (cached, skipping)",
                workflow.value, i, len(enhancements), pass_kind.value,
            )
            results[pass_kind.value] = Narrative(
                kind=enhanced_kind, key=key, text=existing_enh[key],
                generated_at=_now_iso(), model="cached", input_tokens=0,
                output_tokens=0, cost_usd=0.0, backend="cached",
            )
            continue
        _progress(
            "workflow[%s] enhancement %d/%d: %s",
            workflow.value, i, len(enhancements), pass_kind.value,
        )
        enhancement_result = await run_enhancement_pass(
            pass_kind, scale, key, backend=backend, model=model,
        )
        results[pass_kind.value] = enhancement_result

    _progress(
        "workflow[%s] complete: %d results produced",
        workflow.value,
        len(results),
    )
    return results
