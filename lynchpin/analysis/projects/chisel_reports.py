"""Deterministic joins over frozen Chisel inputs; no owner acquisition here."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import tomllib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from lynchpin.sources.chisel_warnings import parse_python_source


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def _dataset_coverage(path: Path) -> dict[str, Any]:
    """Report whether a captured dataset was absent, empty, or populated."""
    if not path.is_file():
        return {"status": "unavailable", "records": None}
    count = len(rows(path)) if path.suffix in {".jsonl", ".ndjson"} else None
    return {"status": "empty" if count == 0 else "available", "records": count}


def task_graph(tasks: list[dict[str, Any]], roots: list[str]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in tasks if "id" in r}
    edges = {key: sorted({str(e["depends_on_id"]) for e in row.get("dependencies", [])
                         if isinstance(e, dict) and e.get("type", "blocks") == "blocks" and e.get("depends_on_id")})
             for key, row in by_id.items()}
    paths: dict[str, list[str]] = {}
    pending = deque((root, [root]) for root in roots)
    while pending:
        key, path = pending.popleft()
        if key in paths:
            continue
        paths[key] = path
        pending.extend((child, [*path, child]) for child in edges.get(key, []))
    cycles: list[list[str]] = []
    visited: set[str] = set()

    def walk(key: str, path: list[str]) -> None:
        if key in path:
            cycles.append(path[path.index(key):] + [key])
            return
        if key in visited:
            return
        visited.add(key)
        for child in edges.get(key, []):
            walk(child, [*path, key])

    for key in paths:
        walk(key, [])
    missing = sorted(paths.keys() - by_id.keys())
    unfinished = sorted(k for k in paths if k in by_id and by_id[k].get("status") != "closed")
    disagreements = []
    for task in tasks:
        metadata = task.get("metadata") or {}
        if not isinstance(metadata, dict) or metadata.get("phase") is None:
            continue
        for field in ("description", "notes", "acceptance_criteria"):
            for match in re.finditer(r"(?im)^\s*(?:#+\s*)?phase\s*[:= ]\s*([\w.-]+)[^\n]*", str(task.get(field) or "")):
                if str(metadata["phase"]).casefold() != match.group(1).casefold():
                    disagreements.append({"task": task.get("id"), "metadata_phase": metadata["phase"],
                        "field": field, "excerpt": match.group(0), "status": "possible_disagreement; interpretation requires review"})
    return {"roots": roots, "coverage": "partial" if missing else "exported_graph",
            "rooted_analysis_status": "unconfigured" if not roots else "partial" if missing else "computed",
            "missing_nodes": missing, "cycles": cycles,
            "unfinished_leaves": [k for k in unfinished if not edges.get(k)],
            "shortest_blocking_paths": {k: paths[k] for k in unfinished},
            "campaign_count": len(paths) if roots and not missing else None,
            "possible_phase_disagreements": disagreements,
            "edges": [{"task": key, "blocker": child} for key, children in edges.items() for child in children]}


def declarations(root: Path, snapshot: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output, references = [], []
    for path in root.rglob("*.py"):
        try:
            # Structure metrics record this file's parse warnings.
            tree, _warnings = parse_python_source(path.read_text(), path.relative_to(root).as_posix())
        except (UnicodeError, SyntaxError):
            continue
        relative = path.relative_to(root).as_posix()
        names: dict[str, list[int]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.setdefault(node.name, []).append(node.lineno)
                kind = "test" if node.name.startswith("test_") else "declaration"
                decorators = [ast.unparse(d) for d in node.decorator_list]
                if any("command(" in d or "route(" in d for d in decorators):
                    kind = "command_or_handler"
                output.append({"snapshot_id": snapshot, "path": relative, "line": node.lineno,
                               "name": node.name, "kind": kind, "decorators": decorators,
                               "method": "python_ast_declaration"})
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        output.append({"snapshot_id": snapshot, "path": relative, "line": node.lineno,
                                       "name": target.id, "kind": "dictionary_registry_candidate",
                                       "method": "python_ast_literal_dictionary", "status": "candidate"})
                        for key, value in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                output.append({"snapshot_id": snapshot, "path": relative,
                                    "line": getattr(value, "lineno", node.lineno), "name": target.id,
                                    "kind": "registry_member", "selector": key.value,
                                    "target": ast.unparse(value), "method": "python_ast_dictionary_member",
                                    "status": "declared_expression"})
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
                references.append({"snapshot_id": snapshot, "path": relative, "line": node.lineno,
                                   "name": node.id, "candidate_lines": names[node.id],
                                   "status": "ambiguous" if len(names[node.id]) > 1 else "same_file_candidate",
                                   "method": "python_ast_name_match; shadowing not resolved"})
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".rs", ".nix", ".toml", ".sql", ".py"}:
            continue
        try:
            text = path.read_text()
        except UnicodeError:
            continue
        relative = path.relative_to(root).as_posix()
        patterns = [("schema_table", r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)"),
                    ("data_reader", r"\bFROM\s+([A-Za-z_][\w.]*)"),
                    ("data_writer", r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][\w.]*)")]
        if path.suffix == ".nix":
            patterns += [("service_declaration", r"\b(?:systemd\.(?:user\.)?services|services)\.([\w-]+)")]
        if path.suffix == ".rs":
            patterns += [("rust_function", r"\b(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
                         ("rust_type", r"\b(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)")]
        for kind, pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I if path.suffix == ".sql" else 0):
                output.append({"snapshot_id": snapshot, "path": relative,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "name": match.group(1), "kind": kind, "status": "textual_candidate",
                    "method": "bounded declaration pattern; comments and strings may match"})
        if relative == ".agentctl/project.toml":
            try:
                document = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            for name, operation in document.get("operations", {}).items():
                line = next((i for i, value in enumerate(text.splitlines(), 1)
                             if value.strip() == f"[operations.{name}]"), None)
                output.append({"snapshot_id": snapshot, "path": relative, "line": line,
                    "name": name, "kind": "declared_operation", "argv": operation.get("exec"),
                    "pool": operation.get("pool"), "result_kind": operation.get("result"),
                    "status": "declared", "method": "agentctl_project_toml"})
    return output, references


def changed_symbols(old: Path, new: Path) -> list[dict[str, Any]]:
    def extract(path: Path) -> dict[str, str]:
        if not path.exists() or path.suffix != ".py":
            return {}
        try:
            tree, _warnings = parse_python_source(path.read_text(), path.name)
        except (UnicodeError, SyntaxError):
            return {}
        result = {}
        def visit(node: ast.AST, prefix: str = "") -> None:
            for child in ast.iter_child_nodes(node):
                name = prefix
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}.{child.name}".strip(".")
                    result[name] = ast.dump(child, include_attributes=False)
                visit(child, name)
        visit(tree)
        return result
    before, after = extract(old), extract(new)
    return [{"name": name, "kind": "added" if name not in before else "deleted" if name not in after else "modified",
             "method": "Python AST comparison without source coordinates"}
            for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]


def content_match(files: list[dict[str, Any]], receipt: dict[str, Any] | None) -> dict[str, Any]:
    """Compare only observed endpoint content; never turn it into acceptance."""
    receipt = receipt or {}
    start, end = receipt.get("start") or {}, receipt.get("end") or {}
    before, after = start.get("content_manifest") or {}, end.get("content_manifest") or {}
    if not before or not after:
        return {"complete_scope_match": None, "measured_paths": 0, "reason": "owner content endpoints unavailable"}
    captured = {r["path"]: r for r in files if r.get("included")}
    compared, mismatches, missing = 0, [], []
    for row in before.get("files", []):
        path = row["path"]
        actual = captured.get(path)
        if row.get("kind") == "absent":
            if actual:
                mismatches.append(path)
            continue
        if actual is None:
            missing.append(path)
            continue
        digest = actual.get("sha256")
        if row.get("kind") == "symlink" and actual.get("symlink_target") is not None:
            digest = hashlib.sha256(actual["symlink_target"].encode()).hexdigest()
        compared += 1
        if digest != row.get("sha256") or row.get("kind") != "symlink" and actual.get("mode") != row.get("mode"):
            mismatches.append(path)
    scope_complete = before.get("coverage") == after.get("coverage") == "complete_declared_scope" and not missing
    endpoints_same = before.get("sha256") == after.get("sha256")
    return {"complete_scope_match": False if mismatches or not endpoints_same else True if scope_complete else None,
            "measured_paths": compared, "mismatched_paths": mismatches, "uncaptured_owner_paths": missing,
            "owner_scope": before.get("scope"), "owner_endpoints_same": endpoints_same,
            "immutable_execution_attestation": receipt.get("immutable_execution_attestation"),
            "interpretation": "Endpoint content match does not establish interval immutability or acceptance."}


def _owner_receipt(package: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Expand only digest-addressed manifests needed for a snapshot comparison."""
    result = dict(receipt)
    for endpoint in ("start", "end"):
        row = result.get(endpoint)
        if not isinstance(row, dict) or "content_manifest" in row:
            continue
        ref = row.get("content_manifest_ref")
        if not isinstance(ref, str) or not ref.startswith("sha256:"):
            continue
        digest = ref.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            continue
        path = package / "owners/content-manifests" / f"{digest}.json"
        if not path.is_file():
            continue
        raw = path.read_bytes().rstrip(b"\n")
        if hashlib.sha256(raw).hexdigest() != digest:
            continue
        result[endpoint] = {**row, "content_manifest": json.loads(raw)}
    return result


