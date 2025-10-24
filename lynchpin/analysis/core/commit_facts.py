"""Commit fact table and deterministic shard manifests (hard-data only)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from os import PathLike
from typing import cast

from ...core.errors import SchemaVersionError
from .canonical import JsonObject, load_analysis_spec
from .commit_stats import collect_commit_stats
from ...core.io import load_json, resolve_analysis_path, save_json
from .naming import safe_key


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _normalize_commit(c: dict[str, object]) -> JsonObject:
    paths = sorted(_as_str_list(c.get("files")))
    return {
        "commit_sha": str(c["sha"]),
        "author": str(c["author"]),
        "timestamp": str(c["date"]),
        "message": str(c.get("subject", "")),
        "additions": c.get("additions", 0),
        "deletions": c.get("deletions", 0),
        "lines_changed": c.get("lines_changed", 0),
        "files_touched": c.get("files_changed", len(paths)),
        "paths": paths,
        "path_roots": sorted(_as_str_list(c.get("path_roots"))),
    }


def run_commit_facts(spec_path: str | PathLike[str], out_file: str | PathLike[str]) -> JsonObject:
    spec = load_analysis_spec(spec_path)

    sinex_commits = collect_commit_stats(
        repo_dir=spec['sinex']['repo'],
        branch=spec['sinex']['branch'],
        keep_files=True,
    )

    sinex_rows = [_normalize_commit(c) for c in sinex_commits]

    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'methodology': {
            'source': 'git log --numstat',
            'sinex_branch': spec['sinex']['branch'],
            'path_roots': 'derived from changed paths using analyzer.core.commit_stats._path_component',
            'note': 'message field is raw commit text only; no semantic label inference is performed here',
        },
        'ecosystems': {
            'sinex': {
                'commit_count': len(sinex_rows),
                'commits': sinex_rows,
            },
        },
    }
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _month_key(ts: object) -> str:
    return str(ts or "")[:7] or "unknown-month"


def _primary_path_root(commit: JsonObject) -> str:
    roots = sorted(_as_str_list(commit.get("path_roots")))
    return roots[0] if roots else "unknown"


def _family_partitions(commits: list[JsonObject], family: str) -> dict[str, list[JsonObject]]:
    buckets: dict[str, list[JsonObject]] = defaultdict(list)
    for commit in commits:
        if family == 'time_month':
            key = _month_key(commit.get('timestamp'))
        elif family == 'author':
            key = commit.get('author') or 'unknown'
        elif family == 'primary_path_root':
            key = _primary_path_root(commit)
        else:
            raise ValueError(f'Unsupported family: {family}')
        buckets[key].append(commit)
    return buckets


def _make_family_manifest(ecosystem: str, commits: list[JsonObject], family: str) -> JsonObject:
    buckets = _family_partitions(commits, family)
    shards: list[JsonObject] = []
    for key in sorted(buckets.keys()):
        items = sorted(buckets[key], key=lambda c: str(c['timestamp']))
        shard_id = f'{family}:{ecosystem}:{safe_key(key)}'
        shards.append(
            {
                'shard_id': shard_id,
                'family': family,
                'ecosystem': ecosystem,
                'key': key,
                'commit_count': len(items),
                'commit_shas': [str(c['commit_sha']) for c in items],
            }
        )

    all_shas = [sha for shard in shards for sha in _as_str_list(shard['commit_shas'])]
    unique_shas = set(all_shas)
    non_overlapping = len(all_shas) == len(unique_shas)
    coverage_pct = round(len(unique_shas) / max(1, len(commits)), 4)

    return {
        'family': family,
        'ecosystem': ecosystem,
        'total_commits': len(commits),
        'shard_count': len(shards),
        'coverage_pct': coverage_pct,
        'non_overlapping': non_overlapping,
        'shards': shards,
    }


def run_commit_shards(commit_facts_file: str | PathLike[str], out_file: str | PathLike[str]) -> JsonObject:
    facts = load_json(resolve_analysis_path(commit_facts_file))
    if not isinstance(facts, dict):
        raise SchemaVersionError(
            found=type(facts).__name__,
            expected="dict",
            source=str(commit_facts_file),
        )
    ecosystems = facts.get('ecosystems', {})
    if not isinstance(ecosystems, dict):
        ecosystems = {}

    families = ('time_month', 'author', 'primary_path_root')
    family_manifests: list[JsonObject] = []
    for eco in ('sinex',):
        eco_payload = ecosystems.get(eco, {})
        raw_commits = eco_payload.get('commits', []) if isinstance(eco_payload, dict) else []
        commits = cast(list[JsonObject], raw_commits) if isinstance(raw_commits, list) else []
        for family in families:
            family_manifests.append(_make_family_manifest(eco, commits, family))

    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_commit_facts': resolve_analysis_path(commit_facts_file),
        'methodology': {
            'families': list(families),
            'partition_rule': 'each commit appears in exactly one shard per family',
            'note': 'shards are transport/index structures only; no semantic labeling',
        },
        'shard_families': family_manifests,
    }
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload
