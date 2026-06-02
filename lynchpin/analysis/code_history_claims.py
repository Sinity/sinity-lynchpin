"""Evidence-shaped claims from git commit and file-change history."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.sources.git_models import GitCommitFact, GitFileChangeFact
from lynchpin.substrate.claims import AnalysisClaimRow, claim_id


@dataclass(frozen=True)
class CodeHistoryInputs:
    commits: tuple[GitCommitFact, ...]
    file_changes: tuple[GitFileChangeFact, ...]


def load_code_history_inputs(*, start: date, end: date) -> CodeHistoryInputs:
    from lynchpin.sources import git as git_source

    return CodeHistoryInputs(
        commits=tuple(git_source.commit_facts(start=start, end=end)),
        file_changes=tuple(git_source.file_change_facts(start=start, end=end)),
    )


def code_history_claims(
    *,
    start: date,
    end: date,
    project: str | None = None,
    top_n: int = 25,
    inputs: CodeHistoryInputs | None = None,
) -> list[AnalysisClaimRow]:
    """Return ranked, substrate-compatible claims from code history.

    These claims are intentionally observational: they identify where history
    concentrates risk or attention, but they do not assert causality.
    """
    data = inputs or load_code_history_inputs(start=start, end=end)
    commits = tuple(c for c in data.commits if project is None or c.repo == project)
    file_changes = tuple(
        row for row in data.file_changes if project is None or row.repo == project
    )
    claims: list[AnalysisClaimRow] = []
    claims.extend(_hotspot_claims(start=start, end=end, rows=file_changes, top_n=top_n))
    claims.extend(_broad_change_claims(start=start, end=end, commits=commits, top_n=top_n))
    claims.extend(_rework_claims(start=start, end=end, commits=commits, top_n=top_n))
    claims.sort(key=lambda row: (-row.confidence, -row.score, row.claim_type, row.summary))
    return claims[: max(top_n, 1)]


def write_code_history_claims(
    out: Path,
    *,
    start: date,
    end: date,
    project: str | None = None,
    top_n: int = 50,
    inputs: CodeHistoryInputs | None = None,
) -> list[AnalysisClaimRow]:
    claims = code_history_claims(
        start=start,
        end=end,
        project=project,
        top_n=top_n,
        inputs=inputs,
    )
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "project": project,
        "claim_count": len(claims),
        "claims": [_claim_payload(row) for row in claims],
        "caveats": [
            "code-history claims are observational and git-derived",
            "hotspots/rework/broad-surface changes do not prove runtime defects or causal impact",
        ],
    }
    save_json(out, payload, sort_keys=True)
    return claims


def _claim_payload(row: AnalysisClaimRow) -> dict[str, Any]:
    payload = asdict(row)
    day = payload.get("date")
    if isinstance(day, date):
        payload["date"] = day.isoformat()
    return payload


def _hotspot_claims(
    *,
    start: date,
    end: date,
    rows: Iterable[GitFileChangeFact],
    top_n: int,
) -> list[AnalysisClaimRow]:
    by_key: dict[tuple[str, str], list[GitFileChangeFact]] = defaultdict(list)
    for row in rows:
        by_key[(row.repo, row.path_root or row.path)].append(row)
    claims = []
    for (repo, root), group in by_key.items():
        commits = {row.commit for row in group}
        churn = sum(row.lines_changed for row in group)
        if len(commits) < 2 and churn < 500:
            continue
        score = churn * max(len(commits), 1)
        support = "moderate" if len(commits) >= 3 or churn >= 1000 else "weak"
        confidence = 0.70 if support == "moderate" else 0.45
        claims.append(
            AnalysisClaimRow(
                claim_id=claim_id("code_hotspot", repo, root, start, end),
                claim_type="code_hotspot",
                project=repo,
                date=end,
                support_level=support,
                confidence=confidence,
                score=float(score),
                summary=(
                    f"{repo}:{root} is a code-history hotspot "
                    f"({len(commits)} commits, {churn} changed lines)"
                ),
                source_ids=tuple(sorted(commits)[:50]),
                relation_ids=(),
                caveats=(
                    "hotspot is based on git churn concentration, not runtime defects",
                ),
                payload={
                    "path_root": root,
                    "commit_count": len(commits),
                    "file_change_count": len(group),
                    "lines_changed": churn,
                    "first_date": min(row.date for row in group).isoformat(),
                    "last_date": max(row.date for row in group).isoformat(),
                },
            )
        )
    claims.sort(key=lambda row: (-row.score, row.project or "", row.summary))
    return claims[:top_n]


def _broad_change_claims(
    *,
    start: date,
    end: date,
    commits: Iterable[GitCommitFact],
    top_n: int,
) -> list[AnalysisClaimRow]:
    candidates = [
        commit
        for commit in commits
        if commit.files_changed >= 20 or commit.lines_changed >= 2500
    ]
    rows = []
    for commit in candidates:
        score = float(commit.files_changed * max(commit.lines_changed, 1))
        support = "moderate" if commit.files_changed >= 40 or commit.lines_changed >= 5000 else "weak"
        confidence = 0.68 if support == "moderate" else 0.42
        rows.append(
            AnalysisClaimRow(
                claim_id=claim_id("code_broad_change", commit.repo, commit.commit),
                claim_type="code_broad_change",
                project=commit.repo,
                date=commit.date,
                support_level=support,
                confidence=confidence,
                score=score,
                summary=(
                    f"{commit.repo} commit {commit.commit[:8]} touched a broad surface "
                    f"({commit.files_changed} files, {commit.lines_changed} changed lines)"
                ),
                source_ids=(commit.commit,),
                relation_ids=(),
                caveats=(
                    "broad change can be mechanical; inspect subject and paths before treating as risk",
                ),
                payload={
                    "commit": commit.commit,
                    "subject": commit.subject,
                    "files_changed": commit.files_changed,
                    "lines_changed": commit.lines_changed,
                    "path_roots": list(commit.path_roots),
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
        )
    rows.sort(key=lambda row: (-row.score, row.date or date.min))
    return rows[:top_n]


def _rework_claims(
    *,
    start: date,
    end: date,
    commits: Iterable[GitCommitFact],
    top_n: int,
) -> list[AnalysisClaimRow]:
    by_repo: dict[str, list[GitCommitFact]] = defaultdict(list)
    for commit in commits:
        by_repo[commit.repo].append(commit)
    rows = []
    for repo, group in by_repo.items():
        rework = [commit for commit in group if _is_rework_subject(commit.subject)]
        if len(group) < 3 or not rework:
            continue
        ratio = len(rework) / len(group)
        if ratio < 0.25 and len(rework) < 3:
            continue
        score = ratio * len(group)
        support = "moderate" if ratio >= 0.35 or len(rework) >= 5 else "weak"
        confidence = 0.66 if support == "moderate" else 0.40
        rows.append(
            AnalysisClaimRow(
                claim_id=claim_id("code_rework_pressure", repo, start, end),
                claim_type="code_rework_pressure",
                project=repo,
                date=end,
                support_level=support,
                confidence=confidence,
                score=round(score, 6),
                summary=(
                    f"{repo} shows elevated rework pressure "
                    f"({len(rework)}/{len(group)} commits match rework subjects)"
                ),
                source_ids=tuple(commit.commit for commit in rework[:50]),
                relation_ids=(),
                caveats=(
                    "subject heuristics overcount some legitimate fix commits and undercount silent rewrites",
                ),
                payload={
                    "commit_count": len(group),
                    "rework_count": len(rework),
                    "rework_ratio": round(ratio, 6),
                    "sample_subjects": [commit.subject for commit in rework[:5]],
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
        )
    rows.sort(key=lambda row: (-row.score, row.project or ""))
    return rows[:top_n]


def _is_rework_subject(subject: str) -> bool:
    return bool(
        re.search(
            r"(?i)\b(revert|fixup|squash|oops|typo|amend)\b|^(fix|hotfix)(\(|:|!)",
            subject,
        )
    )


__all__ = [
    "CodeHistoryInputs",
    "code_history_claims",
    "load_code_history_inputs",
    "write_code_history_claims",
]
