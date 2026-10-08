from __future__ import annotations

from typing import Any

from ..core.projects import canonical_project_name
from .analysis_artifact_helpers import dict_or_empty, list_or_empty, string_tuple
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim


def _active_structural_findings_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return ()
    claims: list[AnalysisClaim] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        recent_count = int(row.get("recent_finding_count") or 0)
        total_count = int(row.get("finding_count") or 0)
        tool_run = dict_or_empty(row.get("tool_run"))
        summary = f"{project}: {recent_count} recent / {total_count} total structural findings"
        claims.append(
            AnalysisClaim(
                id=f"structural-findings-summary:{project}",
                artifact_name=artifact.name,
                claim_type="structural_findings_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "finding_count": total_count,
                    "recent_finding_count": recent_count,
                    "tool_available": bool(tool_run.get("available")),
                },
                confidence=0.60,
                generated_at=artifact.generated_at,
            )
        )
        findings = list_or_empty(row.get("findings"))
        for f in findings[:5]:
            if not isinstance(f, dict) or not f.get("recently_changed"):
                continue
            rule_id = str(f.get("rule_id") or "")
            path = str(f.get("path") or "")
            claims.append(
                AnalysisClaim(
                    id=f"structural-risk:{project}:{rule_id}:{path}",
                    artifact_name=artifact.name,
                    claim_type="structural_risk",
                    project=project,
                    summary=f"{project}: {rule_id} at {path}:{f.get('line', '?')}",
                    payload={
                        "window": window,
                        "rule_id": rule_id,
                        "path": path,
                        "line": f.get("line"),
                        "severity": f.get("severity"),
                        "message": f.get("message"),
                        "recently_changed": f.get("recently_changed"),
                        "caveats": f.get("caveats"),
                    },
                    confidence=0.56,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_semantic_static_findings_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    findings = list_or_empty(payload.get("findings"))
    by_project: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        project = canonical_project_name(f.get("project"))
        if project is None or (selected and project not in selected):
            continue
        by_project.setdefault(project, []).append(f)
    claims: list[AnalysisClaim] = []
    for project, items in sorted(by_project.items()):
        recent = [f for f in items if f.get("recently_changed")]
        summary = f"{project}: {len(recent)} recent / {len(items)} total semgrep privacy findings"
        claims.append(
            AnalysisClaim(
                id=f"semantic-static-summary:{project}",
                artifact_name=artifact.name,
                claim_type="semantic_static_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "finding_count": len(items),
                    "recent_finding_count": len(recent),
                },
                confidence=0.55,
                generated_at=artifact.generated_at,
            )
        )
        for f in recent[:5]:
            rule_id = str(f.get("rule_id") or f.get("check_id") or "")
            path = str(f.get("path") or "")
            claims.append(
                AnalysisClaim(
                    id=f"semantic-static-risk:{project}:{rule_id}:{path}",
                    artifact_name=artifact.name,
                    claim_type="semantic_static_risk",
                    project=project,
                    summary=f"{project}: {rule_id} at {path}:{f.get('line', '?')}",
                    payload={
                        "window": window,
                        "rule_id": rule_id,
                        "path": path,
                        "line": f.get("line"),
                        "severity": f.get("severity"),
                        "message": f.get("message"),
                    },
                    confidence=0.50,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_rust_dependency_hygiene_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    workspaces = list_or_empty(payload.get("workspaces"))
    claims: list[AnalysisClaim] = []
    for row in workspaces:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        machete = dict_or_empty(row.get("machete"))
        geiger = dict_or_empty(row.get("geiger"))
        audit = dict_or_empty(row.get("audit"))
        unused = list_or_empty(machete.get("unused_dep_candidates")) or list_or_empty(
            machete.get("unused")
        )
        candidate_count = (
            int(machete.get("candidate_count") or 0)
            if machete.get("candidate_count") is not None
            else sum(
                len(list_or_empty(c.get("unused"))) if isinstance(c, dict) else 0
                for c in unused
            )
        )
        unsafe_total = (
            int((geiger.get("unsafe") or {}).get("total") or 0)
            if isinstance(geiger.get("unsafe"), dict)
            else 0
        )
        advisories = list_or_empty(audit.get("advisories"))
        machete_available = bool(machete.get("available"))
        geiger_available = bool(geiger.get("available"))
        audit_available = bool(audit.get("available"))
        summary = (
            f"{project}: {candidate_count} unused dep candidates, "
            f"{unsafe_total} unsafe usages, {len(advisories)} RUSTSEC advisories "
            f"(machete={'on' if machete_available else 'off'}, "
            f"geiger={'on' if geiger_available else 'off'}, "
            f"audit={'on' if audit_available else 'off'})"
        )
        claims.append(
            AnalysisClaim(
                id=f"rust-dep-hygiene-summary:{project}",
                artifact_name=artifact.name,
                claim_type="rust_dep_hygiene_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "unused_count": candidate_count,
                    "unsafe_total": unsafe_total,
                    "advisory_count": len(advisories),
                    "machete_available": machete_available,
                    "geiger_available": geiger_available,
                    "audit_available": audit_available,
                    "advisories_sample": advisories[:8],
                },
                confidence=0.60,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _active_python_dependency_hygiene_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    rows = list_or_empty(payload.get("projects"))
    claims: list[AnalysisClaim] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        audit = dict_or_empty(row.get("audit"))
        advisories = list_or_empty(audit.get("advisories"))
        direct = sum(
            1 for adv in advisories if isinstance(adv, dict) and adv.get("direct")
        )
        transitive = sum(
            1 for adv in advisories if isinstance(adv, dict) and adv.get("transitive")
        )
        observed = sum(
            1
            for adv in advisories
            if isinstance(adv, dict) and adv.get("observed_import")
        )
        manifest = row.get("manifest") or "?"
        observed_external_import_count = int(
            row.get("observed_external_import_count") or 0
        )
        audit_available = bool(audit.get("available"))
        summary = (
            f"{project}: {len(advisories)} PyPI advisories on {manifest} "
            f"({direct} direct / {transitive} transitive; "
            f"{observed} observed in imports; "
            f"audit={'on' if audit_available else 'off'})"
        )
        claims.append(
            AnalysisClaim(
                id=f"python-dep-hygiene-summary:{project}",
                artifact_name=artifact.name,
                claim_type="python_dep_hygiene_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "manifest": manifest,
                    "advisory_count": len(advisories),
                    "direct_advisory_count": direct,
                    "transitive_advisory_count": transitive,
                    "observed_advisory_count": observed,
                    "observed_external_import_count": observed_external_import_count,
                    "observed_external_imports_sample": list_or_empty(
                        row.get("observed_external_imports")
                    )[:20],
                    "audit_available": audit_available,
                    "advisories_sample": advisories[:8],
                },
                confidence=0.60,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


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


def _active_ai_attribution_claims(
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
        commit_count = int(row.get("commit_count") or 0)
        high = int(row.get("high") or 0)
        medium = int(row.get("medium") or 0)
        none = int(row.get("none") or 0)
        ratio = float(row.get("ai_assisted_ratio") or 0.0)
        providers = dict_or_empty(row.get("providers"))
        provider_str = (
            "; providers: "
            + ", ".join(f"{k}={v}" for k, v in sorted(providers.items()))
            if providers
            else ""
        )
        summary = (
            f"{project}: {high}/{medium}/{none} commits high/medium/none AI-attributed "
            f"({ratio:.0%} of {commit_count}){provider_str}"
        )
        claims.append(
            AnalysisClaim(
                id=f"ai-attribution-summary:{project}",
                artifact_name=artifact.name,
                claim_type="ai_attribution_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "commit_count": commit_count,
                    "high": high,
                    "medium": medium,
                    "none": none,
                    "ai_assisted_ratio": ratio,
                    "providers": dict(providers),
                },
                confidence=0.65,
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
