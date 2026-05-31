"""Git code-quality / rework-detection analysis.

Detects revert and fixup/rework commits from the git source and computes
per-repo and per-day rework ratios.  Also surfaces a churn-rework signal
(files re-edited by a fix-class commit shortly after being changed) as a
proxy for short-cycle rework.

CAVEATS / INTEGRITY NOTES
--------------------------
* **Heuristic subject matching.**  Revert detection relies on conventional
  ``Revert "..."`` subject prefixes or the presence of "This reverts commit"
  in the body.  Non-conventional messages ("undid the thing", "rolling back
  X") are NOT detected; coverage is therefore a lower bound.

* **Fixup detection is a superset.**  ``fix!``, ``fixup!``, ``squash!``
  prefixes are Conventional Commits machinery; the bare ``fix:`` prefix is
  also matched because the fixup-class heuristic intentionally casts a wide
  net, which risks false positives on legitimate bug-fix commits.  Callers
  that need only interactive-rebase fixups should filter on
  ``is_squash_fixup`` separately.

* **Denominators are always exposed.**  No bare ratio is emitted anywhere.
  Every ratio field is accompanied by numerator + denominator fields.

* **Zero-commit repos/days are excluded from per-entity ratio rows.**  A
  repo that had no commits in the window is not represented in
  ``per_repo_rows``; a day with zero commits is not represented in
  ``per_day_rows``.  This prevents fabricating a 0.0 rework-ratio for
  absent entities.

* **Churn-rework window.**  The re-edit signal is a heuristic: it counts
  files touched by a fix-class commit that were also changed by any commit
  in the preceding ``churn_window_days`` days (default 14).  It is a proxy,
  not a ground truth — a "rework hit" could be legitimate iteration, not a
  bug fix.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterator, Optional

from ..sources.git_models import GitCommitFact

# ──────────────────────────────────────────────────────────────────────────────
# Detection patterns
# ──────────────────────────────────────────────────────────────────────────────

_REVERT_RE = re.compile(r"^revert\b", re.IGNORECASE)
_REVERT_BODY_FRAGMENT = "This reverts commit"

_SQUASH_FIXUP_RE = re.compile(r"^(fixup|squash)!", re.IGNORECASE)
_FIX_PREFIX_RE = re.compile(r"^fix[: !]", re.IGNORECASE)
_FIXUP_WORD_RE = re.compile(r"^fixup\b", re.IGNORECASE)
_TYPO_OOPS_RE = re.compile(r"\b(typo|oops|amend)\b", re.IGNORECASE)


def _is_revert(subject: str) -> bool:
    """Return True if the commit subject matches the revert heuristic."""
    return bool(_REVERT_RE.match(subject)) or _REVERT_BODY_FRAGMENT in subject


def _is_fixup(subject: str) -> bool:
    """Return True if the subject matches the fixup/rework heuristic.

    Matches: ``fixup! …``, ``squash! …``, ``fix: …``, ``fix! …``,
    bare ``fixup …``, and subjects containing ``typo``, ``oops``, or ``amend``.
    Note: ``fix:`` overlaps with conventional bug-fix commits; this is
    intentional (wide net, documented as a caveat).
    """
    return (
        bool(_SQUASH_FIXUP_RE.match(subject))
        or bool(_FIX_PREFIX_RE.match(subject))
        or bool(_FIXUP_WORD_RE.match(subject))
        or bool(_TYPO_OOPS_RE.search(subject))
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommitClassification:
    """Classification result for a single commit.

    ``is_revert`` and ``is_fixup`` can both be False (ordinary commit).
    They can both be True for a revert of a fixup, though that is rare.
    ``is_rework`` is the union: ``is_revert or is_fixup``.
    """

    commit: str
    repo: str
    authored_at: date
    subject: str
    is_revert: bool
    is_fixup: bool
    is_squash_fixup: bool  # narrower: only interactive-rebase fixup!/squash!

    @property
    def is_rework(self) -> bool:
        """True when this commit is classified as any kind of rework."""
        return self.is_revert or self.is_fixup


@dataclass(frozen=True)
class RepoReworkRow:
    """Per-repo rework summary over the analysis window.

    Only repos with at least one commit in the window appear here.
    ``rework_ratio`` = ``rework_count / commit_count`` but both numerator and
    denominator are exposed; callers must not compute the ratio themselves from
    the raw count without checking the denominator.
    """

    repo: str
    commit_count: int
    revert_count: int
    fixup_count: int
    rework_count: int  # revert_count + fixup_count (union, de-duplicated)
    churn_rework_hits: int  # files re-edited by a fix-class commit within window

    @property
    def rework_ratio(self) -> float:
        """Fraction of commits classified as rework; 0.0 when commit_count == 0."""
        return self.rework_count / self.commit_count if self.commit_count else 0.0


@dataclass(frozen=True)
class DayReworkRow:
    """Per-day rework counts across all repos.

    Only days with at least one commit appear here.  The ``*_count`` fields
    aggregate over all repos; ``commit_count`` is the denominator.
    """

    date: date
    commit_count: int
    revert_count: int
    fixup_count: int
    rework_count: int

    @property
    def rework_ratio(self) -> float:
        """Fraction of commits on this day classified as rework."""
        return self.rework_count / self.commit_count if self.commit_count else 0.0


@dataclass(frozen=True)
class TrendSignal:
    """Direction of a Mann-Kendall trend, or None when not computable."""

    direction: Optional[str]  # "increasing" | "decreasing" | "no trend" | None
    tau: Optional[float]
    p_value: Optional[float]
    n: int


@dataclass
class CodeQualityReport:
    """Full rework-detection analysis over a date range.

    Repos with zero commits in the window are excluded from ``per_repo_rows``
    rather than being listed with a fabricated 0.0 ratio.  Days with zero
    commits are excluded from ``per_day_rows`` for the same reason.

    ``churn_window_days`` is the look-back window used for the re-edit signal.
    """

    window_start: date
    window_end: date
    churn_window_days: int

    # Totals
    total_commits: int = 0
    total_reverts: int = 0
    total_fixups: int = 0
    total_rework: int = 0  # union (a commit can only be counted once)
    total_churn_rework_hits: int = 0

    # Per-entity breakdowns (exclude zero-commit repos/days)
    per_repo_rows: list[RepoReworkRow] = field(default_factory=list)
    per_day_rows: list[DayReworkRow] = field(default_factory=list)

    # All classified commits (including non-rework)
    classifications: list[CommitClassification] = field(default_factory=list)

    # Trend in rework ratio over per_day_rows
    rework_ratio_trend: Optional[TrendSignal] = None

    summary: str = ""

    @property
    def overall_rework_ratio(self) -> float:
        """Overall rework ratio; 0.0 when there are no commits in the window."""
        return self.total_rework / self.total_commits if self.total_commits else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def analyze(
    *,
    start: date,
    end: date,
    churn_window_days: int = 14,
) -> CodeQualityReport:
    """Analyse rework density in the git log over [start, end].

    Fetches commit facts from the git source, classifies each commit as revert,
    fixup, or ordinary, then builds per-repo and per-day summary rows.

    The churn-rework signal counts files that a fix-class commit re-edits
    within ``churn_window_days`` days of a prior edit to that file.  This is a
    heuristic proxy for short-cycle rework; it may include legitimate iteration.

    Args:
        start: First day of the analysis window (inclusive).
        end: Last day of the analysis window (inclusive).
        churn_window_days: Look-back window in days for the churn-rework signal.

    Returns:
        CodeQualityReport — denominators always present; zero-commit entities
        excluded rather than fabricated with a 0.0 ratio.
    """
    from ..sources.git import commit_facts

    facts = list(commit_facts(start=start, end=end))
    return _build_report(facts, start=start, end=end, churn_window_days=churn_window_days)


# ──────────────────────────────────────────────────────────────────────────────
# Internal implementation
# ──────────────────────────────────────────────────────────────────────────────


def _classify(fact: GitCommitFact) -> CommitClassification:
    """Classify a single commit fact into the rework taxonomy."""
    rev = _is_revert(fact.subject)
    sqf = bool(_SQUASH_FIXUP_RE.match(fact.subject))
    fix = _is_fixup(fact.subject) and not rev  # reverts are not double-counted as fixups
    return CommitClassification(
        commit=fact.commit,
        repo=fact.repo,
        authored_at=fact.authored_at.date(),
        subject=fact.subject,
        is_revert=rev,
        is_fixup=fix,
        is_squash_fixup=sqf,
    )


def _churn_rework_hits(
    classifications: list[CommitClassification],
    fact_by_commit: dict[str, GitCommitFact],
    churn_window_days: int,
) -> dict[str, int]:
    """Return per-repo count of churn-rework hits.

    A hit is a (repo, path) pair where a fix-class commit touches a file that
    was also touched by any commit in the preceding ``churn_window_days`` days.

    Complexity: O(fix_commits × window_commits × paths_per_commit) which is
    acceptable for personal-scale repos.

    Caveats: counts file×commit pairs, not unique files; the same file being
    re-edited in two different fix-class commits counts as two hits.
    """
    # Build per-repo timeline: date → list[paths]
    repo_timeline: dict[str, list[tuple[date, set[str]]]] = defaultdict(list)
    for fact in fact_by_commit.values():
        repo_timeline[fact.repo].append((fact.authored_at.date(), set(fact.paths)))
    for repo in repo_timeline:
        repo_timeline[repo].sort(key=lambda x: x[0])

    hits: dict[str, int] = defaultdict(int)
    for clf in classifications:
        if not clf.is_rework:
            continue
        fact = fact_by_commit.get(clf.commit)
        if fact is None or not fact.paths:
            continue
        fix_date = clf.authored_at
        window_start = fix_date - timedelta(days=churn_window_days)
        prior_paths: set[str] = set()
        for prior_date, prior_path_set in repo_timeline[clf.repo]:
            if prior_date < window_start:
                continue
            if prior_date >= fix_date:
                break
            prior_paths.update(prior_path_set)
        overlap = set(fact.paths) & prior_paths
        hits[clf.repo] += len(overlap)

    return dict(hits)


def _build_report(
    facts: list[GitCommitFact],
    *,
    start: date,
    end: date,
    churn_window_days: int,
) -> CodeQualityReport:
    """Build the report from a list of commit facts."""
    report = CodeQualityReport(
        window_start=start,
        window_end=end,
        churn_window_days=churn_window_days,
    )

    if not facts:
        report.summary = _build_summary(report)
        return report

    fact_by_commit = {f.commit: f for f in facts}
    classifications = [_classify(f) for f in facts]
    report.classifications = classifications

    # Totals
    seen_rework: set[str] = set()
    for clf in classifications:
        report.total_commits += 1
        if clf.is_revert:
            report.total_reverts += 1
        if clf.is_fixup:
            report.total_fixups += 1
        if clf.is_rework and clf.commit not in seen_rework:
            report.total_rework += 1
            seen_rework.add(clf.commit)

    # Churn-rework signal
    hits = _churn_rework_hits(classifications, fact_by_commit, churn_window_days)

    # Per-repo rows (exclude zero-commit repos)
    repo_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for clf in classifications:
        repo_counts[clf.repo]["total"] += 1
        if clf.is_revert:
            repo_counts[clf.repo]["revert"] += 1
        if clf.is_fixup:
            repo_counts[clf.repo]["fixup"] += 1
        if clf.is_rework:
            repo_counts[clf.repo]["rework"] += 1

    report.per_repo_rows = sorted(
        [
            RepoReworkRow(
                repo=repo,
                commit_count=counts["total"],
                revert_count=counts["revert"],
                fixup_count=counts["fixup"],
                rework_count=counts["rework"],
                churn_rework_hits=hits.get(repo, 0),
            )
            for repo, counts in repo_counts.items()
            if counts["total"] > 0
        ],
        key=lambda r: r.repo,
    )
    report.total_churn_rework_hits = sum(r.churn_rework_hits for r in report.per_repo_rows)

    # Per-day rows (exclude zero-commit days)
    day_counts: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen_day_rework: dict[date, set[str]] = defaultdict(set)
    for clf in classifications:
        d = clf.authored_at
        day_counts[d]["total"] += 1
        if clf.is_revert:
            day_counts[d]["revert"] += 1
        if clf.is_fixup:
            day_counts[d]["fixup"] += 1
        if clf.is_rework and clf.commit not in seen_day_rework[d]:
            day_counts[d]["rework"] += 1
            seen_day_rework[d].add(clf.commit)

    report.per_day_rows = sorted(
        [
            DayReworkRow(
                date=d,
                commit_count=counts["total"],
                revert_count=counts["revert"],
                fixup_count=counts["fixup"],
                rework_count=counts["rework"],
            )
            for d, counts in day_counts.items()
            if counts["total"] > 0
        ],
        key=lambda r: r.date,
    )

    # Trend in rework ratio
    report.rework_ratio_trend = _compute_trend(report.per_day_rows)

    report.summary = _build_summary(report)
    return report


def _compute_trend(day_rows: list[DayReworkRow]) -> Optional[TrendSignal]:
    """Mann-Kendall trend on the per-day rework ratio series.

    Returns None when there are fewer than 4 data points (trend is unreliable).
    """
    if len(day_rows) < 4:
        return TrendSignal(direction=None, tau=None, p_value=None, n=len(day_rows))

    try:
        from ..core.analytics import detect_trend
    except ImportError:
        return TrendSignal(direction=None, tau=None, p_value=None, n=len(day_rows))

    values = [r.rework_ratio for r in day_rows]
    result = detect_trend(values)
    if result is None:
        return TrendSignal(direction=None, tau=None, p_value=None, n=len(day_rows))

    # detect_trend returns a named-tuple or similar; access by attribute
    try:
        direction = result.trend  # type: ignore[union-attr]
        tau = float(result.Tau) if hasattr(result, "Tau") else None  # type: ignore[union-attr]
        p_value = float(result.p) if hasattr(result, "p") else None  # type: ignore[union-attr]
    except AttributeError:
        direction = str(result)
        tau = None
        p_value = None

    return TrendSignal(
        direction=direction,
        tau=tau,
        p_value=p_value,
        n=len(day_rows),
    )


def _build_summary(report: CodeQualityReport) -> str:
    """Human-readable summary of code-quality / rework findings."""
    lines = [
        f"Code Quality / Rework Report: {report.window_start} → {report.window_end}",
        f"  Total commits: {report.total_commits}",
        f"  Reverts: {report.total_reverts}",
        f"  Fixups/reworks: {report.total_fixups}",
        f"  Total rework (union): {report.total_rework} / {report.total_commits}"
        + (f"  ({report.overall_rework_ratio:.1%})" if report.total_commits else ""),
        f"  Churn-rework hits (re-edited within {report.churn_window_days}d): "
        f"{report.total_churn_rework_hits}",
    ]

    if not report.total_commits:
        lines.append("  No commits in window — no rework analysis possible.")
        lines.append("")
        lines.append(
            "CAVEAT: heuristic subject matching; revert detection misses non-conventional "
            "messages; fix: prefix may include legitimate bug-fix commits."
        )
        return "\n".join(lines)

    lines.append("")
    if report.per_repo_rows:
        lines.append("Per-repo rework (repos with commits only):")
        for row in sorted(report.per_repo_rows, key=lambda r: -r.rework_ratio):
            lines.append(
                f"  {row.repo}: {row.rework_count}/{row.commit_count} rework "
                f"({row.rework_ratio:.1%}), churn-hits={row.churn_rework_hits}"
            )

    if report.rework_ratio_trend is not None and report.rework_ratio_trend.direction is not None:
        lines.append("")
        lines.append(
            f"Rework-ratio trend (Mann-Kendall, n={report.rework_ratio_trend.n}): "
            f"{report.rework_ratio_trend.direction}"
            + (f", τ={report.rework_ratio_trend.tau:.3f}" if report.rework_ratio_trend.tau is not None else "")
            + (f", p={report.rework_ratio_trend.p_value:.4f}" if report.rework_ratio_trend.p_value is not None else "")
        )

    lines.append("")
    lines.append(
        "CAVEATS: heuristic subject matching; revert detection misses non-conventional "
        "messages; fix: prefix overlaps with legitimate bug-fix commits (wide net, "
        "documented); churn-rework hits are a proxy, not ground truth."
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Convenience iterator
# ──────────────────────────────────────────────────────────────────────────────


def iter_rework_commits(
    *,
    start: date,
    end: date,
) -> Iterator[CommitClassification]:
    """Yield only the rework-classified commits in [start, end].

    Convenience wrapper over ``analyze`` for callers that need only the
    rework commits without the full report.
    """
    report = analyze(start=start, end=end)
    for clf in report.classifications:
        if clf.is_rework:
            yield clf


__all__ = [
    "CommitClassification",
    "RepoReworkRow",
    "DayReworkRow",
    "TrendSignal",
    "CodeQualityReport",
    "analyze",
    "iter_rework_commits",
]
