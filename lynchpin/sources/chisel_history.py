"""Offline, revision-bound Git history evidence for Chisel packages.

This module records repository facts. It does not infer effort, quality, or
causation from commit history. Paths are kept as JSON strings after Git's
NUL-delimited formats have been decoded with surrogate escapes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from lynchpin.core.primitives import logical_date
from lynchpin.sources.chisel_inventory import POLICY_VERSION, classify_role

_FORMAT_VERSION = "chisel-history-v3"
_COMMIT_FORMAT = "%x1e%H%x1f%P%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%s%x00"
_DIRECT_REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:#\d+|[A-Za-z][A-Za-z0-9]+-[A-Za-z0-9]+)(?![A-Za-z0-9])")


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if check and result.returncode:
        raise RuntimeError(
            f"git {' '.join(args[:3])} failed ({result.returncode}): "
            + result.stderr.decode("utf-8", "replace").strip()
        )
    return result.stdout


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _batches(values: list[str], size: int = 128) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _role(path: str, project: str) -> str:
    classified = classify_role(path, project=project)
    return classified[0] if isinstance(classified, tuple) else classified


def _jsonl(path: Path, rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def _resolve_revision(repo: Path, revision: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()


def _refs(repo: Path) -> list[dict[str, str]]:
    raw = _git(
        repo,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(upstream)%00",
    )
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        part = line.split(b"\0")
        if part and part[-1] == b"":
            part.pop()
        if len(part) == 4:
            rows.append(dict(zip(("name", "object", "type", "upstream"), map(_decode, part))))
    return sorted(rows, key=lambda row: row["name"])


def _status(repo: Path) -> tuple[bool, bool, int]:
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    staged = unstaged = False
    count = 0
    fields = raw.split(b"\0")
    i = 0
    while i < len(fields) and fields[i]:
        record = fields[i]
        i += 1
        count += 1
        if len(record) >= 2:
            staged |= record[0:1] not in (b" ", b"?")
            unstaged |= record[1:2] not in (b" ", b"?")
        # Porcelain -z emits a second path for rename/copy records.
        if record[:2] and (b"R" in record[:2] or b"C" in record[:2]) and i < len(fields):
            i += 1
    return staged, unstaged, count


def _dirty_patch_fingerprints(history_dir: Path) -> dict[str, str]:
    """Hash captured diffs so coherence checks retain no diff text in errors."""
    return {
        name: hashlib.sha256((history_dir / f"{name}.patch").read_bytes()).hexdigest()
        for name in ("staged", "unstaged")
    }


def _coherence_reasons(
    start_head: str,
    end_head: str,
    start_actual_head: str,
    end_actual_head: str,
    start_refs: list[dict[str, str]],
    end_refs: list[dict[str, str]],
    start_status: tuple[bool, bool, int],
    end_status: tuple[bool, bool, int],
    start_dirty: dict[str, str],
    end_dirty: dict[str, str],
) -> list[str]:
    """Return safe, useful differences without exposing paths or diff bodies."""
    reasons: list[str] = []
    if start_head != end_head:
        reasons.append("requested revision moved")
    if start_actual_head != end_actual_head:
        reasons.append("HEAD moved")
    before = {row["name"]: row for row in start_refs}
    after = {row["name"]: row for row in end_refs}
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(name for name in before.keys() & after.keys() if before[name] != after[name])
    if added or removed or changed:
        reasons.append(
            "refs changed "
            f"(added={len(added)}, removed={len(removed)}, moved={len(changed)})"
        )
    if start_status != end_status:
        reasons.append(
            "tracked status changed "
            f"(before staged/unstaged/paths={start_status[0]}/{start_status[1]}/{start_status[2]}, "
            f"after={end_status[0]}/{end_status[1]}/{end_status[2]})"
        )
    changed_patches = [name for name in ("staged", "unstaged") if start_dirty[name] != end_dirty[name]]
    if changed_patches:
        reasons.append("staged/unstaged patch fingerprint changed (" + ", ".join(changed_patches) + ")")
    return reasons


def _parse_numstat(raw: bytes, project: str) -> list[dict[str, Any]]:
    """Parse `--numstat -z`, including NUL-separated rename source/dest paths."""
    out: list[dict[str, Any]] = []
    fields = raw.split(b"\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if not field:
            continue
        bits = field.lstrip(b"\n").split(b"\t", 2)
        if len(bits) != 3:
            continue
        try:
            added = None if bits[0] == b"-" else int(bits[0])
            deleted = None if bits[1] == b"-" else int(bits[1])
        except ValueError:
            continue
        if bits[2]:
            old_path = path = _decode(bits[2])
        else:
            old_path = _decode(fields[i]) if i < len(fields) else ""
            i += 1
            path = _decode(fields[i]) if i < len(fields) else ""
            i += 1
        old_role = _role(old_path, project) if old_path else None
        role = _role(path, project) if path else "unclassified"
        out.append({
            "path": path,
            "old_path": old_path if old_path != path else None,
            "role": role,
            "old_role": old_role if old_path != path else None,
            "additions": added,
            "deletions": deleted,
            "binary": added is None or deleted is None,
            "text_change_known": added is not None and deleted is not None,
        })
    return out


def _name_status(repo: Path, shas: list[str] | None = None) -> dict[str, list[dict[str, str | None]]]:
    args = ["log", "--all", "--reverse", "--diff-merges=first-parent", "--no-textconv", "--no-ext-diff", "--no-color", "--format=%x1e%H%x00", "--name-status", "-z", "-M"]
    if shas is not None:
        args = ["log", "--no-walk=unsorted", "--diff-merges=first-parent", "--no-textconv", "--no-ext-diff", "--no-color", "--format=%x1e%H%x00", "--name-status", "-z", "-M", *shas]
    raw = _git(repo, *args)
    result: dict[str, list[dict[str, str | None]]] = {}
    for block in raw.split(b"\x1e"):
        if not block:
            continue
        sha_raw, _, payload = block.partition(b"\0")
        sha = _decode(sha_raw.strip(b"\n"))
        fields = [field for field in payload.split(b"\0") if field]
        rows: list[dict[str, str | None]] = []
        i = 0
        while i < len(fields):
            status = _decode(fields[i].lstrip(b"\n"))
            i += 1
            if status[:1] in {"R", "C"}:
                old_path = _decode(fields[i]) if i < len(fields) else ""
                path = _decode(fields[i + 1]) if i + 1 < len(fields) else ""
                i += 2
            else:
                old_path = None
                path = _decode(fields[i]) if i < len(fields) else ""
                i += 1
            rows.append({"change_type": status, "path": path, "old_path": old_path})
        result[sha] = rows
    return result


def _commit_messages(repo: Path, shas: list[str]) -> dict[str, str]:
    if not shas:
        return {}
    raw = _git(repo, "log", "--no-walk=unsorted", "--no-textconv", "--no-ext-diff", "--no-color", "--format=%H%x00%B%x00", *shas)
    fields = raw.split(b"\0")
    result: dict[str, str] = {}
    i = 0
    while i + 1 < len(fields):
        sha = _decode(fields[i].strip(b"\n"))
        message = _decode(fields[i + 1]).strip("\n")
        if re.fullmatch(r"[0-9a-f]{40,64}", sha):
            result[sha] = message
        i += 2
    return result


def _commit_records(
    repo: Path, project: str, cache_dir: Path | None
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], int]:
    immutable_cache = (
        Path(cache_dir) / "commits" / f"{_FORMAT_VERSION}-{POLICY_VERSION}"
        if cache_dir is not None else None
    )
    if immutable_cache is not None:
        immutable_cache.mkdir(parents=True, exist_ok=True)
        shas = _git(repo, "rev-list", "--all", "HEAD", "--reverse").decode().splitlines()
        cached_by_sha: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        missing: list[str] = []
        for sha in shas:
            path = immutable_cache / f"{sha}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("sha") == sha:
                    cached_by_sha[sha] = (payload["commit"], payload["changes"])
                    continue
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
            missing.append(sha)
        if not missing:
            return [cached_by_sha[sha] for sha in shas], len(shas)
    else:
        shas = _git(repo, "rev-list", "--all", "HEAD", "--reverse").decode().splitlines()
        cached_by_sha = {}
        missing = shas
    if not shas:
        return [], 0

    new_records: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for batch in _batches(missing):
        raw = _git(
            repo,
            "log",
            "--no-walk=unsorted",
            "--diff-merges=first-parent",
            "--no-textconv",
            "--no-ext-diff",
            "--no-color",
            "--format=" + _COMMIT_FORMAT,
            "--numstat",
            "-z",
            "--find-renames",
            "--find-copies",
            *batch,
        )
        statuses = _name_status(repo, batch)
        messages = _commit_messages(repo, batch)
        for block in raw.split(b"\x1e"):
            if not block:
                continue
            metadata, sep, changes = block.partition(b"\0")
            parts = metadata.strip(b"\n").split(b"\x1f")
            if len(parts) != 9:
                continue
            keys = ("sha", "parents", "authored_at", "committed_at", "author", "author_email", "committer", "committer_email", "subject")
            row: dict[str, Any] = dict(zip(keys, (_decode(value) for value in parts)))
            row["parents"] = row["parents"].split()
            row["message"] = messages.get(row["sha"], row["subject"])
            row["references"] = sorted(set(_DIRECT_REFERENCE.findall(row["message"])))
            rows = _parse_numstat(changes if sep else b"", project)
            status_rows = statuses.get(row["sha"], [])
            status_lookup = {(item["path"], item["old_path"]): item["change_type"] for item in status_rows}
            for change in rows:
                change["change_type"] = status_lookup.get((change["path"], change["old_path"])) or status_lookup.get((change["path"], None)) or "?"
            new_records[row["sha"]] = (row, rows)
            if immutable_cache is not None:
                (immutable_cache / f"{row['sha']}.json").write_text(
                    json.dumps({"sha": row["sha"], "commit": row, "changes": rows}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
    merged = {**cached_by_sha, **new_records}
    return [merged[sha] for sha in shas if sha in merged], len(cached_by_sha)


def _write_dirty_patches(repo: Path, history_dir: Path) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for label, args in (("staged", ("diff", "--cached", "--binary", "--no-textconv", "--no-ext-diff", "--no-color")),
                        ("unstaged", ("diff", "--binary", "--no-textconv", "--no-ext-diff", "--no-color"))):
        result = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(f"git diff {label} failed: {result.stderr.decode('utf-8', 'replace')}")
        (history_dir / f"{label}.patch").write_bytes(result.stdout)
        outcomes[label] = bool(result.stdout)
    return outcomes


def _dump_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _growth_products(package_dir: Path, project: str,
                     commits: list[dict[str, Any]], changes: list[dict[str, Any]],
                     windows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Write the legacy chart inputs using explicit text-change denominators.

    All repository text activity and maintained implementation/test/tooling are
    separate series. Context or documentation churn is never labeled code.
    """
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {
        "commits": 0, "all_additions": 0, "all_deletions": 0,
        "maintained_additions": 0, "maintained_deletions": 0,
    })
    for row in commits:
        day = row["date"]
        target = by_day[day]
        target["commits"] += 1
        target["all_additions"] += int(row["all_text_additions"])
        target["all_deletions"] += int(row["all_text_deletions"])
        target["maintained_additions"] += int(row["maintained_code_text_additions"])
        target["maintained_deletions"] += int(row["maintained_code_text_deletions"])
    daily: list[dict[str, Any]] = []
    all_net = maintained_net = 0
    for day, values in sorted(by_day.items()):
        all_net += values["all_additions"] - values["all_deletions"]
        maintained_net += values["maintained_additions"] - values["maintained_deletions"]
        daily.append({"day": day, **values, "all_net": all_net,
                      "maintained_code_net": maintained_net,
                      # Existing chart names now deliberately point to maintained code.
                      "net": maintained_net, "cumulative_net": maintained_net,
                      "gross": values["maintained_additions"] + values["maintained_deletions"]})

    def aggregate(period: str) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, int]] = defaultdict(lambda: {
            "commits": 0, "all_additions": 0, "all_deletions": 0,
            "maintained_additions": 0, "maintained_deletions": 0,
        })
        for row in daily:
            dt = datetime.fromisoformat(row["day"])
            key = (dt.replace(day=1).date().isoformat() if period == "month" else
                   (dt.date() - timedelta(days=dt.weekday())).isoformat())
            for metric in groups[key]:
                groups[key][metric] += int(row[metric])
        cumulative = 0
        result = []
        for key, values in sorted(groups.items()):
            cumulative += values["maintained_additions"] - values["maintained_deletions"]
            result.append({period: key, **values, "net": values["maintained_additions"] - values["maintained_deletions"],
                           "cumulative_net": cumulative,
                           "gross": values["maintained_additions"] + values["maintained_deletions"]})
        return result

    weekly, monthly = aggregate("week"), aggregate("month")
    role_rows: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "additions": 0, "deletions": 0, "binary_files": 0})
    for change in changes:
        addition_bucket = role_rows[change["role"]]
        deletion_bucket = role_rows[change.get("old_role") or change["role"]]
        addition_bucket["files"] += 1
        if deletion_bucket is not addition_bucket:
            deletion_bucket["files"] += 1
        if change["binary"]:
            addition_bucket["binary_files"] += 1
            if deletion_bucket is not addition_bucket:
                deletion_bucket["binary_files"] += 1
        else:
            addition_bucket["additions"] += int(change["additions"] or 0)
            deletion_bucket["deletions"] += int(change["deletions"] or 0)
    bucket_churn = [{"role": role, **vals, "gross": vals["additions"] + vals["deletions"]}
                    for role, vals in sorted(role_rows.items())]
    all_additions = sum(int(row["all_text_additions"]) for row in commits)
    all_deletions = sum(int(row["all_text_deletions"]) for row in commits)
    maintained_additions = sum(int(row["maintained_code_text_additions"]) for row in commits)
    maintained_deletions = sum(int(row["maintained_code_text_deletions"]) for row in commits)
    summary = {
        "commit_count_all_refs": len(commits),
        "all_repository_text_additions": all_additions,
        "all_repository_text_deletions": all_deletions,
        "all_repository_text_net": all_additions - all_deletions,
        "maintained_code_additions": maintained_additions,
        "maintained_code_deletions": maintained_deletions,
        "net_tracked_text_lines": maintained_additions - maintained_deletions,
        "gross_line_churn": maintained_additions + maintained_deletions,
        "binary_changes_have_unknown_line_counts": True,
        "date_range": [commits[0]["date"], commits[-1]["date"]] if commits else [],
        "last_30_days": windows["30d"][0],
        "last_90_days": windows["90d"][0],
        "method": "all refs; Git numstat; maintained code means implementation, tests, and tooling; current role policy",
    }
    payload = {
        "project": project,
        "method": {"history_scope": "all refs", "date_basis": "author logical date",
                   "measure": "Git numstat changed text, not executable LoC or effort",
                   "chart_series": "legacy net/cumulative fields represent maintained code"},
        "summary": summary, "daily": daily, "weekly": weekly, "monthly": monthly,
        "bucket_churn": bucket_churn,
    }
    out = package_dir / f"{project}-growth"
    (out.with_suffix(".json")).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = [f"# {project} change history", "", "## Method", "",
                "All refs are included. Git numstat measures changed text lines, not executable LoC, effort, or semantic change.",
                "Maintained code includes only implementation, tests, and tooling under the current role policy.",
                "Documentation, context, evidence, and unclassified path changes remain in all repository activity.", "",
                "## Summary", "", "| Measure | Value |", "| --- | ---: |",
                f"| Commits across captured refs | {len(commits)} |",
                f"| All repository text additions / deletions | {all_additions} / {all_deletions} |",
                f"| Maintained code text additions / deletions | {maintained_additions} / {maintained_deletions} |", "",
                "Detailed commit, file, role, daily, weekly, and monthly records are in `history/`."]
    out.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    _dump_csv(package_dir / f"{project}-growth-daily.csv", daily, list(daily[0]) if daily else ["day"])
    _dump_csv(package_dir / f"{project}-growth-weekly.csv", weekly, list(weekly[0]) if weekly else ["week"])
    _dump_csv(package_dir / f"{project}-growth-monthly.csv", monthly, list(monthly[0]) if monthly else ["month"])
    _dump_csv(package_dir / f"{project}-growth-buckets.csv", bucket_churn, ["role", "files", "additions", "deletions", "binary_files", "gross"])
    return payload


