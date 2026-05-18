"""Gap-code regression detector for machine telemetry.

Surfaces situations where a `gap_codes` value (recorded by
`machine-telemetry` when it cannot fulfill some aspect of a sample —
missing NIC, NVML failure, fan hwmon unavailable, etc.) appears in an
unusually large share of recent rows. The motivating failure mode is
the 2026-04-11→2026-05-15 silent regression where
``network.interface_missing`` appeared on every ``network_sample`` row
for 34 days because the hardcoded NIC name had drifted; nobody noticed
because nothing was checking aggregate gap-code share.

The analyzer is a pure read over substrate. It runs in the daily DAG,
writes ``machine_gap_summary.json``, and exposes an MCP tool that
reads the same artifact (or recomputes on demand). A separate sentinel
handle (not in this module) can poll the artifact and notify when any
code crosses a configurable share threshold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.core.io import save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path


DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_REGRESSION_PCT = 5.0
# Codes whose presence is structural/expected (e.g. backfilled rows from a
# retired collector that pre-dates newer columns) and would otherwise dominate
# the regression list every run. They are still counted in ``counts`` so the
# share remains visible; they are excluded only from ``regressions``.
DEFAULT_LEGACY_PREFIXES: tuple[str, ...] = ("legacy.",)
SOURCES: tuple[tuple[str, str], ...] = (
    ("machine_metric_sample", "observed_at"),
    ("machine_network_sample", "observed_at"),
)


@dataclass(frozen=True)
class GapCodeCount:
    table: str
    code: str
    rows_with_code: int
    rows_in_window: int
    share_pct: float


@dataclass(frozen=True)
class GapCodeRegression:
    table: str
    code: str
    share_pct: float
    threshold_pct: float
    rows_with_code: int
    rows_in_window: int
    severity: str


@dataclass(frozen=True)
class GapSummaryAnalysis:
    generated_for: dict[str, Any]
    counts: list[GapCodeCount]
    regressions: list[GapCodeRegression]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_gap_summary(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    regression_pct: float = DEFAULT_REGRESSION_PCT,
    legacy_prefixes: tuple[str, ...] = DEFAULT_LEGACY_PREFIXES,
    path: Path | None = None,
    now: datetime | None = None,
) -> GapSummaryAnalysis:
    """Compute per-(table, gap_code) share of rows over the last N days.

    Any code whose share exceeds ``regression_pct`` is also returned in
    ``regressions``, except codes whose name starts with one of
    ``legacy_prefixes`` (those remain in ``counts`` but never flag, since
    they reflect backfilled rows from a retired collector and would
    drown the regression list otherwise). Both lists are sorted by share
    descending so callers can take the top-N without re-sorting.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if regression_pct < 0:
        raise ValueError("regression_pct must be non-negative")

    end_dt = now or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=lookback_days)

    counts: list[GapCodeCount] = []
    with connect(path or substrate_path(), read_only=True) as conn:
        for table, ts_column in SOURCES:
            rows_in_window = _count_rows_in_window(conn, table, ts_column, start_dt, end_dt)
            if rows_in_window == 0:
                continue
            per_code = _count_per_code(conn, table, ts_column, start_dt, end_dt)
            for code, rows_with_code in per_code:
                share = (rows_with_code / rows_in_window) * 100.0
                counts.append(
                    GapCodeCount(
                        table=table,
                        code=code,
                        rows_with_code=rows_with_code,
                        rows_in_window=rows_in_window,
                        share_pct=round(share, 3),
                    )
                )

    counts.sort(key=lambda c: (-c.share_pct, c.table, c.code))
    regressions = [
        GapCodeRegression(
            table=c.table,
            code=c.code,
            share_pct=c.share_pct,
            threshold_pct=regression_pct,
            rows_with_code=c.rows_with_code,
            rows_in_window=c.rows_in_window,
            severity="critical" if c.share_pct >= 50.0 else "warning",
        )
        for c in counts
        if c.share_pct >= regression_pct
        and not any(c.code.startswith(prefix) for prefix in legacy_prefixes)
    ]
    return GapSummaryAnalysis(
        generated_for={
            "window_start": start_dt.isoformat(),
            "window_end": end_dt.isoformat(),
            "lookback_days": lookback_days,
            "regression_pct": regression_pct,
            "legacy_prefixes": list(legacy_prefixes),
            "sources": [table for table, _ in SOURCES],
        },
        counts=counts,
        regressions=regressions,
    )


def write_gap_summary_analysis(
    out: Path,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    regression_pct: float = DEFAULT_REGRESSION_PCT,
    legacy_prefixes: tuple[str, ...] = DEFAULT_LEGACY_PREFIXES,
    path: Path | None = None,
    now: datetime | None = None,
) -> GapSummaryAnalysis:
    analysis = analyze_gap_summary(
        lookback_days=lookback_days,
        regression_pct=regression_pct,
        legacy_prefixes=legacy_prefixes,
        path=path,
        now=now,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": (now or datetime.now(timezone.utc)).isoformat(),
        **analysis.to_dict(),
    }
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _count_rows_in_window(
    conn: Any,
    table: str,
    ts_column: str,
    start_dt: datetime,
    end_dt: datetime,
) -> int:
    rows_sql = latest_machine_rows(table)
    result = conn.execute(
        f"""
        SELECT count(*) FROM ({rows_sql})
        WHERE {ts_column} >= ? AND {ts_column} < ?
        """,
        [start_dt, end_dt],
    ).fetchone()
    return int(result[0]) if result else 0


def _count_per_code(
    conn: Any,
    table: str,
    ts_column: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list[tuple[str, int]]:
    rows_sql = latest_machine_rows(table)
    # UNNEST flattens the gap_codes VARCHAR[] into one row per (sample, code);
    # a sample carrying ["a","b"] therefore contributes one row to each code.
    # That matches the natural reading of "share of samples that recorded
    # code X" so long as a sample's codes are unique within itself (the
    # collector guarantees this).
    rows = conn.execute(
        f"""
        SELECT code, count(*) AS rows_with_code
        FROM (
            SELECT UNNEST(gap_codes) AS code, {ts_column}
            FROM ({rows_sql})
            WHERE {ts_column} >= ? AND {ts_column} < ?
        )
        GROUP BY code
        ORDER BY rows_with_code DESC, code
        """,
        [start_dt, end_dt],
    ).fetchall()
    return [(str(code), int(rows_with_code)) for code, rows_with_code in rows]
