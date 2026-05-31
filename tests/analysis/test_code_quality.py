"""Tests for lynchpin.analysis.code_quality.

Pins the three correctness contracts:

1. Revert and fixup detection — correct classification by subject heuristics.
2. Ratio denominators always present — zero-commit repos/days excluded, not
   fabricated with a 0.0 ratio.
3. Zero-commits-≠-zero-ratio — entities with no commits in the window must be
   absent from per_repo_rows / per_day_rows, not listed with a spurious 0.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import lynchpin.analysis.code_quality as cq
from lynchpin.analysis.code_quality import analyze
from lynchpin.sources.git_models import GitCommitFact

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_TZ = timezone.utc


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=_TZ)


def _fact(
    subject: str,
    *,
    repo: str = "testrepo",
    d: date = date(2024, 6, 1),
    commit: str | None = None,
    paths: tuple[str, ...] = ("src/foo.py",),
) -> GitCommitFact:
    """Build a synthetic GitCommitFact for testing."""
    sha = commit or subject[:8].replace(" ", "_").lower()
    return GitCommitFact(
        repo=repo,
        commit=sha,
        authored_at=_dt(d),
        author="Test User",
        subject=subject,
        lines_added=10,
        lines_deleted=5,
        lines_changed=15,
        files_changed=len(paths),
        paths=paths,
        path_roots=tuple(p.split("/")[0] for p in paths),
    )


def _patch_commit_facts(monkeypatch, facts: list[GitCommitFact]) -> None:
    """Monkeypatch lynchpin.sources.git.commit_facts to return a fixed list."""
    import lynchpin.sources.git as git_mod

    monkeypatch.setattr(git_mod, "commit_facts", lambda *, start, end: iter(facts))


# ──────────────────────────────────────────────────────────────────────────────
# 1. Detection correctness
# ──────────────────────────────────────────────────────────────────────────────


class TestClassification:
    """Unit-level classification tests — no monkeypatching needed."""

    @pytest.mark.parametrize(
        "subject,expect_revert",
        [
            ("Revert \"feat: add oauth\"", True),
            ("revert: bad deploy", True),
            ("REVERT something", True),
            ("This reverts commit abc123def", True),
            ("feat: add login", False),
            ("fix: null pointer", False),
            ("fix! urgent hotfix", False),  # fix-class but not a revert
        ],
    )
    def test_revert_detection(self, subject: str, expect_revert: bool) -> None:
        clf = cq._classify(_fact(subject))
        assert clf.is_revert is expect_revert, f"subject={subject!r}"

    @pytest.mark.parametrize(
        "subject,expect_fixup",
        [
            ("fixup! rename variable", True),
            ("squash! cleanup whitespace", True),
            ("fix: broken import", True),     # wide net — documented caveat
            ("fix! emergency patch", True),
            ("fixup some leftover", True),
            ("oops forgot semicolon", True),
            ("typo in README", True),
            ("amend previous changes", True),
            ("feat: add feature", False),
            ("docs: update readme", False),
            ("chore: bump version", False),
        ],
    )
    def test_fixup_detection(self, subject: str, expect_fixup: bool) -> None:
        clf = cq._classify(_fact(subject))
        # Reverts are NOT double-counted as fixups; a revert subject like
        # "Revert X" may not match fixup patterns anyway, but guard explicitly.
        if clf.is_revert:
            assert clf.is_fixup is False
        else:
            assert clf.is_fixup is expect_fixup, f"subject={subject!r}"

    def test_is_rework_union(self) -> None:
        """is_rework is True when either is_revert or is_fixup is True."""
        rev = cq._classify(_fact("Revert \"feat: foo\""))
        assert rev.is_revert and rev.is_rework
        assert not rev.is_fixup  # revert suppresses fixup double-count

        fix = cq._classify(_fact("fixup! rename"))
        assert fix.is_fixup and fix.is_rework
        assert not fix.is_revert

        normal = cq._classify(_fact("feat: add thing"))
        assert not normal.is_rework

    def test_squash_fixup_is_subset_of_fixup(self) -> None:
        """is_squash_fixup must only be True for fixup!/squash! prefixes."""
        sqf = cq._classify(_fact("fixup! whitespace"))
        assert sqf.is_squash_fixup and sqf.is_fixup

        plain_fix = cq._classify(_fact("fix: null crash"))
        assert not plain_fix.is_squash_fixup
        assert plain_fix.is_fixup


# ──────────────────────────────────────────────────────────────────────────────
# 2. Ratio denominators always exposed; integration through analyze()
# ──────────────────────────────────────────────────────────────────────────────


class TestReport:
    def test_denominators_exposed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every ratio field must be accompanied by numerator + denominator."""
        d0 = date(2024, 3, 1)
        facts = [
            _fact("feat: feature A", d=d0, commit="aaa001"),
            _fact("fixup! spelling", d=d0, commit="bbb002"),
            _fact("Revert \"chore: X\"", d=d0 + timedelta(days=1), commit="ccc003"),
            _fact("docs: update guide", d=d0 + timedelta(days=1), commit="ddd004"),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0 + timedelta(days=2))

        # Totals are separate fields
        assert report.total_commits == 4
        assert report.total_rework == 2  # fixup + revert
        assert report.total_reverts == 1
        assert report.total_fixups == 1

        # overall_rework_ratio is a property with accessible denominator
        assert report.overall_rework_ratio == report.total_rework / report.total_commits

        # Per-repo row carries commit_count as denominator
        assert len(report.per_repo_rows) == 1
        row = report.per_repo_rows[0]
        assert row.commit_count == 4
        assert row.rework_count == 2
        assert row.rework_ratio == row.rework_count / row.commit_count

        # Per-day rows carry commit_count as denominator
        assert len(report.per_day_rows) == 2
        for day_row in report.per_day_rows:
            assert day_row.commit_count > 0
            assert day_row.rework_ratio == day_row.rework_count / day_row.commit_count

    def test_revert_count_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        d0 = date(2024, 4, 1)
        facts = [
            _fact("Revert \"feat: foo\"", d=d0, commit="r001"),
            _fact("Revert \"fix: bar\"", d=d0, commit="r002"),
            _fact("feat: normal", d=d0, commit="n003"),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0)

        assert report.total_reverts == 2
        assert report.total_fixups == 0
        assert report.total_rework == 2
        assert report.total_commits == 3

    def test_fixup_count_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        d0 = date(2024, 4, 5)
        facts = [
            _fact("fixup! oops", d=d0, commit="f001"),
            _fact("squash! cleanup", d=d0, commit="f002"),
            _fact("fix: real bug", d=d0, commit="f003"),
            _fact("typo in comment", d=d0, commit="f004"),
            _fact("chore: deps", d=d0, commit="n005"),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0)

        assert report.total_fixups == 4
        assert report.total_reverts == 0
        assert report.total_rework == 4
        assert report.total_commits == 5

    def test_summary_contains_caveat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Summary must contain the heuristic-matching caveat."""
        d0 = date(2024, 5, 1)
        facts = [_fact("feat: thing", d=d0, commit="x001")]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0)
        assert "heuristic" in report.summary.lower()
        assert "revert" in report.summary.lower()


# ──────────────────────────────────────────────────────────────────────────────
# 3. Zero-commits-≠-zero-ratio
# ──────────────────────────────────────────────────────────────────────────────


class TestZeroCommitsExclusion:
    def test_empty_window_yields_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A window with no commits must produce empty per_repo/per_day rows.

        The overall_rework_ratio is 0.0 only because the denominator is 0 and
        the property guards against division by zero — not because we fabricate
        a rework record.
        """
        _patch_commit_facts(monkeypatch, [])
        report = analyze(start=date(2024, 1, 1), end=date(2024, 1, 31))

        assert report.total_commits == 0
        assert report.total_rework == 0
        assert report.per_repo_rows == []
        assert report.per_day_rows == []
        # overall_rework_ratio is 0.0 but that is the guard value, not a fabricated ratio
        assert report.overall_rework_ratio == 0.0

    def test_zero_rework_repo_present_but_ratio_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repo with commits but no rework appears in per_repo_rows with rework_count=0.

        It IS present (it had commits) but rework_count == 0, not absent.
        This confirms the contract: only repos with zero commits are excluded.
        """
        d0 = date(2024, 2, 1)
        facts = [
            _fact("feat: clean feature", d=d0, commit="c001"),
            _fact("docs: update api", d=d0, commit="c002"),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0)

        assert len(report.per_repo_rows) == 1
        row = report.per_repo_rows[0]
        assert row.commit_count == 2
        assert row.rework_count == 0
        assert row.rework_ratio == 0.0  # legitimate zero — there were commits

    def test_multi_repo_zero_commit_repo_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repos that had no commits in the window are NOT present in per_repo_rows.

        Only repos that actually had commits appear; the missing ones are
        absent from the report entirely, not represented with a fabricated row.
        """
        d0 = date(2024, 7, 1)
        facts = [
            _fact("feat: alpha", repo="repo-A", d=d0, commit="a001"),
            _fact("fixup! alpha", repo="repo-A", d=d0, commit="a002"),
            # repo-B had NO commits in this window
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0)

        repos_in_report = {r.repo for r in report.per_repo_rows}
        assert "repo-A" in repos_in_report
        assert "repo-B" not in repos_in_report  # absent, not fabricated

    def test_day_without_commits_absent_from_per_day_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Days with no commits in the window must not appear in per_day_rows."""
        d0 = date(2024, 8, 1)
        d2 = d0 + timedelta(days=2)  # day 1 is a gap
        facts = [
            _fact("feat: day0", d=d0, commit="x001"),
            _fact("fix: day2", d=d2, commit="x002"),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d2)

        days_in_report = {r.date for r in report.per_day_rows}
        assert d0 in days_in_report
        assert d2 in days_in_report
        assert d0 + timedelta(days=1) not in days_in_report  # gap day — absent


# ──────────────────────────────────────────────────────────────────────────────
# 4. Churn-rework signal
# ──────────────────────────────────────────────────────────────────────────────


class TestChurnRework:
    def test_rework_hit_on_recently_edited_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fix-class commit re-editing a recently changed file scores a hit."""
        d0 = date(2024, 9, 1)
        d1 = d0 + timedelta(days=3)  # within the 14-day default window
        shared_path = "src/module.py"
        facts = [
            _fact("feat: initial", d=d0, commit="init01", paths=(shared_path,)),
            _fact("fix: regression", d=d1, commit="fix001", paths=(shared_path,)),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d1, churn_window_days=14)

        assert report.total_churn_rework_hits >= 1
        repo_row = report.per_repo_rows[0]
        assert repo_row.churn_rework_hits >= 1

    def test_no_hit_when_beyond_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fix-class commit beyond the churn window does not score a hit."""
        d0 = date(2024, 10, 1)
        d1 = d0 + timedelta(days=20)  # beyond 14-day window
        shared_path = "src/thing.py"
        facts = [
            _fact("feat: first", d=d0, commit="e001", paths=(shared_path,)),
            _fact("fix: later", d=d1, commit="e002", paths=(shared_path,)),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d1, churn_window_days=14)

        assert report.total_churn_rework_hits == 0

    def test_no_hit_for_normal_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-rework commits do not contribute to the churn-rework hit count."""
        d0 = date(2024, 11, 1)
        shared_path = "src/util.py"
        facts = [
            _fact("feat: start", d=d0, commit="g001", paths=(shared_path,)),
            _fact("feat: extend", d=d0 + timedelta(days=1), commit="g002", paths=(shared_path,)),
        ]
        _patch_commit_facts(monkeypatch, facts)
        report = analyze(start=d0, end=d0 + timedelta(days=2))

        assert report.total_churn_rework_hits == 0
