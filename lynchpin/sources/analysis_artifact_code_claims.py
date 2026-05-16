from __future__ import annotations

from typing import Any

from ..core.projects import canonical_project_name
from .analysis_artifact_helpers import dict_or_empty, list_or_empty, string_tuple
from .analysis_artifact_models import AnalysisArtifact, AnalysisClaim


def _active_code_hotspot_claims(
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
        changed = int(row.get("changed_file_count") or 0)
        central = int(row.get("central_files_changed") or 0)
        guardrail = int(row.get("guardrail_files_changed") or 0)
        interpretation = dict_or_empty(row.get("interpretation"))
        claims.append(
            AnalysisClaim(
                id=f"code-hotspot-summary:{project}",
                artifact_name=artifact.name,
                claim_type="code_hotspot_summary",
                project=project,
                summary=(
                    f"{project}: {changed} files changed, {central} central, "
                    f"{guardrail} guardrail — "
                    f"primary category: {interpretation.get('primary_category', 'none')}"
                ),
                payload={
                    "window": window,
                    "changed_file_count": changed,
                    "central_files_changed": central,
                    "guardrail_files_changed": guardrail,
                    "top_path_roots": row.get("top_path_roots"),
                    "quality_gates_detected": row.get("quality_gates_detected"),
                    "interpretation": interpretation,
                    "caveats": row.get("caveats"),
                },
                confidence=0.72,
                generated_at=artifact.generated_at,
            )
        )
        hotspots = list_or_empty(row.get("hotspot_files"))
        for h in hotspots[:5]:
            if not isinstance(h, dict):
                continue
            path = str(h.get("path") or "")
            count = int(h.get("change_count") or 0)
            signals = h.get("signals") or []
            if not signals:
                continue
            claims.append(
                AnalysisClaim(
                    id=f"code-hotspot:{project}:{path}",
                    artifact_name=artifact.name,
                    claim_type="code_hotspot",
                    project=project,
                    summary=f"{project}: {path} changed {count} times [{', '.join(signals)}]",
                    payload={
                        "window": window,
                        "path": path,
                        "change_count": count,
                        "active_days": h.get("active_days"),
                        "central": h.get("central"),
                        "guardrail": h.get("guardrail"),
                        "signals": signals,
                    },
                    confidence=0.66,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_quality_guardrail_claims(
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
        change_count = int(row.get("guardrail_change_count") or 0)
        interpretation = dict_or_empty(row.get("interpretation"))
        holes = list_or_empty(row.get("guardrail_holes"))
        changes_by_type = dict_or_empty(row.get("guardrail_changes_by_type"))
        test_changed = int(row.get("test_files_changed") or 0)
        ci_changed = int(row.get("ci_files_changed") or 0)
        type_changed = int(row.get("type_files_changed") or 0)
        summary = (
            f"{project}: {change_count} guardrail changes "
            f"(test={test_changed}, ci={ci_changed}, type={type_changed}) — "
            f"{'gates present' if interpretation.get('gates_detected') else 'no gates detected'}"
        )
        claims.append(
            AnalysisClaim(
                id=f"quality-guardrail-summary:{project}",
                artifact_name=artifact.name,
                claim_type="quality_guardrail_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "guardrail_change_count": change_count,
                    "guardrail_changes_by_type": changes_by_type,
                    "test_files_changed": test_changed,
                    "ci_files_changed": ci_changed,
                    "type_files_changed": type_changed,
                    "quality_gates": row.get("quality_gates"),
                    "quality_gate_count": row.get("quality_gate_count"),
                    "guardrail_holes": holes,
                    "interpretation": interpretation,
                    "caveats": row.get("caveats"),
                },
                confidence=0.70,
                generated_at=artifact.generated_at,
            )
        )
        if holes:
            for hole in holes[:3]:
                claims.append(
                    AnalysisClaim(
                        id=f"guardrail-hole:{project}:{hash(hole) & 0xFFFF}",
                        artifact_name=artifact.name,
                        claim_type="guardrail_debt",
                        project=project,
                        summary=f"{project}: {hole}",
                        payload={"window": window, "hole": hole},
                        confidence=0.62,
                        generated_at=artifact.generated_at,
                    )
                )
    return tuple(claims)


def _active_code_inventory_claims(
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
        total_lines = int(row.get("total_lines") or 0)
        total_code = int(row.get("total_code_lines") or 0)
        langs = dict_or_empty(row.get("languages") or row.get("language_breakdown"))
        dominant = string_tuple(row.get("dominant_languages"))
        lang_count = len(langs)
        tool = dict_or_empty(row.get("tool_run"))
        tool_available = bool(tool.get("available"))
        dom_str = ", ".join(dominant[:4]) if dominant else "none"
        summary = (
            f"{project}: {total_code:,} code lines ({total_lines:,} total) "
            f"across {lang_count} languages — dominant: {dom_str}"
        )
        claims.append(
            AnalysisClaim(
                id=f"code-inventory-summary:{project}",
                artifact_name=artifact.name,
                claim_type="code_inventory_summary",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "total_lines": total_lines,
                    "total_code_lines": total_code,
                    "language_count": lang_count,
                    "dominant_languages": dominant,
                    "languages": langs,
                    "tool_available": tool_available,
                    "tool_version": tool.get("version"),
                },
                confidence=0.90 if tool_available else 0.40,
                generated_at=artifact.generated_at,
            )
        )
        if langs:
            top_langs = sorted(langs.items(), key=lambda x: -x[1].get("code", 0))[:5]
            lang_summary = ", ".join(
                f"{lang}={stats.get('code', 0):,}" for lang, stats in top_langs
            )
            claims.append(
                AnalysisClaim(
                    id=f"language-inventory:{project}",
                    artifact_name=artifact.name,
                    claim_type="language_inventory",
                    project=project,
                    summary=f"{project}: {lang_summary}",
                    payload={
                        "window": window,
                        "language_count": lang_count,
                        "languages": {
                            lang: {
                                "code": stats.get("code", 0),
                                "comments": stats.get("comments", 0),
                            }
                            for lang, stats in top_langs
                        },
                        "dominant_languages": dominant,
                        "tool_available": tool_available,
                    },
                    confidence=0.82,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_python_complexity_claims(
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
        summary = dict_or_empty(row.get("summary"))
        total_loc = int(summary.get("total_loc") or 0)
        total_funcs = int(summary.get("total_functions") or 0)
        complex_funcs = int(summary.get("complex_functions") or 0)
        parse_error_count = len(list_or_empty(row.get("parse_errors")))
        summary_text = (
            f"{project}: {total_loc:,} loc, {total_funcs} functions "
            f"({complex_funcs} complex by native AST)"
            + (f", {parse_error_count} parse errors" if parse_error_count else "")
        )
        claims.append(
            AnalysisClaim(
                id=f"python-complexity-summary:{project}",
                artifact_name=artifact.name,
                claim_type="python_complexity_summary",
                project=project,
                summary=summary_text,
                payload={
                    "window": window,
                    "total_loc": total_loc,
                    "total_functions": total_funcs,
                    "complex_functions": complex_funcs,
                    "parse_error_count": parse_error_count,
                    "rank_distribution": summary.get("rank_distribution"),
                    "file_count": int(row.get("file_count") or 0),
                    "tool_run": row.get("tool_run"),
                    "methodology": "native AST decision-count approximation",
                },
                confidence=0.82,
                generated_at=artifact.generated_at,
            )
        )
        if complex_funcs > 0:
            claims.append(
                AnalysisClaim(
                    id=f"function-complexity:{project}",
                    artifact_name=artifact.name,
                    claim_type="function_complexity",
                    project=project,
                    summary=(
                        f"{project}: {complex_funcs} complex functions (>10 native AST decision-count), "
                        f"{total_funcs} total"
                    ),
                    payload={
                        "window": window,
                        "complex_function_count": complex_funcs,
                        "total_function_count": total_funcs,
                        "parse_error_count": parse_error_count,
                        "rank_distribution": summary.get("rank_distribution"),
                        "caveat": "native AST complexity is a structural signal, not a quality verdict or radon parity claim",
                    },
                    confidence=0.72,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_python_import_graph_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
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
        module_count = int(row.get("module_count") or 0)
        edge_count = int(row.get("import_edge_count") or 0)
        cycle_count = len(list_or_empty(row.get("cycle_modules")))
        parse_error_count = len(list_or_empty(row.get("parse_errors")))
        summary_text = (
            f"{project}: {module_count} modules, {edge_count} import edges"
            + (f", {cycle_count} cycle modules" if cycle_count else "")
            + (f", {parse_error_count} parse errors" if parse_error_count else "")
        )
        claims.append(
            AnalysisClaim(
                id=f"python-import-graph-summary:{project}",
                artifact_name=artifact.name,
                claim_type="python_import_graph_summary",
                project=project,
                summary=summary_text,
                payload={
                    "module_count": module_count,
                    "import_edge_count": edge_count,
                    "cycle_module_count": cycle_count,
                    "top_fan_out": row.get("top_fan_out"),
                    "top_fan_in": row.get("top_fan_in"),
                    "tool_run": row.get("tool_run"),
                    "parse_error_count": parse_error_count,
                    "methodology": "native AST internal import graph",
                },
                confidence=0.78,
                generated_at=artifact.generated_at,
            )
        )
        cycle_modules = list_or_empty(row.get("cycle_modules"))
        if cycle_modules:
            claims.append(
                AnalysisClaim(
                    id=f"cycle-risk:{project}",
                    artifact_name=artifact.name,
                    claim_type="cycle_risk",
                    project=project,
                    summary=(
                        f"{project}: {len(cycle_modules)} modules in import cycles "
                        f"({', '.join(str(m) for m in cycle_modules[:5])}{'...' if len(cycle_modules) > 5 else ''})"
                    ),
                    payload={
                        "module_count": module_count,
                        "cycle_modules": cycle_modules,
                        "cycle_count": len(cycle_modules),
                        "caveat": "import cycles are structural signals; some may be intentional or benign",
                    },
                    confidence=0.58,
                    generated_at=artifact.generated_at,
                )
            )
    return tuple(claims)


def _active_rust_graph_claims(
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
        status = str(row.get("status") or "available")
        if status != "available":
            claims.append(
                AnalysisClaim(
                    id=f"rust-workspace:{project}",
                    artifact_name=artifact.name,
                    claim_type="rust_workspace_unavailable",
                    project=project,
                    summary=f"{project}: Rust workspace unavailable ({row.get('reason', 'unknown')})",
                    payload={"window": window, "reason": row.get("reason")},
                    confidence=0.85,
                    generated_at=artifact.generated_at,
                )
            )
            continue
        crate_count = int(row.get("workspace_crate_count") or 0)
        edge_count = int(row.get("internal_edge_count") or 0)
        crates = list_or_empty(row.get("crates"))
        high_centrality = [
            c
            for c in crates
            if isinstance(c, dict) and c.get("risk_level") == "high-centrality"
        ]
        active_fringe = [
            c
            for c in crates
            if isinstance(c, dict) and c.get("risk_level") == "active-fringe"
        ]
        summary = (
            f"{project}: {crate_count} workspace crates, {edge_count} internal edges; "
            f"{len(high_centrality)} high-centrality, {len(active_fringe)} active-fringe"
        )
        claims.append(
            AnalysisClaim(
                id=f"rust-workspace-shape:{project}",
                artifact_name=artifact.name,
                claim_type="rust_workspace_shape",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "workspace_crate_count": crate_count,
                    "internal_edge_count": edge_count,
                    "high_centrality_crates": [
                        {
                            "name": c.get("name"),
                            "crate_path": c.get("crate_path"),
                            "in_degree": c.get("in_degree"),
                            "out_degree": c.get("out_degree"),
                            "recent_file_changes": c.get("recent_file_changes"),
                        }
                        for c in high_centrality
                    ],
                    "active_fringe_crates": [
                        {
                            "name": c.get("name"),
                            "crate_path": c.get("crate_path"),
                            "recent_file_changes": c.get("recent_file_changes"),
                        }
                        for c in active_fringe[:5]
                    ],
                    "tool_available": bool(
                        dict_or_empty(row.get("tool_run")).get("available")
                    ),
                },
                confidence=0.78,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)


def _active_commit_semantics_claims(
    artifact: AnalysisArtifact,
    payload: dict[str, Any],
    *,
    selected: set[str],
) -> tuple[AnalysisClaim, ...]:
    window = dict_or_empty(payload.get("window"))
    commits = payload.get("commits")
    if not isinstance(commits, list):
        return ()
    claims: list[AnalysisClaim] = []
    for row in commits[:20]:
        if not isinstance(row, dict):
            continue
        project = canonical_project_name(row.get("project"))
        if project is None or (selected and project not in selected):
            continue
        ops = dict_or_empty(row.get("semantic_operations"))
        impact = dict_or_empty(row.get("impact"))
        risk = list_or_empty(row.get("risk_flags"))
        top_ops = sorted(ops.items(), key=lambda x: -x[1])[:4]
        summary = (
            f"{project}: {row.get('subject', '')[:80]} "
            f"[{', '.join(f'{k}={v:.1f}' for k, v in top_ops)}]"
        )
        claims.append(
            AnalysisClaim(
                id=f"commit-semantics:{row.get('short_sha', '')}",
                artifact_name=artifact.name,
                claim_type="commit_semantic_capsule",
                project=project,
                summary=summary,
                payload={
                    "window": window,
                    "sha": row.get("sha"),
                    "short_sha": row.get("short_sha"),
                    "subject": row.get("subject"),
                    "conventional_kind": row.get("conventional_kind"),
                    "semantic_operations": ops,
                    "impact": impact,
                    "risk_flags": risk,
                    "symbol_count": row.get("symbol_count"),
                    "caveats": row.get("caveats"),
                },
                confidence=0.64,
                generated_at=artifact.generated_at,
            )
        )
    return tuple(claims)
