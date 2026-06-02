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
    assert "personal_daily_signal" in contracts["reddit"].substrate_tables
    assert contracts["title_metadata"].collection_model == "metadata"
    assert "title_metadata_audit" in contracts["title_metadata"].mcp_tools
    assert contracts["atuin"].collection_model == "continuous"
    assert "terminal_daily" in contracts["atuin"].mcp_tools
    assert contracts["analysis_artifacts"].collection_model == "derived"
    assert "analysis_artifact_inventory" in contracts["analysis_artifacts"].mcp_tools
    assert "read_analysis_artifact" in contracts["analysis_artifacts"].mcp_tools
    assert contracts["clipboard"].graph_node_kinds == ("clipboard_entry",)
    assert contracts["raw_log"].graph_node_kinds == ("raw_log",)
    assert contracts["wykop"].substrate_daily_signal is True


def test_available_source_keys_are_contracted_or_aliased() -> None:
    cfg = LynchpinConfig.from_env()

    intentionally_not_contracted = {"git_baseline", "raindrop_live"}
    known = set(SOURCE_CONTRACT_NAMES) | set(SOURCE_CONTRACT_ALIASES)
    missing = set(cfg.available_sources()) - known - intentionally_not_contracted

    assert not missing
    assert source_contract("fbmessenger").name == "facebook_messenger"
    assert source_contract("gmail_takeout").name == "google_takeout"
    assert source_contract("irc_raw").name == "irc"
