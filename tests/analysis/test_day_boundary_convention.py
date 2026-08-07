"""Enforce the logical-day convention for day-bucketing in the analysis layer.

The repo's daily products key by the 06:00-local *logical* day
(``lynchpin.core.primitives.logical_date``). Calendar ``.date()`` bucketing
in analysis code is how keystroke_attribution silently misattributed 31.7%
of focus-span time (the 00:00-06:00 share) in a per-day join against
logical-day activity seconds (fixed in 4e37102). For an operator whose
activity is this nocturnal, calendar-vs-logical is a third of the signal
on the wrong row, so the convention is enforced, not just documented.

Mechanism: AST-count no-arg ``.date()`` calls per module and compare with
the frozen allowlist below. A *new* call fails this test; the fix is either
to bucket via ``logical_date`` (correct for anything joined with
ActivityWatch/keylog/machine daily products) or, when calendar semantics
are genuinely intended (pure timestamp arithmetic like
evening_activity_sleep's late-evening cutoff, or provider-local exports
that never join machine products), to bump the module's entry here in the
same commit — making the choice reviewable instead of accidental.

The frozen entries are under review in lynchpin-t3a; shrink them freely
(reduced counts below the allowance also fail, so the allowlist tracks
reality in both directions).
"""

from __future__ import annotations

import ast
from pathlib import Path

ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "lynchpin" / "analysis"

# module filename -> number of sanctioned no-arg .date() call sites.
ALLOWED_CALENDAR_DATE_CALLS = {
    "ai_session_efficiency.py": 2,  # lynchpin-t3a: self-consistent calendar days
    "code_quality.py": 2,
    "evening_activity_sleep.py": 1,  # timestamp arithmetic (late cutoff), not a join key
    "google_takeout_mining.py": 7,  # provider-local export dates
    "health_baselines.py": 1,
    "operator_public_text.py": 8,  # provider-local post timestamps
    "personal_interest_fusion.py": 2,  # lynchpin-t3a
    "sleep_coverage.py": 3,
    "sleep_stage_model.py": 1,
    "url_crossref.py": 4,  # lynchpin-t3a
    "quota_advisory.py": 1,  # provider/archive calendar query, not a daily join key
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
