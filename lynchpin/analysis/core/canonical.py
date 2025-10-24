"""Canonical analysis snapshot + validation for derived artifacts."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from os import PathLike
from typing import Any, cast

from ...core.errors import SchemaVersionError
from ...core.io import (
    load_json,
    repo_root,
    resolve_analysis_path,
    resolve_artifact_path,
    resolve_repo_path,
    save_json,
)

JsonObject = dict[str, Any]


def _sha256(path: str | PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_rev(repo: str, ref: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", ref],
            cwd=repo,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def load_analysis_spec(spec_path: str | PathLike[str]) -> JsonObject:
    data = load_json(resolve_repo_path(spec_path))
    if not isinstance(data, dict):
        raise SchemaVersionError(
            found=type(data).__name__,
            expected="dict",
            source=str(spec_path),
        )
    return cast(JsonObject, data)


def _load_artifacts(spec: JsonObject) -> JsonObject:
    artifacts: JsonObject = {}
    spec_artifacts = spec["artifacts"]
    if not isinstance(spec_artifacts, dict):
        raise SchemaVersionError(
            found=type(spec_artifacts).__name__,
            expected="dict",
            source="analysis spec artifacts",
        )
    for name in spec_artifacts:
        path = resolve_artifact_path(spec, name)
        artifacts[name] = {
            "path": path,
            "data": load_json(path),
            "sha256": _sha256(path),
        }
    return artifacts


def build_analysis_snapshot(spec_path: str | PathLike[str], out_path: str | PathLike[str]) -> JsonObject:
    spec = load_analysis_spec(spec_path)
    artifacts = _load_artifacts(spec)

    sinex_structure = artifacts["sinex_structure_metrics"]["data"]
    sinex_temporal = artifacts["sinex_temporal_metrics"]["data"]

    canonical_claims = {
        'sinex_test_to_app_ratio': 0,
        'sinex_unsafe_blocks': sinex_structure['totals']['unsafe_blocks'],
        'sinex_total_commits': sinex_temporal['stats']['total_commits'],
        'sinex_files_per_commit_median': sinex_temporal['stats']['files_per_commit_median'],
    }

    repo_revisions = {
        'analysis_repo_head': _git_rev(repo_root(), 'HEAD'),
        'sinex_branch': spec['sinex']['branch'],
        'sinex_branch_rev': _git_rev(spec['sinex']['repo'], spec['sinex']['branch']),
    }

    manifest = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'analysis_spec': spec,
        'repo_revisions': repo_revisions,
        'artifact_hashes': {
            k: v['sha256'] for k, v in artifacts.items()
        },
        'canonical_claims': canonical_claims,
        'sanity': {
            'sinex_crates': len(sinex_structure.get('crates', {})),
            'sinex_time_span_days': sinex_temporal.get('stats', {}).get('span_days', 0),
        },
    }

    save_json(resolve_analysis_path(out_path), manifest, sort_keys=True)

    return manifest


def _validate_commit_facts_payload(payload: JsonObject) -> list[str]:
    issues: list[str] = []
    ecosystems = payload.get("ecosystems")
    if not isinstance(ecosystems, dict):
        return ['commit_facts missing ecosystems object']

    for eco in ('sinex',):
        section = ecosystems.get(eco)
        if not isinstance(section, dict):
            issues.append(f'commit_facts missing ecosystem section: {eco}')
            continue
        commits = section.get('commits')
        if not isinstance(commits, list):
            issues.append(f'commit_facts {eco}.commits is not a list')
            continue

        seen: set[Any] = set()
        for i, commit in enumerate(commits):
            if not isinstance(commit, dict):
                issues.append(f'commit_facts {eco}.commits[{i}] is not an object')
                continue
            required = {
                'commit_sha',
                'author',
                'timestamp',
                'message',
                'additions',
                'deletions',
                'lines_changed',
                'files_touched',
                'paths',
                'path_roots',
            }
            missing = required - set(commit.keys())
            if missing:
                issues.append(f'commit_facts {eco}.commits[{i}] missing fields: {sorted(missing)}')
                continue

            sha = commit['commit_sha']
            if sha in seen:
                issues.append(f'commit_facts {eco} duplicate commit_sha: {sha}')
            seen.add(sha)

            if not isinstance(commit.get('paths'), list):
                issues.append(f'commit_facts {eco}.commits[{i}] paths is not a list')
            if not isinstance(commit.get('path_roots'), list):
                issues.append(f'commit_facts {eco}.commits[{i}] path_roots is not a list')
            if commit.get('files_touched') != len(commit.get('paths', [])):
                issues.append(f'commit_facts {eco}.commits[{i}] files_touched mismatch vs paths length')

        declared = section.get('commit_count')
        if declared != len(commits):
            issues.append(f'commit_facts {eco}.commit_count mismatch vs commits length')

    return issues


def _validate_commit_shards_payload(commit_facts: JsonObject, commit_shards: JsonObject) -> list[str]:
    issues: list[str] = []
    ecosystems = commit_facts.get("ecosystems", {})
    families = commit_shards.get("shard_families")
    if not isinstance(families, list):
        return ['commit_shards missing shard_families list']

    by_key: dict[tuple[Any, Any], JsonObject] = {}
    for fam in families:
        if not isinstance(fam, dict):
            issues.append("commit_shards shard family is not an object")
            continue
        eco = fam.get('ecosystem')
        family = fam.get('family')
        key = (family, eco)
        by_key[key] = cast(JsonObject, fam)

    expected = {
        ('time_month', 'sinex'),
        ('author', 'sinex'),
        ('primary_path_root', 'sinex'),
    }
    missing_families = sorted(expected - set(by_key.keys()))
    if missing_families:
        issues.append(f'commit_shards missing families: {missing_families}')

    for (family, eco), fam in by_key.items():
        shards = fam.get('shards', [])
        if not isinstance(shards, list):
            issues.append(f'commit_shards {family}/{eco} shards not list')
            continue

        listed = []
        for i, shard in enumerate(shards):
            if not isinstance(shard, dict):
                issues.append(f'commit_shards {family}/{eco} shard[{i}] not object')
                continue
            shas = shard.get('commit_shas')
            if not isinstance(shas, list):
                issues.append(f'commit_shards {family}/{eco} shard[{i}] commit_shas not list')
                continue
            listed.extend(shas)

        unique = set(listed)
        if len(unique) != len(listed):
            issues.append(f'commit_shards {family}/{eco} has overlapping commit_shas')

        fact_shas = {
            c['commit_sha']
            for c in ecosystems.get(eco, {}).get('commits', [])
            if isinstance(c, dict)
        }
        if unique != fact_shas:
            issues.append(f'commit_shards {family}/{eco} coverage mismatch vs commit_facts')

        declared_count = fam.get('total_commits')
        if declared_count != len(fact_shas):
            issues.append(f'commit_shards {family}/{eco} total_commits mismatch')

        if fam.get('non_overlapping') is not True:
            issues.append(f'commit_shards {family}/{eco} non_overlapping is not true')
        if fam.get('coverage_pct') != 1.0 and len(fact_shas) > 0:
            issues.append(f'commit_shards {family}/{eco} coverage_pct != 1.0')

    return issues


def _validate_analysis_status_payload(payload: JsonObject) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ['analysis_status payload is not an object']
    families = payload.get('families')
    if not isinstance(families, dict):
        return ['analysis_status missing families object']

    allowed_status = {'stable', 'provisional', 'limited', 'missing'}
    for key, row in families.items():
        if not isinstance(row, dict):
            issues.append(f'analysis_status family {key} is not an object')
            continue
        status = row.get('status')
        if status not in allowed_status:
            issues.append(f'analysis_status family {key} invalid status: {status}')
        if not row.get('rationale'):
            issues.append(f'analysis_status family {key} missing rationale')
        artifacts = row.get('artifacts')
        if not isinstance(artifacts, list):
            issues.append(f'analysis_status family {key} artifacts is not a list')
    return issues


def _validate_work_package_scope_payload(payload: JsonObject) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ['work_package_scope payload is not an object']

    ecosystems = payload.get('ecosystems')
    if not isinstance(ecosystems, dict):
        return ['work_package_scope missing ecosystems object']

    for eco in ('sinex', 'polylogue'):
        section = ecosystems.get(eco)
        if not isinstance(section, dict):
            issues.append(f'work_package_scope missing ecosystem section: {eco}')
            continue
        summary = section.get('summary')
        packages = section.get('packages')
        if not isinstance(summary, dict):
            issues.append(f'work_package_scope {eco}.summary is not an object')
        if not isinstance(packages, list):
            issues.append(f'work_package_scope {eco}.packages is not a list')
            continue
        if isinstance(summary, dict) and summary.get('unit_count') != len(packages):
            issues.append(f'work_package_scope {eco}.summary.unit_count mismatch vs packages length')
        for index, row in enumerate(packages[:10]):
            if not isinstance(row, dict):
                issues.append(f'work_package_scope {eco}.packages[{index}] is not an object')
                continue
            required = {
                'work_package_id',
                'unit_type',
                'label',
                'commit_count',
                'artifact_churn_kloc',
                'artifact_paths',
                'breadth',
                'scope_geom',
                'survival_surface_share',
                'durability_adjusted_scope',
            }
            missing = required - set(row.keys())
            if missing:
                issues.append(f'work_package_scope {eco}.packages[{index}] missing fields: {sorted(missing)}')
    return issues


def validate_analysis_artifacts(spec_path: str | PathLike[str]) -> list[str]:
    spec = load_analysis_spec(spec_path)
    issues: list[str] = []

    artifacts: JsonObject = {}
    for name, rel_path in spec['artifacts'].items():
        path = resolve_artifact_path(spec, name)
        if not os.path.exists(path):
            issues.append(f'missing artifact: {name} ({rel_path})')
            continue
        try:
            data = load_json(path)
        except Exception as exc:
            issues.append(f'invalid json: {name} ({exc})')
            continue
        artifacts[name] = data

    legacy_paths = [
        resolve_analysis_path('bundle_catalog.json'),
        resolve_analysis_path('comparison.json'),
        resolve_analysis_path('sinex_structure.json'),
        resolve_analysis_path('sinex_temporal.json'),
    ]
    for legacy in legacy_paths:
        if os.path.exists(legacy):
            issues.append(f'legacy artifact still present: {legacy}')

    commit_facts_path = resolve_analysis_path('commit_facts.json')
    commit_shards_path = resolve_analysis_path('commit_shards.json')
    commit_facts_exists = os.path.exists(commit_facts_path)
    commit_shards_exists = os.path.exists(commit_shards_path)

    if commit_facts_exists:
        commit_facts = load_json(commit_facts_path)
        issues.extend(_validate_commit_facts_payload(commit_facts))

    if commit_shards_exists and not commit_facts_exists:
        issues.append('commit_shards present without commit_facts artifact')
    elif commit_shards_exists and commit_facts_exists:
        commit_shards = load_json(commit_shards_path)
        issues.extend(_validate_commit_shards_payload(commit_facts, commit_shards))

    analysis_status_path = resolve_analysis_path('analysis_status.json')
    if os.path.exists(analysis_status_path):
        analysis_status = load_json(analysis_status_path)
        issues.extend(_validate_analysis_status_payload(analysis_status))

    work_package_scope_path = resolve_analysis_path('work_package_scope.json')
    if os.path.exists(work_package_scope_path):
        work_package_scope = load_json(work_package_scope_path)
        issues.extend(_validate_work_package_scope_payload(work_package_scope))

    return issues
