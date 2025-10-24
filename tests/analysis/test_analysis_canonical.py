"""Tests for lynchpin.analysis.core.canonical pure validation functions."""

from __future__ import annotations


from lynchpin.analysis.core.canonical import (
    _validate_analysis_status_payload,
    _validate_commit_facts_payload,
    _validate_commit_shards_payload,
    _validate_work_package_scope_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _commit(sha: str = "abc", author: str = "alice", ts: str = "2026-03-01",
            additions: int = 10, deletions: int = 5, lines_changed: int = 15,
            paths: list[str] | None = None) -> dict:
    ps = paths or ["src/main.rs"]
    return {
        "commit_sha": sha,
        "author": author,
        "timestamp": ts,
        "message": "fix: something",
        "additions": additions,
        "deletions": deletions,
        "lines_changed": lines_changed,
        "files_touched": len(ps),
        "paths": ps,
        "path_roots": ["src"],
    }


def _facts_payload(sinex_commits: list | None = None) -> dict:
    sinex = sinex_commits or [_commit("s1"), _commit("s2")]
    return {
        "ecosystems": {
            "sinex": {"commit_count": len(sinex), "commits": sinex},
        }
    }


def _shards_payload(commit_facts: dict) -> dict:
    """Build a minimal valid shard payload from commit_facts."""
    ecosystems = commit_facts["ecosystems"]
    families = []
    for eco in ("sinex",):
        commits = ecosystems[eco]["commits"]
        shas = [c["commit_sha"] for c in commits]
        for family in ("time_month", "author", "primary_path_root"):
            families.append({
                "family": family,
                "ecosystem": eco,
                "total_commits": len(shas),
                "non_overlapping": True,
                "coverage_pct": 1.0,
                "shards": [{"commit_shas": shas}],
            })
    return {"shard_families": families}


# ---------------------------------------------------------------------------
# _validate_commit_facts_payload
# ---------------------------------------------------------------------------

class TestValidateCommitFactsPayload:
    def test_valid_payload_returns_no_issues(self) -> None:
        assert _validate_commit_facts_payload(_facts_payload()) == []

    def test_missing_ecosystems_object_returns_error(self) -> None:
        issues = _validate_commit_facts_payload({})
        assert any("missing ecosystems" in i for i in issues)

    def test_non_dict_ecosystems_returns_error(self) -> None:
        issues = _validate_commit_facts_payload({"ecosystems": "not a dict"})
        assert len(issues) >= 1

    def test_missing_sinex_section_flagged(self) -> None:
        payload = {"ecosystems": {}}
        issues = _validate_commit_facts_payload(payload)
        assert any("sinex" in i for i in issues)

    def test_missing_required_field_flagged(self) -> None:
        bad = _commit("sha1")
        del bad["author"]
        payload = _facts_payload(sinex_commits=[bad])
        issues = _validate_commit_facts_payload(payload)
        assert any("author" in i for i in issues)

    def test_duplicate_sha_flagged(self) -> None:
        c1 = _commit("dup")
        c2 = _commit("dup")
        payload = _facts_payload(sinex_commits=[c1, c2])
        issues = _validate_commit_facts_payload(payload)
        assert any("duplicate" in i.lower() for i in issues)

    def test_commit_count_mismatch_flagged(self) -> None:
        sinex = [_commit("sha1"), _commit("sha2")]
        payload = {
            "ecosystems": {
                "sinex": {"commit_count": 99, "commits": sinex},  # wrong count
            }
        }
        issues = _validate_commit_facts_payload(payload)
        assert any("commit_count mismatch" in i for i in issues)

    def test_files_touched_mismatch_flagged(self) -> None:
        c = _commit("sha1", paths=["a.rs", "b.rs"])
        c["files_touched"] = 99  # wrong
        payload = _facts_payload(sinex_commits=[c])
        issues = _validate_commit_facts_payload(payload)
        assert any("files_touched mismatch" in i for i in issues)

    def test_paths_not_list_flagged(self) -> None:
        c = _commit("sha1")
        c["paths"] = "not a list"
        payload = _facts_payload(sinex_commits=[c])
        issues = _validate_commit_facts_payload(payload)
        assert any("paths is not a list" in i for i in issues)


# ---------------------------------------------------------------------------
# _validate_commit_shards_payload
# ---------------------------------------------------------------------------

class TestValidateCommitShardsPayload:
    def test_valid_shards_returns_no_issues(self) -> None:
        facts = _facts_payload()
        shards = _shards_payload(facts)
        assert _validate_commit_shards_payload(facts, shards) == []

    def test_missing_shard_families_returns_error(self) -> None:
        facts = _facts_payload()
        issues = _validate_commit_shards_payload(facts, {})
        assert any("shard_families" in i for i in issues)

    def test_missing_family_flagged(self) -> None:
        facts = _facts_payload()
        shards = _shards_payload(facts)
        # Remove one family entry
        shards["shard_families"] = [
            f for f in shards["shard_families"]
            if not (f["family"] == "author" and f["ecosystem"] == "sinex")
        ]
        issues = _validate_commit_shards_payload(facts, shards)
        assert any("missing families" in i for i in issues)

    def test_duplicate_shas_in_shards_flagged(self) -> None:
        facts = _facts_payload(sinex_commits=[_commit("sha1")])
        shards = _shards_payload(facts)
        # Create overlap: shard with sha1 twice
        for f in shards["shard_families"]:
            if f["family"] == "time_month" and f["ecosystem"] == "sinex":
                f["shards"] = [{"commit_shas": ["sha1", "sha1"]}]
                break
        issues = _validate_commit_shards_payload(facts, shards)
        assert any("overlapping" in i for i in issues)

    def test_coverage_mismatch_flagged(self) -> None:
        facts = _facts_payload(sinex_commits=[_commit("sha1"), _commit("sha2")])
        shards = _shards_payload(facts)
        # Replace sha2 with a bogus sha in one family
        for f in shards["shard_families"]:
            if f["family"] == "time_month" and f["ecosystem"] == "sinex":
                f["shards"] = [{"commit_shas": ["sha1", "completely_different"]}]
                break
        issues = _validate_commit_shards_payload(facts, shards)
        assert any("coverage mismatch" in i for i in issues)

    def test_non_overlapping_false_flagged(self) -> None:
        facts = _facts_payload(sinex_commits=[_commit("sha1")])
        shards = _shards_payload(facts)
        for f in shards["shard_families"]:
            if f["family"] == "time_month" and f["ecosystem"] == "sinex":
                f["non_overlapping"] = False
                break
        issues = _validate_commit_shards_payload(facts, shards)
        assert any("non_overlapping" in i for i in issues)


# ---------------------------------------------------------------------------
# _validate_analysis_status_payload
# ---------------------------------------------------------------------------

def _status_row(status: str = "stable", rationale: str = "ok", artifacts: list | None = None) -> dict:
    return {"status": status, "rationale": rationale, "artifacts": artifacts or ["a.json"]}


class TestValidateAnalysisStatusPayload:
    def test_valid_payload_returns_no_issues(self) -> None:
        payload = {"families": {"sinex": _status_row(), "polylogue": _status_row("provisional", "pending")}}
        assert _validate_analysis_status_payload(payload) == []

    def test_non_dict_returns_error(self) -> None:
        issues = _validate_analysis_status_payload("not a dict")
        assert len(issues) >= 1

    def test_missing_families_returns_error(self) -> None:
        issues = _validate_analysis_status_payload({})
        assert any("families" in i for i in issues)

    def test_non_dict_families_returns_error(self) -> None:
        issues = _validate_analysis_status_payload({"families": "nope"})
        assert len(issues) >= 1

    def test_invalid_status_flagged(self) -> None:
        payload = {"families": {"sinex": _status_row(status="unknown")}}
        issues = _validate_analysis_status_payload(payload)
        assert any("invalid status" in i for i in issues)

    def test_allowed_statuses_pass(self) -> None:
        for status in ("stable", "provisional", "limited", "missing"):
            payload = {"families": {"x": _status_row(status=status)}}
            issues = _validate_analysis_status_payload(payload)
            assert not any("invalid status" in i for i in issues), f"status={status!r} should be valid"

    def test_empty_rationale_flagged(self) -> None:
        payload = {"families": {"sinex": _status_row(rationale="")}}
        issues = _validate_analysis_status_payload(payload)
        assert any("rationale" in i for i in issues)

    def test_missing_artifacts_list_flagged(self) -> None:
        row = _status_row()
        row["artifacts"] = "not a list"
        payload = {"families": {"sinex": row}}
        issues = _validate_analysis_status_payload(payload)
        assert any("artifacts" in i for i in issues)

    def test_non_dict_family_row_flagged(self) -> None:
        payload = {"families": {"sinex": "not a dict"}}
        issues = _validate_analysis_status_payload(payload)
        assert any("sinex" in i for i in issues)

    def test_multiple_families_all_validated(self) -> None:
        payload = {
            "families": {
                "sinex": _status_row(status="bad"),
                "polylogue": _status_row(rationale=""),
            }
        }
        issues = _validate_analysis_status_payload(payload)
        assert len(issues) >= 2


def _work_package_payload() -> dict:
    row = {
        "work_package_id": "sinex-cluster:0001",
        "unit_type": "contiguous_change_cluster",
        "label": "sinex change cluster",
        "commit_count": 4,
        "artifact_churn_kloc": 1.2,
        "artifact_paths": 8,
        "breadth": 3,
        "scope_geom": 3.14,
        "survival_surface_share": 0.75,
        "durability_adjusted_scope": 2.75,
    }
    return {
        "ecosystems": {
            "sinex": {"summary": {"unit_count": 1}, "packages": [row]},
            "polylogue": {"summary": {"unit_count": 1}, "packages": [{**row, "work_package_id": "polylogue-cluster:0001"}]},
        }
    }


class TestValidateWorkPackageScopePayload:
    def test_valid_payload_returns_no_issues(self) -> None:
        assert _validate_work_package_scope_payload(_work_package_payload()) == []

    def test_missing_ecosystems_flagged(self) -> None:
        issues = _validate_work_package_scope_payload({})
        assert any("ecosystems" in issue for issue in issues)

    def test_missing_section_flagged(self) -> None:
        payload = _work_package_payload()
        del payload["ecosystems"]["polylogue"]
        issues = _validate_work_package_scope_payload(payload)
        assert any("polylogue" in issue for issue in issues)

    def test_unit_count_mismatch_flagged(self) -> None:
        payload = _work_package_payload()
        payload["ecosystems"]["sinex"]["summary"]["unit_count"] = 99
        issues = _validate_work_package_scope_payload(payload)
        assert any("unit_count mismatch" in issue for issue in issues)

    def test_missing_required_row_field_flagged(self) -> None:
        payload = _work_package_payload()
        del payload["ecosystems"]["sinex"]["packages"][0]["scope_geom"]
        issues = _validate_work_package_scope_payload(payload)
        assert any("scope_geom" in issue for issue in issues)
