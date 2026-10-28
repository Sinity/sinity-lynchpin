"""Keep analysis day keys aligned with the 06:00-local logical day.

Daily joins use ``lynchpin.core.primitives.logical_date``. Calendar dates
are permitted for explicit timestamp arithmetic and provider-local exports
that do not join daily machine products. The per-module counts below must
match those exceptions exactly, including when a call is removed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "lynchpin" / "analysis"

# module filename -> number of sanctioned no-arg .date() call sites.
ALLOWED_CALENDAR_DATE_CALLS = {
    "code_quality.py": 2,
    "evening_activity_sleep.py": 1,  # timestamp arithmetic (late cutoff), not a join key
    "google_takeout_mining.py": 7,  # provider-local export dates
    "health_baselines.py": 1,
    "keylog.py": 1,  # UTC-named log file selection, not a daily join key
    "operator_public_text.py": 8,  # provider-local post timestamps
    "sleep_coverage.py": 3,
    "sleep_stage_model.py": 1,
    "terminal_reconstruction.py": 2,  # calendar-named capture file selection, not a daily join key
}


def _count_date_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "date"
        and not node.args
        and not node.keywords
    )


def test_analysis_day_bucketing_uses_logical_date() -> None:
    actual = {
        path.name: count
        for path in sorted(ANALYSIS_DIR.glob("*.py"))
        if (count := _count_date_calls(path))
    }
    new_or_grown = {
        name: (ALLOWED_CALENDAR_DATE_CALLS.get(name, 0), count)
        for name, count in actual.items()
        if count > ALLOWED_CALENDAR_DATE_CALLS.get(name, 0)
    }
    shrunk = {
        name: (allowed, actual.get(name, 0))
        for name, allowed in ALLOWED_CALENDAR_DATE_CALLS.items()
        if actual.get(name, 0) < allowed
    }
    assert not new_or_grown, (
        "new calendar .date() call(s) in analysis modules "
        f"{new_or_grown} (allowed, actual): bucket by "
        "lynchpin.core.primitives.logical_date instead, or consciously "
        "extend ALLOWED_CALENDAR_DATE_CALLS in this test (see docstring)"
    )
    assert not shrunk, (
        f"calendar .date() calls removed {shrunk} (allowed, actual): "
        "great - shrink ALLOWED_CALENDAR_DATE_CALLS to match"
    )
