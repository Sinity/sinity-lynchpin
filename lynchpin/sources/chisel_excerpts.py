"""Project-linked work notes from the tracker export."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def collect_excerpts(package: Path, options: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=options.context_days)
    rows: list[dict[str, Any]] = []
    gaps = []
    used = 0
    notes = package / "trackers/beads-export.jsonl"
    selected_roots = {root for project, root in options.task_roots if project == package.name}
    if not notes.exists():
        gaps.append("tracker export is unavailable")
    else:
        candidates = []
        for line, text in enumerate(notes.read_text().splitlines(), 1):
            task = json.loads(text)
            if not task.get("notes") or not task.get("updated_at"):
                continue
            try:
                stamp = datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            except ValueError:
                continue
            if start <= stamp <= now:
                candidates.append((task["id"] in selected_roots, stamp, line, task))
        for selected, stamp, line, task in sorted(candidates, key=lambda r: (r[0], r[1]), reverse=True):
            content = str(task["notes"])
            row = {"owner": "beads", "task_ref": task["id"], "timestamp": stamp.isoformat(),
                   "timestamp_scope": "task update; note creation time unknown",
                   "reference": f"trackers/beads-export.jsonl:{line}", "field": "notes",
                   "selection_reason": "explicit selected-task work note" if selected else "project tracker work note",
                   "text": content, "text_truncated": False,
                   "origin": "owner_recorded_note; authorship not inferred", "confidence": None}
            size = len((json.dumps(row, ensure_ascii=False) + "\n").encode())
            if len(rows) >= options.context_limit or used + size > options.context_bytes:
                gaps.append("work-note limit reached")
                break
            rows.append(row)
            used += size
    output = package / "context"
    output.mkdir(exist_ok=True)
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (output / "excerpts.jsonl").write_text(data)
    report = {"owner": "beads", "interface": "tracker notes",
              "observed_at": now.isoformat(), "start": start.isoformat(), "end": now.isoformat(),
              "limit": options.context_limit, "byte_limit": options.context_bytes,
              "bytes": len(data.encode()), "excerpts": len(rows),
              "observation_sha256": hashlib.sha256(data.encode()).hexdigest(),
              "coverage": "bounded" if rows else "unavailable" if gaps else "empty_tracker_selection",
              "gaps": gaps + ["Work notes are bounded tracker notes; journal exports are not queried."]}
    (output / "coverage.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