def _authored_acceptance(
    tasks: list[dict[str, Any]], record: dict[str, Any], checks: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Bind a criterion only when its authored version and execution match exactly."""
    source = record.get("source_record") or {}
    result = source.get("worker_result") or {}
    for task_binding in source.get("task_snapshot") or []:
        if not isinstance(task_binding, dict):
            continue
        task_id = task_binding.get("id")
        authored = next((task for task in tasks if task.get("id") == task_id), None)
        binding = task_binding.get("evidence_binding") or {}
        if not authored or binding.get("v2_available") is not True:
            continue
        metadata = authored.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                continue
        if not isinstance(metadata, dict):
            continue
        criteria = authored.get("acceptance_criteria")
        if not isinstance(criteria, list):
            criteria = metadata.get("acceptance_criteria")
        if not isinstance(criteria, list):
            continue
        claimed = next((entry for entry in result.get("beads") or []
                        if isinstance(entry, dict) and entry.get("id") == task_id), None)
        if not claimed or str(claimed.get("bead_revision")) != str(binding.get("bead_revision")):
            continue
        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            criterion_id = criterion.get("id")
            version = criterion.get("revision")
            expectation = criterion.get("verification") or {}
            if not criterion_id or not isinstance(version, str) or not version or not isinstance(expectation, dict):
                continue
            selector = expectation.get("selector")
            workload = expectation.get("workload")
            if not isinstance(selector, list) or not selector or not all(isinstance(x, str) for x in selector) or not isinstance(workload, str) or not workload:
                continue
            matching_claim = any(isinstance(row, dict) and row.get("id", row.get("ac_id")) == criterion_id
                                 and row.get("revision") == version and row.get("text") == criterion.get("text")
                                 and row.get("status") == "satisfied"
                                 for row in claimed.get("criteria") or [])
            matching_binding = any(isinstance(row, dict) and row.get("id", row.get("ac_id")) == criterion_id
                                   and row.get("revision") == version and row.get("text") == criterion.get("text")
                                   for row in binding.get("criteria") or [])
            if not matching_claim or not matching_binding:
                continue
            for check, comparison in zip(checks, comparisons):
                observation = check.get("owner_observation") or {}
                execution = observation.get("execution_evidence") or {}
                coverage = check.get("criterion_ids") or []
                if (comparison.get("complete_scope_match") is True
                    and criterion_id in coverage
                    and check.get("tested_revision") == record.get("candidate_revision")
                    and check.get("claimed_outcome") == "passed"
                    and isinstance(check.get("receipt"), str) and bool(check["receipt"])
                    and check["receipt"] == observation.get("reference")
                    and observation.get("checked") is True
                    and observation.get("eligible") is True
                    and observation.get("phase") in {"succeeded", "passed"}
                    and observation.get("exit_code") == 0
                    and execution.get("command_execution_observed") is True
                    and execution.get("selector") == selector
                    and execution.get("workload") == workload):
                    return {"state": "criterion_receipt_bound", "task_id": task_id,
                            "criterion_id": criterion_id, "criterion_version": version,
                            "selector": selector, "workload": workload,
                            "receipt": check.get("receipt")}
    return None


def build_reports(package: Path, *, project: str, task_roots: list[str]) -> dict[str, Any]:
    capture = json.loads((package / "capture.json").read_text())
    snapshot = capture["snapshot_id"]
    out = package / "report"
    out.mkdir(exist_ok=True)
    tasks = rows(package / "trackers/beads-export.jsonl")
    graph = task_graph(tasks, task_roots)
    if task_roots and (package / "owners/tasks.json").exists():
        from .campaign import campaign_evidence, campaign_scope_delta

        frozen = {name: json.loads((package / f"owners/{name}.json").read_text())
                  for name in ("tasks", "batches", "native")}
        owner_project = frozen["tasks"].get("project_id", project)
        report = campaign_evidence(project=owner_project,
            bead_refs=[f"sinnix://projects/{owner_project}/beads/{root}" for root in task_roots],
            task_snapshot=frozen["tasks"], runtime_snapshot=frozen["batches"],
            native_evidence_snapshot=frozen["native"],
            session_snapshot={"coverage": "unavailable", "sessions": [], "gaps": ["not acquired"]})
        (out / "campaign-evidence.json").write_text(json.dumps(report, indent=2) + "\n")
        baseline = package / "owners/task-baseline.json"
        delta = campaign_scope_delta(json.loads(baseline.read_text()), frozen["tasks"]) if baseline.exists() else {
            "coverage": "unavailable", "reason": "no prior selected-task snapshot; no scope changes inferred"}
        (out / "campaign-scope-delta.json").write_text(json.dumps(delta, indent=2) + "\n")
    edges = rows(package / "structure/dependency_edges.jsonl")
    symbols = rows(package / "structure/symbols.jsonl")
    differences = []
    integration = []
    base = {r["path"]: r for r in rows(package / "inventory.jsonl") if r.get("included")}
    base_by_hash: dict[str, list[str]] = defaultdict(list)
    symbols_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edges_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path, record in base.items():
        if record.get("sha256"):
            base_by_hash[record["sha256"]].append(path)
    for symbol in symbols:
        symbols_by_path[symbol.get("path", "")].append(symbol)
    for edge in edges:
        edges_by_path[edge.get("source_path", "")].append(edge)
    for path in sorted((package / "snapshots").glob("*/manifest.json")):
        overlay = json.loads(path.read_text())
        files = {r["path"]: r for r in overlay["files"] if r.get("included")}
        deleted = overlay["deleted"]
        deleted_by_hash: dict[str, list[str]] = defaultdict(list)
        for deleted_path in deleted:
            old_record = base.get(deleted_path)
            if old_record and old_record.get("sha256"):
                deleted_by_hash[old_record["sha256"]].append(deleted_path)
        for changed in overlay["changed"] + deleted:
            old, new = base.get(changed), files.get(changed)
            rename_candidates = deleted_by_hash.get(new.get("sha256"), []) if new else []
            differences.append({"snapshot_id": snapshot, "other_snapshot_id": overlay["snapshot_id"],
                "snapshot": path.parent.name, "path": changed,
                "kind": "deleted" if new is None else "added" if old is None else "modified",
                "old_sha256": old.get("sha256") if old else None,
                "new_sha256": new.get("sha256") if new else None,
                "exact_content_rename_candidates": rename_candidates,
                "affected_symbols": symbols_by_path.get(changed, []),
                "changed_symbols": changed_symbols(package / "source" / changed, path.parent / "files" / changed),
                "static_neighbors": edges_by_path.get(changed, [])})
            if new:
                matches = base_by_hash.get(new.get("sha256"), [])
                integration.append({"snapshot_id": overlay["snapshot_id"], "base_snapshot_id": snapshot,
                    "path": changed, "exact_content_matches": matches,
                    "superseded": None, "retired": None,
                    "method": "SHA-256 equality; owner disposition required for supersession"})
    worktree = package / "snapshots/worktree"
    for path in sorted((package / "snapshots").glob("candidate-*/manifest.json")):
        candidate = json.loads(path.read_text())
        work_manifest = json.loads((worktree / "manifest.json").read_text())
        for changed in set(candidate["changed"]) & set(work_manifest["changed"]):
            left, right = worktree / "files" / changed, path.parent / "files" / changed
            if max(left.stat().st_size, right.stat().st_size) > 1_000_000:
                continue
            try:
                a, b = left.read_text(), right.read_text()
                baseline = (package / "source" / changed).read_text() if (package / "source" / changed).exists() else ""
            except UnicodeError:
                continue
            def patch_id(text: str) -> str:
                lines = difflib.unified_diff(baseline.splitlines(), text.splitlines(), n=0)
                changes = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
                return hashlib.sha256("\n".join(changes).encode()).hexdigest()
            integration.append({"snapshot_id": work_manifest["snapshot_id"], "other_snapshot_id": candidate["snapshot_id"],
                "path": changed, "equivalent_text_patch": patch_id(a) == patch_id(b),
                "similarity": difflib.SequenceMatcher(None, a, b, autojunk=True).ratio(),
                "superseded": None, "retired": None,
                "method": "same-base added/deleted line comparison and text similarity; review candidates only"})
    ops, refs = declarations(package / "source", snapshot)
    for record in [*ops, *refs]:
        classified = base.get(record["path"], {})
        record.update({key: classified.get(key) for key in ("purpose", "material", "component")})
    verification = rows(package / "verification/records.jsonl")
    catalogue = json.loads((package / "snapshots.json").read_text()) if (package / "snapshots.json").exists() else {"snapshots": [{"snapshot_id": snapshot, "revision": capture.get("revision"), "dirty": capture.get("dirty")}]}
    by_snapshot = {snapshot: list(base.values())}
    for path in (package / "snapshots").glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        by_snapshot[manifest["snapshot_id"]] = manifest["files"]
    candidates = []
    native_records = [r for r in verification if r.get("kind") == "native_evidence"]
    owner_jobs = package / "owners/jobs.json"
    execution_details = (json.loads(owner_jobs.read_text()).get("details") or []) if owner_jobs.is_file() else [
        r for r in verification if r.get("kind") == "agentctl_job_execution"]
    owner_detail_coverage = (
        json.loads(owner_jobs.read_text()).get("detail_coverage")
        if owner_jobs.is_file() else None
    )
    for selected in catalogue["snapshots"]:
        selected_id = selected.get("snapshot_id")
        selected_revision = selected.get("revision")
        if not selected_id or not selected_revision or selected_id not in by_snapshot:
            continue
        for record in native_records:
            if not record.get("evidence_id") or record.get("candidate_revision") != selected_revision:
                continue
            checks = record.get("verification") or []
            comparisons = [content_match(by_snapshot.get(selected_id, []),
                (check.get("owner_observation") or {}).get("execution_receipt"))
                for check in checks]
            bound_methods = []
            for check, comparison in zip(checks, comparisons):
                observed = check.get("owner_observation") or {}
                receipt = observed.get("execution_receipt") or {}
                if observed.get("eligible") is not True or check.get("tested_revision") != selected_revision:
                    continue
                if comparison["complete_scope_match"] is True:
                    bound_methods.append("complete_scope_endpoint_content")
                elif comparison.get("reason") == "owner content endpoints unavailable" and selected.get("dirty") is False and all(
                    isinstance(receipt.get(endpoint), dict)
                    and receipt[endpoint].get("head") == selected_revision
                    and receipt[endpoint].get("dirty") is False
                    for endpoint in ("start", "end")
                ):
                    bound_methods.append("clean_execution_endpoints")
            if not bound_methods:
                continue
            candidates.append({"snapshot_id": selected_id,
                "snapshot_revision": selected_revision,
                "snapshot_dirty": selected.get("dirty"),
                "evidence_id": record["evidence_id"],
                "association": bound_methods[0],
                "revision_match": True,
                "integration": (record.get("source_record") or {}).get("publication"),
                "focused_tests": checks, "content_comparisons": comparisons,
                "qualification": None,
                "acceptance": _authored_acceptance(tasks, record, checks, comparisons),
                "deployment": None,
                "interpretation": "Eligible execution endpoints associate this evidence with the snapshot; endpoints alone do not establish immutable execution or acceptance."})
        for detail in execution_details:
            receipt = detail.get("execution_receipt") or {}
            start, end = receipt.get("start") or {}, receipt.get("end") or {}
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            if start.get("head") != selected_revision or end.get("head") != selected_revision:
                continue
            comparison = content_match(by_snapshot[selected_id], _owner_receipt(package, receipt))
            if comparison["complete_scope_match"] is True:
                method = "complete_scope_endpoint_content"
            elif (comparison.get("reason") == "owner content endpoints unavailable" and selected.get("dirty") is False
                  and start.get("dirty") is False and end.get("dirty") is False):
                method = "clean_execution_endpoints"
            else:
                continue
            candidates.append({
                "snapshot_id": selected_id, "snapshot_revision": selected_revision,
                "snapshot_dirty": selected.get("dirty"),
                "evidence_id": detail.get("reference") or detail.get("source_id"),
                "evidence_kind": "agentctl_job_execution", "association": method,
                "revision_match": True, "integration": None,
                "focused_tests": [], "qualification": None, "acceptance": None,
                "deployment": None, "content_comparisons": [comparison],
                "execution": {"operation": detail.get("operation"),
                              "phase": detail.get("phase"), "result": detail.get("result"),
                              "exit_code": detail.get("exit_code"),
                              "execution_evidence": detail.get("execution_evidence"),
                              "artifact_refs": detail.get("artifact_refs"),
                              "started_at": detail.get("started_at"),
                              "ended_at": detail.get("ended_at")},
                "interpretation": "Owner job execution is associated by endpoint content; its result is not acceptance, and endpoints do not attest to an immutable execution interval.",
            })
    bound_details = {
        row["evidence_id"] for row in candidates
        if row.get("evidence_kind") == "agentctl_job_execution"
    }
    unbound_details = []
    for detail in execution_details:
        evidence_id = detail.get("reference") or detail.get("source_id")
        if evidence_id in bound_details:
            continue
        receipt = detail.get("execution_receipt") or {}
        start, end = receipt.get("start") or {}, receipt.get("end") or {}
        if not isinstance(start, dict) or not isinstance(end, dict) or not start or not end:
            reason = "execution_endpoints_unavailable"
            comparisons = []
            reason_comparisons = comparisons
        else:
            matching = [selected for selected in catalogue["snapshots"]
                        if selected.get("snapshot_id") in by_snapshot
                        and selected.get("revision") == start.get("head") == end.get("head")]
            comparisons = [content_match(by_snapshot[selected["snapshot_id"]],
                                         _owner_receipt(package, receipt)) for selected in matching]
            closest = [row for row in comparisons if not row.get("mismatched_paths")
                       and not row.get("uncaptured_owner_paths")]
            reason_comparisons = closest or comparisons
            if not matching:
                reason = "captured_revision_mismatch"
            elif any(row.get("mismatched_paths") for row in reason_comparisons):
                reason = "endpoint_content_mismatch"
            elif any(row.get("uncaptured_owner_paths") for row in reason_comparisons):
                reason = "owner_paths_absent_from_capture"
            elif any(row.get("owner_endpoints_same") is False for row in reason_comparisons):
                reason = "execution_endpoints_changed"
            elif all(row.get("complete_scope_match") is None for row in reason_comparisons):
                reason = "complete_content_scope_unavailable"
            else:
                reason = "candidate_binding_unestablished"
        unbound_details.append({
            "evidence_id": evidence_id,
            "reason": reason,
            "observed_head": start.get("head") if isinstance(start, dict) else None,
            "mismatched_path_count": max((len(row.get("mismatched_paths", [])) for row in reason_comparisons), default=0),
            "uncaptured_owner_path_count": max((len(row.get("uncaptured_owner_paths", [])) for row in reason_comparisons), default=0),
        })
    unbound_reasons = {reason: sum(row["reason"] == reason for row in unbound_details)
                       for reason in sorted({row["reason"] for row in unbound_details})}
    activation_path = package / "verification/activation.json"
    if activation_path.exists():
        activation = json.loads(activation_path.read_text())
        for candidate in candidates:
            observed = [r for r in activation.get("records", [])
                        if r.get("sinnix_revision") == candidate.get("snapshot_revision")]
            if observed:
                candidate["deployment"] = {"state": "recorded_activation", "observations": observed,
                    "source": "verification/activation.json", "content_binding": "revision association only",
                    "installed": None, "running": None}
    for name, records in (("snapshot-differences", differences), ("operations", ops),
                          ("references", refs), ("candidate-evidence", candidates),
                          ("preserved-work-integration", integration)):
        (out / f"{name}.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records))
    (out / "task-dependencies.json").write_text(json.dumps({"snapshot_id": snapshot, **graph}, indent=2) + "\n")
    coverage = {"schema_version": 3, "snapshot_id": snapshot,
        "task_roots": task_roots, "campaign_scope": "explicit roots only",
        "dataset_coverage": {
            name: _dataset_coverage(package / name)
            for name in (
                "inventory.jsonl",
                "trackers/beads-export.jsonl",
                "structure/dependency_edges.jsonl",
                "structure/symbols.jsonl",
                "verification/records.jsonl",
                "owners/jobs.json",
                "owners/tasks.json",
            )
        },
        "candidate_evidence": {
            "bound_rows": len(candidates) if (
                (package / "verification/records.jsonl").is_file() or owner_jobs.is_file()
            ) else None,
            "native_records": len(native_records) if (package / "verification/records.jsonl").is_file() else None,
            "detailed_job_records": len(execution_details) if (
                owner_jobs.is_file() or (package / "verification/records.jsonl").is_file()
            ) else None,
            "bound_detailed_jobs": sum(r.get("evidence_kind") == "agentctl_job_execution" for r in candidates)
            if (owner_jobs.is_file() or (package / "verification/records.jsonl").is_file()) else None,
            "owner_detail_selection": owner_detail_coverage,
            "unbound_detail_reasons": unbound_reasons,
            "unbound_detail_examples": unbound_details[:24],
            "unbound_native_records": len(native_records) - len({
                r["evidence_id"] for r in candidates
                if r.get("evidence_kind") != "agentctl_job_execution"
            }) if (package / "verification/records.jsonl").is_file() else None,
            "lifecycle_observations_kept_separate": sum(
                r.get("kind") == "agentctl_job_observation" for r in verification
            ) if (package / "verification/records.jsonl").is_file() else None,
            "status": (
                "unavailable" if not verification and not execution_details
                and not (package / "verification/records.jsonl").is_file() and not owner_jobs.is_file()
                else "partial_coverage" if not (package / "verification/records.jsonl").is_file()
                or not owner_jobs.is_file()
                else "no_bound_evidence" if not candidates
                else "bounded_associations"
            ),
        },
        "gaps": ["Rust, SQL and Nix pattern matches retain textual-candidate status.",
                 "Symbol references are conservative same-file candidates.",
                 "Text patch equivalence does not establish semantic equivalence or supersession.",
                 "Changed-file symbols are affected candidates, not proof each symbol changed."]}
    (out / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    return coverage
