from __future__ import annotations

from typing import get_args

from lynchpin.core.config import LynchpinConfig
from lynchpin.core.evidence_graph import EvidenceNodeKind
from lynchpin.core.source_contracts import SOURCE_CONTRACT_ALIASES
from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES
from lynchpin.core.source_contracts import SOURCE_CONTRACTS
from lynchpin.core.source_contracts import source_contract


def test_old_disk_image_residue_is_not_a_lynchpin_source() -> None:
    rejected = {
        "calibre_library",
        "cloud_file_inventory",
        "legacy_app_logs",
        "onedrive_inventory",
        "software_inventory",
        "software_installs",
        "teams_logs",
        "tortoisesvn_logs",
    }

    cfg = LynchpinConfig.from_env()

    assert rejected.isdisjoint(SOURCE_CONTRACT_NAMES)
    assert rejected.isdisjoint(cfg.available_sources())
    assert "historical_dataset" not in get_args(EvidenceNodeKind)


def test_source_contracts_carry_capability_and_coverage_policy() -> None:
    contracts = {contract.name: contract for contract in SOURCE_CONTRACTS}

    assert contracts["webhistory"].collection_model == "continuous"
    assert "web_daily" in contracts["webhistory"].mcp_tools
    assert contracts["reddit"].collection_model == "event_export"
    assert contracts["reddit"].materialization_mode == "local"
    assert contracts["reddit"].materialization_executor.ref == "reddit"
    assert "personal_daily_signal" in contracts["reddit"].substrate_tables
    assert contracts["title_metadata"].collection_model == "metadata"
    assert "title_metadata_audit" in contracts["title_metadata"].mcp_tools
    assert contracts["atuin"].collection_model == "continuous"
    assert "terminal_daily" in contracts["atuin"].mcp_tools
    assert contracts["activitywatch"].collection_model == "continuous"
    assert contracts["activitywatch"].materialization_executor.ref == "activitywatch"
    assert contracts["analysis_artifacts"].collection_model == "derived"
    assert contracts["analysis_artifacts"].materialization_mode == "derived"
    assert contracts["analysis_artifacts"].materialization_target == "artifact:analysis_artifacts"
    assert "analysis_artifact_inventory" in contracts["analysis_artifacts"].mcp_tools
    assert "read_analysis_artifact" in contracts["analysis_artifacts"].mcp_tools
    assert contracts["clipboard"].graph_node_kinds == ("clipboard_entry",)
    assert contracts["raw_log"].graph_node_kinds == ("raw_log",)
    assert contracts["wykop"].substrate_daily_signal is True
    assert contracts["keylog"].substrate_daily_signal is True
    assert contracts["webhistory"].materialization_mode == "local"
    assert contracts["webhistory"].materialization_executor.kind == "materializer"
    assert contracts["webhistory"].default_max_age_seconds == 300
    assert contracts["keylog"].materialization_mode == "live"
    assert contracts["health"].materialization_mode == "coverage_bound"
    assert contracts["spotify"].materialization_mode == "local"
    assert contracts["facebook_messenger"].materialization_mode == "local"
    assert contracts["raindrop"].materialization_mode == "local"
    assert contracts["activitywatch_derived"].materialization_mode == "derived"
    assert contracts["activitywatch_derived"].materialization_target == "artifact:activitywatch_derived"
    assert contracts["spotify_daily"].materialization_mode == "derived"
    assert contracts["personal_daily_signals"].materialization_mode == "derived"
    assert contracts["temporal_signals"].materialization_mode == "derived"
    assert contracts["sleep_productivity"].materialization_mode == "derived"
    assert contracts["sleep_productivity"].materialization_executor.ref == "sleep_productivity"


def test_every_source_contract_has_materialization_stance() -> None:
    for contract in SOURCE_CONTRACTS:
        assert contract.materialization_mode in {"live", "local", "derived", "coverage_bound", "manual"}
        assert contract.materialization_target
        assert contract.materialization_executor.kind in {"none", "materializer"}
        assert "argv" not in contract.materialization_executor.to_json()
        if contract.materialization_mode in {"local", "derived"}:
            assert contract.default_max_age_seconds is not None
        if contract.materialization_mode == "live":
            assert contract.materialization_executor.kind == "none"


def test_available_source_keys_are_contracted_or_aliased() -> None:
    cfg = LynchpinConfig.from_env()

    intentionally_not_contracted = {"git_baseline", "raindrop_live"}
    known = set(SOURCE_CONTRACT_NAMES) | set(SOURCE_CONTRACT_ALIASES)
    missing = set(cfg.available_sources()) - known - intentionally_not_contracted

    assert not missing
    assert source_contract("fbmessenger").name == "facebook_messenger"
    assert source_contract("gmail_takeout").name == "google_takeout"
    assert source_contract("irc_raw").name == "irc"
