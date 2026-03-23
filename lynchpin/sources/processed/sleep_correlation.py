"""Sleep-productivity correlations: link sleep quality to next-day output."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Optional


@dataclass(frozen=True)
class SleepProductivityCorrelation:
    sleep_date: date
    sleep_hours: float
    sleep_score: float | None
    sleep_quality: str
    next_day_active_hours: float
    next_day_commits: int
    next_day_dominant_mode: str | None
    next_day_deep_work_minutes: float
    productivity_vs_baseline: float


def iter_sleep_correlations(
    *, start: date, end: date
) -> Iterator[SleepProductivityCorrelation]:
    from ...core.config import get_config
    from ...metrics.health import sleep_summary
    from ...sources.exports.health import iter_samsung_sleep_sessions

    # Load trajectory days from warehouse
    day_data: dict[date, dict] = {}
    try:
        r = subprocess.run(
            [
                "duckdb",
                "artefacts/lynchpin/warehouse.duckdb",
                "-c",
                f"SELECT date, active_seconds/3600.0, commit_count, dominant_mode FROM trajectory_day WHERE date BETWEEN '{start}' AND '{end + timedelta(days=2)}'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().split("\n"):
            parts = line.strip().split("│")
            if len(parts) >= 5:
                try:
                    d = date.fromisoformat(parts[1].strip())
                    day_data[d] = {
                        "active_hours": float(parts[2].strip()),
                        "commits": int(float(parts[3].strip())),
                        "mode": parts[4].strip() or None,
                    }
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass

    # Rolling baseline
    all_hours = [v["active_hours"] for v in day_data.values()]
    baseline_avg = (
        sum(all_hours) / max(len(all_hours), 1) if all_hours else 8.0
    )

    cfg = get_config()
    export_path = cfg.exports_root / "health" / "raw" / "samsung-health"
    for session in iter_samsung_sleep_sessions(export_path):
        st = getattr(session, "start_time", None) or getattr(
            session, "start_local", None
        )
        if st is None:
            continue
        sleep_date = (
            st.date()
            if hasattr(st, "date")
            else date.fromisoformat(str(st)[:10])
        )
        if sleep_date < start or sleep_date > end:
            continue

        dur_min = getattr(session, "duration_minutes", 0) or 0
        sm = sleep_summary(session)
        next_day = sleep_date + timedelta(days=1)
        nd = day_data.get(next_day, {})

        # Deep work for next day
        dw_minutes = 0.0
        try:
            from datetime import datetime

            from .deep_work import iter_deep_work

            blocks = list(
                iter_deep_work(
                    start=datetime(
                        next_day.year, next_day.month, next_day.day
                    ),
                    end=datetime(
                        next_day.year, next_day.month, next_day.day
                    )
                    + timedelta(days=1),
                )
            )
            dw_minutes = sum(b.duration_minutes for b in blocks)
        except Exception:
            pass

        next_active = nd.get("active_hours", 0.0)
        yield SleepProductivityCorrelation(
            sleep_date=sleep_date,
            sleep_hours=dur_min / 60.0,
            sleep_score=getattr(session, "sleep_score", None),
            sleep_quality=sm.quality_label if sm else "unknown",
            next_day_active_hours=next_active,
            next_day_commits=nd.get("commits", 0),
            next_day_dominant_mode=nd.get("mode"),
            next_day_deep_work_minutes=dw_minutes,
            productivity_vs_baseline=next_active / max(baseline_avg, 0.1),
        )
