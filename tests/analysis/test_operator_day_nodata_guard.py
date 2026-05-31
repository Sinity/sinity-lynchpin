"""Guard tests for the OperatorDay zero-vs-no-data footgun (D2).

Counter fields default to ``0`` for both a genuine zero and a no-coverage day.
``has_source``/``measured`` are the typed guards that let consumers tell them
apart; these tests pin that contract so a future consumer reading a raw ``0``
gets caught, not silently fed a fabricated observation.
"""

from __future__ import annotations

from datetime import date

from lynchpin.analysis.operator_daily import OperatorDay


def test_counter_defaults_are_zero_the_trap():
    """A day with no sources still reports 0 commits — the trap being guarded."""
    row = OperatorDay(date=date(2024, 1, 1))
    assert row.git_commits == 0
    assert row.substance_doses == 0
    # ...but provenance shows nothing was observed.
    assert row.sources_present == frozenset()
    assert row.has_source("git") is False
    assert row.has_source("substance") is False


def test_measured_returns_none_for_unobserved_source():
    """measured() converts the fabricated 0 into an honest None when unobserved."""
    row = OperatorDay(date=date(2024, 1, 1))
    row.git_commits = 0
    assert row.measured("git", row.git_commits) is None
    row.substance_doses = 0
    assert row.measured("substance", row.substance_doses) is None


def test_measured_returns_value_for_observed_source():
    """When the source IS present, measured() passes the real value through."""
    row = OperatorDay(date=date(2024, 1, 1))
    row.git_commits = 5
    row.sources_present = frozenset({"git"})
    assert row.has_source("git") is True
    assert row.measured("git", row.git_commits) == 5
    # A genuine in-coverage zero is preserved as 0 (not None).
    row.git_commits = 0
    assert row.measured("git", row.git_commits) == 0
