"""Derived Chisel package and portfolio assembly."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import chisel_build as chisel
from lynchpin.sources.chisel_inventory import CapturedInventory, InventoryFile
from lynchpin.sources.chisel_package import captured_sidecars, run_view, verify_history_bundle
from lynchpin.sources.chisel_metrics import build_metrics
from lynchpin.sources.chisel_context import build_context
from lynchpin.sources.chisel_offline import build_offline_package
from lynchpin.sources.chisel_compact import compact_jsonl
from lynchpin.sources.chisel_excerpts import collect_excerpts
from lynchpin.sources import chisel_options
from .chisel_structure import build_structure, build_portfolio_links


def evidence_outputs(plan: Any, inventory: Any, out_dir: Path, cache_dir: Path,
                     log: list[str] | None = None,
                     *, report_builder: Callable[..., Any] | None = None) -> list[dict]:
    """Derive navigation from captured bytes and owner-exported records."""

    if report_builder is None and "source" in chisel_options.active_options.datasets:
        raise ValueError("Chisel source reports require the analysis-layer report builder")

    items = [item for key, values in (chisel._github_context_index or {}).items()
             if key[0] == plan.name for item in values]
    tracker_dir = out_dir / "trackers"
    tracker_dir.mkdir(exist_ok=True)
    materialization = chisel._github_context_manifest or None
    project_inventory_coverage = (
        (materialization or {}).get("inventory_coverage", {}).get(plan.name, {})
        if isinstance(materialization, dict) else {}
    )
    (tracker_dir / "github-coverage.json").write_text(json.dumps({
        "project": plan.name, "repository": plan.github_slug,
        "exported_records": len(items),
        "inventory_coverage": project_inventory_coverage,
        "materialization": materialization,
        "interpretation": "Exported record counts are observed rows. An inventory at its requested limit may be truncated; its total is unknown. Missing materialization or stale fallback does not establish current zero issues or PRs.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    steps = [
        ("structure", lambda: build_structure(inventory, out_dir, cache_dir=cache_dir / "structure")),
        ("overlay-views", lambda: build_overlay_views(inventory, out_dir, cache_dir, chisel_options.active_options.datasets)),
        ("owner-evidence", lambda: build_context(plan.path, out_dir, project=plan.name,
            revision=inventory.revision, dirty=inventory.dirty, github_items=items,
            github_slug=plan.github_slug)),
        ("context", lambda: collect_excerpts(plan.path, out_dir, chisel_options.active_options)),
        ("reports", lambda: report_builder(out_dir, project=plan.name,
            task_roots=[root for project, root in chisel_options.active_options.task_roots if project == plan.name])),
        ("compact", lambda: compact_jsonl(out_dir)),
        ("offline-index", lambda: build_offline_package(out_dir, project=plan.name,
            snapshot_id=inventory.snapshot_id, generated_at=inventory.generated_at)),
    ]
    timings = []
    for name, run in steps:
        dataset = {"owner-evidence": "execution", "offline-index": "source", "reports": "source", "overlay-views": "structure"}.get(name, name)
        if name == "owner-evidence" and "trackers" in chisel_options.active_options.datasets:
            pass
        elif name == "compact":
            pass
        elif name == "overlay-views" and "metrics" in chisel_options.active_options.datasets:
            pass
        elif dataset not in chisel_options.active_options.datasets:
            continue
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        chisel._set_stage(plan.name, name, True)
        chisel._emit(log, f"  → {plan.name}: {name}")
        try:
            run()
        finally:
            chisel._set_stage(plan.name, name, False)
        elapsed = round(time.perf_counter() - start, 3)
        timings.append({"stage": name, "label": plan.name, "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_s": elapsed, "queue_wait_s": 0.0})
        chisel._emit(log, f"  ✓ {plan.name}: {name} ({elapsed:.1f}s)")
    return timings


def build_portfolio(root: Path, plans: Any, results: dict, generated_at: str) -> str:
    """One extraction exposes every selected project and its exact capture ID."""

    build_portfolio_links(root, plans)
    projects = [{"project": p.name, "snapshot_id": results[p.name]["snapshot_id"],
                 "manifest_sha256": hashlib.sha256(
                     (root / p.name / f"{p.name}-manifest.json").read_bytes()).hexdigest(),
                 "captured_at": json.loads((root / p.name / "capture.json").read_text())["generated_at"],
                 "git": results[p.name]["git"], "path": p.name} for p in plans]
    (root / "portfolio.json").write_text(json.dumps({
        "generated_at": generated_at, "projects": projects,
        "method": "comparisons use each listed capture; capture times may differ",
    }, indent=2) + "\n", encoding="utf-8")
    (root / "START_HERE.md").write_text(
        "# Chisel portfolio\n\nEach project directory contains START_HERE.md, raw source, "
        "evidence tables and a Python standard-library browse.py helper.\n\n"
        "portfolio.json binds the selection to exact snapshot IDs. growth/ contains "
        "comparative measurements with their methods. Project context and documentation "
        "are excluded from maintained code counts. Test source share is not coverage.\n",
        encoding="utf-8",
    )
    (root / "ATTACHMENT_START_HERE.md").write_text(
        "# Chisel attachments\n\nExtract the selected attachments together. "
        "Each archive's navigation and verified contents are listed under `attachments/`. "
        "Those manifests name the actual companion files and snapshot identities. "
        "Numbered parts include their reconstruction requirements under `parts/`; "
        "run `python3 reconstruct.py` after extracting them. Start browsing with "
        "`portfolio.json` and each project's `START_HERE.md`.\n")
    (root / "attachment-profile.json").unlink(missing_ok=True)
    from lynchpin.sources.chisel_attachments import build_attachments

    manifest = build_attachments(root, [p.name for p in plans],
                                 limit=chisel_options.active_options.attachment_bytes,
                                 layout=chisel_options.active_options.attachment_layout)
    return manifest["attachments"][0]["path"] if len(manifest["attachments"]) == 1 else "attachments.json"

def build_overlay_views(primary: CapturedInventory, package: Path, cache: Path, datasets: tuple[str, ...]) -> None:
    """Temporarily assemble overlays using links, retaining only their computed views."""

    for path in sorted((package / "snapshots").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if manifest["snapshot_id"] == primary.snapshot_id:
            continue
        with tempfile.TemporaryDirectory(prefix=".overlay-view-", dir=package.parent) as temporary:
            root = Path(temporary)
            records = tuple(InventoryFile(**row) for row in manifest["files"])
            for record in records:
                if not record.included:
                    continue
                source = (path.parent / "files" if record.path in manifest["changed"] else primary.root) / record.path
                target = root / record.path
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
            inventory = replace(primary, root=root, files=records,
                snapshot_id=manifest["snapshot_id"], revision=manifest["revision"], dirty=manifest["dirty"])
            if "metrics" in datasets:
                build_metrics(inventory, path.parent)
            if "structure" in datasets:
                build_structure(inventory, path.parent, cache_dir=cache / "structure")
