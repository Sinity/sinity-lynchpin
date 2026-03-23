"""Pre-compute ActivityWatch collapsed signals for the fast-path artefact cache.

Partitions output by calendar month so that a 7-day trajectory query reads at most
2 monthly files (~50k signals) instead of scanning all 300k+ collapsed signals.

Run periodically (e.g. via `just ingest-aw`) to keep artefacts fresh.
Incremental mode (--months N) rebuilds only the last N months.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import typer

from ..sources.captures import activitywatch
from ..trajectory.signal import _signal_id, _as_local, _text, TrajectorySignal
from ..trajectory.signal_sources import _collapse_window_like

app = typer.Typer(help="ActivityWatch signal pre-computation")

_AW_WINDOW_ARTEFACT_DIR = Path("artefacts/ingest/aw_window")
_AW_WEB_ARTEFACT_DIR = Path("artefacts/ingest/aw_web")
_AW_AFk_ARTEFACT_DIR = Path("artefacts/ingest/aw_afk")
_INGEST_START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _iter_months(start: datetime, end: datetime) -> Iterator[tuple[int, int]]:
    """Yield (year, month) pairs for every calendar month covering [start, end]."""
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        yield cur.year, cur.month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def _since_months_ago(n: int, now: datetime) -> datetime:
    y, m = now.year, now.month
    for _ in range(n - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return datetime(y, m, 1, tzinfo=timezone.utc)


def _iter_afk_signals(
    month_start: datetime,
    month_end: datetime,
) -> Iterator[TrajectorySignal]:
    """Convert AFK events to TrajectorySignals (filter status == 'afk' only)."""
    for event in activitywatch.afk_events(start=month_start, end=month_end):
        status = _text((event.data or {}).get("status"))
        if status != "afk":
            continue
        signal_start = _as_local(event.start)
        signal_end = max(_as_local(event.end), signal_start)
        yield TrajectorySignal(
            signal_id=_signal_id("activitywatch.afk", signal_start, signal_end, status),
            source="activitywatch.afk",
            kind="afk",
            start=signal_start,
            end=signal_end,
            mode_hint="recovery",
            detail=status,
            evidence={"bucket": event.bucket, "status": status},
        )


@app.command()
def signals(
    output_window: Path = typer.Option(
        _AW_WINDOW_ARTEFACT_DIR, "--output-window", help="Window signals output dir"
    ),
    output_web: Path = typer.Option(
        _AW_WEB_ARTEFACT_DIR, "--output-web", help="Web signals output dir"
    ),
    output_afk: Path = typer.Option(
        _AW_AFk_ARTEFACT_DIR, "--output-afk", help="AFK signals output dir"
    ),
    months: int = typer.Option(
        0, "--months", help="Only rebuild last N months (0 = all since ingest start)"
    ),
) -> None:
    """Pre-compute collapsed AW signals partitioned by calendar month."""
    now = datetime.now(timezone.utc)
    since = _since_months_ago(months, now) if months > 0 else _INGEST_START

    output_window.mkdir(parents=True, exist_ok=True)
    output_web.mkdir(parents=True, exist_ok=True)
    output_afk.mkdir(parents=True, exist_ok=True)

    total_window = 0
    total_web = 0
    total_afk = 0

    for year, month in _iter_months(since, now):
        _, last_day = monthrange(year, month)
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        month_end = min(
            datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
            + timedelta(microseconds=1),
            now,
        )
        month_key = f"{year:04d}-{month:02d}"

        # Window signals
        w_events = list(activitywatch.window_events(start=month_start, end=month_end))
        w_path = output_window / f"{month_key}.jsonl"
        w_count = 0
        with w_path.open("w", encoding="utf-8") as fh:
            for sig in _collapse_window_like(
                source="activitywatch.window",
                kind="window",
                events=w_events,
                app_key="app",
                title_key="title",
                url_key=None,
            ):
                fh.write(json.dumps(sig.to_dict(), ensure_ascii=False) + "\n")
                w_count += 1
        total_window += w_count

        # Web signals
        web_events_list = list(activitywatch.web_events(start=month_start, end=month_end))
        web_path = output_web / f"{month_key}.jsonl"
        web_count = 0
        with web_path.open("w", encoding="utf-8") as fh:
            for sig in _collapse_window_like(
                source="activitywatch.web",
                kind="web",
                events=web_events_list,
                app_key="browser",
                title_key="title",
                url_key="url",
            ):
                fh.write(json.dumps(sig.to_dict(), ensure_ascii=False) + "\n")
                web_count += 1
        total_web += web_count

        # AFK signals
        afk_path = output_afk / f"{month_key}.jsonl"
        afk_count = 0
        with afk_path.open("w", encoding="utf-8") as fh:
            for sig in _iter_afk_signals(month_start, month_end):
                fh.write(json.dumps(sig.to_dict(), ensure_ascii=False) + "\n")
                afk_count += 1
        total_afk += afk_count

        typer.secho(
            f"  {month_key}: {w_count} window, {web_count} web, {afk_count} afk",
            fg=typer.colors.CYAN,
        )

    typer.secho(
        f"✓ {total_window} window signals → {output_window}/", fg=typer.colors.GREEN
    )
    typer.secho(
        f"✓ {total_web} web signals → {output_web}/", fg=typer.colors.GREEN
    )
    typer.secho(
        f"✓ {total_afk} afk signals → {output_afk}/", fg=typer.colors.GREEN
    )


if __name__ == "__main__":
    app()
