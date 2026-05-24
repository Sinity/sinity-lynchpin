from __future__ import annotations

from typing import get_args

from lynchpin.core.config import LynchpinConfig
from lynchpin.core.evidence_graph import EvidenceNodeKind
from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES


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