def build_history(
    repo: Path,
    package_dir: Path,
    *,
    project: str,
    revision: str,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Write searchable commits, path changes, refs and cached patch evidence.

    `revision` must resolve to HEAD. If HEAD, refs, or tracked dirty state move
    during collection, this raises so a caller can discard the candidate.
    """
    repo, package_dir = Path(repo), Path(package_dir)
    history_dir = package_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    start_head = _resolve_revision(repo, revision)
    start_actual_head = _resolve_revision(repo, "HEAD")
    if start_head != start_actual_head:
        raise RuntimeError(
            f"Chisel history revision mismatch: requested {start_head}, repository HEAD is {start_actual_head}"
        )
    start_refs = _refs(repo)
    start_refs_hash = hashlib.sha256(json.dumps(start_refs, sort_keys=True).encode()).hexdigest()
    start_status = _status(repo)
    dirty = _write_dirty_patches(repo, history_dir)
    dirty_fingerprints = _dirty_patch_fingerprints(history_dir)
    commits_and_changes, cached_commit_rows = _commit_records(repo, project, cache_dir)
    commits = [row for row, _ in commits_and_changes]
    change_rows: list[dict[str, Any]] = []
    code_scope = {"implementation", "tests", "tooling"}
    per_commit: dict[str, dict[str, Any]] = {}
    for commit, changes in commits_and_changes:
        all_add = all_del = maintained_add = maintained_del = 0
        binary = 0
        for change in changes:
            item = {"sha": commit["sha"], **change}
            change_rows.append(item)
            if change["binary"]:
                binary += 1
                continue
            added, deleted = int(change["additions"] or 0), int(change["deletions"] or 0)
            all_add += added
            all_del += deleted
            if change["role"] in code_scope:
                maintained_add += added
            if (change.get("old_role") or change["role"]) in code_scope:
                maintained_del += deleted
        per_commit[commit["sha"]] = {
            "sha": commit["sha"], "all_files_changed": len(changes),
            "all_text_additions": all_add, "all_text_deletions": all_del,
            "maintained_code_text_additions": maintained_add,
            "maintained_code_text_deletions": maintained_del,
            "binary_files": binary,
        }
    for row in commits:
        row.update(per_commit.get(row["sha"], {}))
        row["date"] = logical_date(datetime.fromisoformat(row["authored_at"])).isoformat()
        row["commit_date"] = logical_date(datetime.fromisoformat(row["committed_at"])).isoformat()

    _jsonl(history_dir / "commits.jsonl", commits)
    _jsonl(history_dir / "changes.jsonl", change_rows)
    refs = start_refs
    _jsonl(history_dir / "refs.jsonl", refs)

    now = datetime.now().astimezone()
    cutoff30 = logical_date(now - timedelta(days=30)).isoformat()
    cutoff90 = logical_date(now - timedelta(days=90)).isoformat()
    windows: dict[str, list[dict[str, Any]]] = {}
    for label, cutoff in (("30d", cutoff30), ("90d", cutoff90)):
        rows = [r for r in commits if r["date"] >= cutoff]
        windows[label] = [{
            "window": label,
            "commits": len(rows),
            "all_text_additions": sum(int(r["all_text_additions"]) for r in rows),
            "all_text_deletions": sum(int(r["all_text_deletions"]) for r in rows),
            "maintained_code_text_additions": sum(int(r["maintained_code_text_additions"]) for r in rows),
            "maintained_code_text_deletions": sum(int(r["maintained_code_text_deletions"]) for r in rows),
            "binary_files": sum(int(r["binary_files"]) for r in rows),
                "basis": "author logical dates; text numstat; additions use destination role and deletions use source role for renames",
        }]
    window_rows = windows["30d"] + windows["90d"]
    _dump_csv(history_dir / "windows.csv", window_rows, list(window_rows[0]) if window_rows else ["window", "commits"])
    growth = _growth_products(package_dir, project, commits, change_rows, windows)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"commits": 0, "all_additions": 0, "all_deletions": 0, "maintained_additions": 0, "maintained_deletions": 0})
    for row in commits:
        item = by_day[row["date"]]
        item["commits"] += 1
        item["all_additions"] += int(row["all_text_additions"])
        item["all_deletions"] += int(row["all_text_deletions"])
        item["maintained_additions"] += int(row["maintained_code_text_additions"])
        item["maintained_deletions"] += int(row["maintained_code_text_deletions"])
    daily = [{"logical_date": day, **values} for day, values in sorted(by_day.items())]
    _dump_csv(history_dir / "daily.csv", daily, ["logical_date", "commits", "all_additions", "all_deletions", "maintained_additions", "maintained_deletions"])

    end_head = _resolve_revision(repo, revision)
    end_actual_head = _resolve_revision(repo, "HEAD")
    end_refs = _refs(repo)
    end_refs_hash = hashlib.sha256(json.dumps(end_refs, sort_keys=True).encode()).hexdigest()
    end_status = _status(repo)
    staged, unstaged, changed_paths = end_status
    end_dirty = _write_dirty_patches(repo, history_dir)
    end_dirty_fingerprints = _dirty_patch_fingerprints(history_dir)
    coherent = (
        start_head == end_head == start_actual_head == end_actual_head
        and start_refs_hash == end_refs_hash
        and start_status == end_status
        and dirty_fingerprints == end_dirty_fingerprints
        and dirty == end_dirty
    )
    if not coherent:
        reasons = _coherence_reasons(
            start_head, end_head, start_actual_head, end_actual_head,
            start_refs, end_refs, start_status, end_status,
            dirty_fingerprints, end_dirty_fingerprints,
        )
        raise RuntimeError(
            "Chisel history capture was rejected because repository state changed during collection: "
            + ("; ".join(reasons) if reasons else "dirty patch presence changed")
        )
    role_counts = Counter(row["role"] for row in change_rows)
    unknown_status_count = sum(row["change_type"] == "?" for row in change_rows)
    coverage = {
        "status": "complete",
        "requested_revision": revision,
        "revision": start_head,
        "revision_after": end_head,
        "head_at_capture": start_actual_head,
        "refs_sha256": start_refs_hash,
        "refs_sha256_after": end_refs_hash,
        "history_scope": "all commits reachable from refs present at capture start",
        "commit_count": len(commits),
        "change_count": len(change_rows),
        "unknown_change_type_count": unknown_status_count,
        "role_counts": dict(sorted(role_counts.items())),
        "classification_policy": "current Chisel role policy applied to destination and old path separately for renames",
        "classification_policy_version": POLICY_VERSION,
        "committed_diffs": {"storage": "all-refs Git bundle", "individual_patch_files": False},
        "immutable_commit_cache_rows_reused": cached_commit_rows,
        "dirty_worktree": {"staged_patch_present": dirty["staged"], "unstaged_patch_present": dirty["unstaged"], "tracked_changed_paths": changed_paths,
                           "untracked_files_included": False},
        "windows": windows,
        "logical_timezone": str(now.tzinfo),
        "growth_products": {"summary": growth["summary"], "daily_rows": len(growth["daily"]),
                            "weekly_rows": len(growth["weekly"]), "monthly_rows": len(growth["monthly"])},
        "limitations": [
            "numstat records changed text lines, not executable lines, effort, or semantic change",
            "historical paths use the current classification policy; renamed source and destination are recorded separately",
            "binary changes have unknown line counts, represented as null",
            "commit references are explicit textual references, not resolved task or pull request relationships",
            "untracked files are not represented in staged or unstaged patches",
        ],
    }
    (history_dir / "coverage.json").write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return coverage
