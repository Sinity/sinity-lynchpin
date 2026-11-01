from __future__ import annotations

from typing import Any

from ..core.projects import canonical_project_name
from .analysis_artifact_helpers import dict_or_empty, list_or_empty, string_tuple
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim


def _active_symbol_index_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    projects = list_or_empty(payload.get("projects"))
    languages_indexed = string_tuple(payload.get("languages_indexed"))
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        symbol_count = int(row.get("symbol_count") or 0)
        if not row.get("exists", True) or symbol_count == 0:
            continue
        symbols = list_or_empty(row.get("symbols"))
        kind_counts: dict[str, int] = {}
        exported_count = 0
        for s in symbols:
            if not isinstance(s, dict):
                continue
            kind = str(s.get("symbol_kind") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            if s.get("exported"):
                exported_count += 1
        kind_summary = ", ".join(f"{k}={v}" for k, v in sorted(kind_counts.items()))
        summary = (
            f"{project}: {symbol_count} symbols ({exported_count} exported) "
            f"across {', '.join(string_tuple(row.get('languages'))) or 'no languages'}; "
            f"{kind_summary}"
        )
        claims.append(
            AnalysisClaim(
                id=f"symbol-index-summary:{project}",
                artifact_name=artifact.name,
                claim_type="symbol_index_summary",
                project=project,
                summary=summary,
                payload={
                    "symbol_count": symbol_count,
                    "exported_count": exported_count,
                    "kind_counts": kind_counts,
                    "languages": list(string_tuple(row.get("languages"))),
                    "languages_indexed": list(languages_indexed),
                },
                confidence=0.70,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)




def _active_ci_health_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = list_or_empty(payload.get("projects"))
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        wf_count = int(row.get("workflow_count") or 0)
        job_count = int(row.get("total_job_count") or 0)
        explicit = int(row.get("explicit_timeout_count") or 0)
        missing = int(row.get("missing_timeout_count") or 0)
        runs_block = dict_or_empty(row.get("runs"))
        run_workflows = list_or_empty(runs_block.get("workflows"))
        flaky = [w for w in run_workflows if isinstance(w, dict) and w.get("flaky")]
        runs_str = ""
        if runs_block.get("available"):
            total_runs = int(runs_block.get("total_run_count") or 0)
            runs_str = (
                f"; {total_runs} runs in last {runs_block.get('lookback_days', 30)}d"
            )
            if flaky:
                runs_str += f", {len(flaky)} flaky workflows"
        summary = (
            f"{project}: {wf_count} workflows / {job_count} jobs; "
            f"{explicit} jobs with explicit timeouts, {missing} relying on default"
            f"{runs_str}"
        )
        claims.append(
            AnalysisClaim(
                id=f"ci-health-summary:{project}",
                artifact_name=artifact.name,
                claim_type="ci_health_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "workflow_count": wf_count,
                    "total_job_count": job_count,
                    "explicit_timeout_count": explicit,
                    "missing_timeout_count": missing,
                    "runs_available": bool(runs_block.get("available")),
                    "total_run_count": runs_block.get("total_run_count"),
                    "flaky_workflow_count": len(flaky),
                    "workflows": [
                        {
                            "name": wf.get("name"),
                            "path": wf.get("path"),
                            "triggers": wf.get("triggers"),
                            "job_count": len(list_or_empty(wf.get("jobs"))),
                        }
                        for wf in list_or_empty(row.get("workflows"))
                    ],
                    "run_summaries": [
                        {
                            "name": w.get("name"),
                            "run_count": w.get("run_count"),
                            "success_rate": w.get("success_rate"),
                            "p50_duration_s": w.get("p50_duration_s"),
                            "p90_duration_s": w.get("p90_duration_s"),
                            "flaky": w.get("flaky"),
                        }
                        for w in run_workflows
                        if isinstance(w, dict)
                    ],
                },
                confidence=0.70,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _active_symbol_diffs_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = list_or_empty(payload.get("projects"))
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        events = int(row.get("events_emitted") or 0)
        breaking = int(row.get("breaking_candidate_count") or 0)
        commit_count = int(row.get("commit_count") or 0)
        top = list_or_empty(row.get("top_touched_symbols"))
        summary = (
            f"{project}: {events} symbol-touch events across {commit_count} commits "
            f"({breaking} breaking candidates)"
        )
        claims.append(
            AnalysisClaim(
                id=f"symbol-diffs-summary:{project}",
                artifact_name=artifact.name,
                claim_type="symbol_diffs_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "events_emitted": events,
                    "commit_count": commit_count,
                    "breaking_candidate_count": breaking,
                    "top_touched_symbols": [
                        {
                            "qualified_name": item.get("qualified_name"),
                            "symbol_kind": item.get("symbol_kind"),
                            "exported": item.get("exported"),
                            "touch_count": item.get("touch_count"),
                            "lines_added": item.get("lines_added"),
                            "lines_removed": item.get("lines_removed"),
                        }
                        for item in top[:10]
                    ],
                },
                confidence=0.65,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _active_symbol_changes_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = list_or_empty(payload.get("projects"))
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        commits_touched = int(row.get("commits_touched") or 0)
        breaking_count = int(row.get("breaking_candidate_count") or 0)
        kinds = dict_or_empty(row.get("symbol_touches_by_kind"))
        kind_summary = (
            ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
            or "no symbol touches"
        )
        summary = (
            f"{project}: {commits_touched} commits touched symbols "
            f"({breaking_count} breaking candidates); {kind_summary}"
        )
        claims.append(
            AnalysisClaim(
                id=f"symbol-changes-summary:{project}",
                artifact_name=artifact.name,
                claim_type="symbol_changes_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "commits_touched": commits_touched,
                    "breaking_candidate_count": breaking_count,
                    "symbol_touches_by_kind": dict(kinds),
                    "breaking_candidates": list_or_empty(
                        row.get("breaking_candidates")
                    )[:10],
                },
                confidence=0.55,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)
