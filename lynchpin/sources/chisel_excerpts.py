"""Bounded project-linked excerpts through the archive owner's public facade."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def collect_excerpts(repo: Path, package: Path, options: Any, *, client: Any = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=options.context_days)
    rows: list[dict[str, Any]] = []
    gaps = []
    used = 0
    notes = package / "trackers/beads-export.jsonl"
    selected_roots = {root for project, root in options.task_roots if project == package.name}
    if notes.exists():
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
            content = str(task["notes"]).encode()[:16_000].decode("utf-8", errors="ignore")
            row = {"owner": "beads", "task_ref": task["id"], "timestamp": stamp.isoformat(),
                   "timestamp_scope": "task update; note creation time unknown",
                   "reference": f"trackers/beads-export.jsonl:{line}", "field": "notes",
                   "selection_reason": "explicit selected-task work note" if selected else "project tracker work note",
                   "text": content, "text_truncated": len(content.encode()) < len(str(task["notes"]).encode()),
                   "origin": "owner_recorded_note; authorship not inferred", "confidence": None}
            size = len((json.dumps(row, ensure_ascii=False) + "\n").encode())
            if len(rows) >= options.context_limit or used + size > options.context_bytes:
                gaps.append("work-note limit reached")
                break
            rows.append(row)
            used += size
    note_rows = rows
    rows = []
    used = 0
    try:
        if client is None:
            from .polylogue_client import _polylogue_client

            client = _polylogue_client()
        linked = []
        tasks_path = package / "owners/tasks.json"
        if tasks_path.exists():
            task_snapshot = json.loads(tasks_path.read_text())
            def collect(value: Any) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"session_ref", "session_refs", "parent_session_ref", "child_session_ref"}:
                            values = item if isinstance(item, list) else [item]
                            linked.extend(v.removeprefix("session:") for v in values if isinstance(v, str) and v.startswith("session:"))
                        elif isinstance(item, (dict, list)):
                            collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
            collect(task_snapshot.get("nodes", []))
        sessions = client.query_sessions(cwd_prefix=str(repo), since=start.isoformat(),
                                         until=now.isoformat(), limit=options.context_limit,
                                         sort="date")
        sessions = [{"id": ref, "selected_task_link": True} for ref in dict.fromkeys(linked)] + sessions
        seen = set()
        for session in sessions:
            if len(rows) >= options.context_limit or used >= options.context_bytes:
                break
            session_id = session.get("id") or session.get("session_id")
            if not session_id or session_id in seen:
                continue
            seen.add(session_id)
            if len(seen) > options.context_limit:
                gaps.append("session acquisition limit reached")
                break
            _, total, _ = client.get_messages_paginated(str(session_id), limit=1, offset=0)
            take = min(10, options.context_limit - len(rows))
            messages, current_total, coverage = client.get_messages_paginated(str(session_id),
                limit=take, offset=max(0, total - take))
            if current_total != total:
                gaps.append(f"session changed during bounded read: {session_id}")
            messages = sorted(messages, key=lambda m: str(getattr(m, "timestamp", "")), reverse=True)
            for message in messages:
                timestamp = getattr(message, "timestamp", None)
                if timestamp is None:
                    continue
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if not start <= timestamp <= now:
                    continue
                content = (getattr(message, "text", None) or "").encode()
                if not content:
                    continue
                content = content[:min(16_000, options.context_bytes - used)]
                row = {"owner": "polylogue", "session_ref": f"session:{session_id}",
                       "message_ref": f"message:{message.id}", "timestamp": timestamp.isoformat(),
                       "selection_reason": "explicit selected-task session link" if session.get("selected_task_link") else "owner-indexed workspace path", "workspace": str(repo),
                       "text": content.decode("utf-8", errors="ignore"),
                       "origin": "original_message", "confidence": None,
                       "author_role": getattr(message, "role", None),
                       "model": getattr(message, "model", None),
                       "summary_status": getattr(message, "is_summary", None),
                       "message_count": total, "window_limit": 10,
                       "text_truncated": len(content) < len((message.text or "").encode()),
                       "owner_coverage": str(coverage)}
                encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode()
                if used + len(encoded) > options.context_bytes:
                    gaps.append("byte limit reached")
                    break
                rows.append(row)
                used += len(encoded)
                if len(rows) >= options.context_limit:
                    break
            if len(rows) >= options.context_limit or "byte limit reached" in gaps:
                break
        if len(sessions) >= options.context_limit:
            gaps.append("session selection reached limit; remaining sessions not read")
    except Exception as exc:
        gaps.append(f"owner excerpt route unavailable: {type(exc).__name__}: {exc}")
    candidates = sorted([*note_rows, *rows], key=lambda row: (
        "selected-task" in row["selection_reason"], row["timestamp"]), reverse=True)
    rows = []
    used = 0
    for row in candidates:
        size = len((json.dumps(row, ensure_ascii=False) + "\n").encode())
        if len(rows) >= options.context_limit or used + size > options.context_bytes:
            gaps.append("combined excerpt limit reached")
            break
        rows.append(row)
        used += size
    output = package / "context"
    output.mkdir(exist_ok=True)
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (output / "excerpts.jsonl").write_text(data)
    report = {"owner": "polylogue", "interface": "SyncPolylogue.query_sessions/get_messages_paginated",
              "observed_at": now.isoformat(), "start": start.isoformat(), "end": now.isoformat(),
              "limit": options.context_limit, "byte_limit": options.context_bytes,
              "bytes": len(data.encode()), "excerpts": len(rows),
              "observation_sha256": hashlib.sha256(data.encode()).hexdigest(),
              "coverage": "bounded" if rows else "unavailable" if gaps else "empty_owner_selection",
              "gaps": gaps + ["Work notes are bounded tracker notes; journal exports are not queried.",
                              "Only the latest ten messages per selected session are sampled."]}
    (output / "coverage.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
