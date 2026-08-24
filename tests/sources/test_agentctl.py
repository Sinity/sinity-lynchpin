from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest


def _envelope(*, phase: str = "succeeded", terminal: bool = True) -> dict:
    return {
        "schema": 1,
        "ok": True,
        "payload": {
            "kind": "inline",
            "value": {
                "jobs": [
                    {
                        "job_id": "11111111-1111-1111-1111-111111111111",
                        "kind": "attested-agent",
                        "project_id": "lynchpin",
                        "operation": None,
                        "created_at": "2026-08-24T00:00:00+00:00",
                        "timeout_seconds": 3600,
                        "contract": {"prompt": {"bytes": 999, "sha256": "private-digest"}},
                        "checkout": {"path": "/private/worktree", "head": "abc"},
                        "artifacts": {
                            "log": {"ref": "sinnix://jobs/111/log", "max_bytes": 64000},
                            "result": {"ref": "sinnix://jobs/111/result", "max_bytes": 64000},
                        },
                        "state": {
                            "phase": phase,
                            "terminal": terminal,
                            "observed_at": "2026-08-24T00:01:00+00:00",
                            "systemd": {"ExecMainStatus": "0", "MemoryPeak": "104857600"},
                        },
                    }
                ],
                "truncated": False,
                "snapshot": {"ordering": "created_at_desc_job_id_desc", "ceiling": ["2026-08-24T00:00:00+00:00", "111"]},
            },
        },
    }


def test_agentctl_v1_adapter_projects_only_public_observation_fields() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    snapshot = read_observation_snapshot(loader=lambda: _envelope())
    row = snapshot.observations[0]

    assert row.source_id == "agentctl:11111111-1111-1111-1111-111111111111"
    assert row.status == "succeeded"
    assert row.exit_code == 0
    assert row.memory_usage_max_mb == 100.0
    assert row.ended_at is None
    assert row.duration_s is None
    assert row.host == "unknown"
    assert row.command == ()
    assert row.cwd is None
    assert row.args_json == "{}"
    assert json.loads(row.artifact_refs_json) == ["sinnix://jobs/111/log", "sinnix://jobs/111/result"]
    serialized = json.dumps(row.__dict__, default=str)
    assert "/private/worktree" not in serialized
    assert "private-digest" not in serialized
    assert "prompt" not in serialized


@pytest.mark.parametrize(
    ("phase", "terminal", "expected_outcome", "expected_exit"),
    [
        ("observation-unknown", False, None, None),
        ("outcome-unknown", False, None, None),
        ("cancelled", True, True, 0),
        ("launch-failed", True, True, None),
    ],
)
def test_agentctl_states_preserve_unknown_and_cancellation_truth(
    phase: str, terminal: bool, expected_outcome: bool | None, expected_exit: int | None
) -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    row = read_observation_snapshot(loader=lambda: _envelope(phase=phase, terminal=terminal)).observations[0]

    assert row.status == phase
    assert row.outcome_known is expected_outcome
    assert row.exit_code == expected_exit
    assert row.recovery_state is None
    assert "restart/recovery" in row.caveats_json
    assert "reconciliation time" in row.caveats_json


def test_agentctl_receipt_refs_require_explicit_public_values() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    payload = _envelope()
    job = payload["payload"]["value"]["jobs"][0]
    job["semantic_receipts"] = [
        {"owner": "polylogue", "ref": "polylogue://receipts/42", "private_payload": "ignore"},
        {"owner": "sinex", "ref": "sinex://receipts/99"},
        {"owner": "other", "ref": "other://not-joined"},
    ]
    row = read_observation_snapshot(loader=lambda: payload).observations[0]

    assert {(ref.owner, ref.ref) for ref in row.receipt_refs} == {
        ("polylogue", "polylogue://receipts/42"),
        ("sinex", "sinex://receipts/99"),
    }
    assert "private_payload" not in json.dumps(row.__dict__, default=str)


def test_agentctl_missing_result_artifact_stays_an_explicit_caveat() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    payload = _envelope()
    payload["payload"]["value"]["jobs"][0]["artifacts"]["result"] = None

    row = read_observation_snapshot(loader=lambda: payload).observations[0]
    assert json.loads(row.artifact_refs_json) == ["sinnix://jobs/111/log"]
    assert "result artifact is absent" in row.caveats_json


def test_agentctl_rejects_unsupported_public_contract_schema() -> None:
    from lynchpin.sources.agentctl import AgentctlObservationContractError, read_observation_snapshot

    payload = deepcopy(_envelope())
    payload["schema"] = 2
    with pytest.raises(AgentctlObservationContractError, match="schema 1"):
        read_observation_snapshot(loader=lambda: payload)


def test_agentctl_reader_has_no_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources import agentctl

    calls: list[tuple[str, ...]] = []

    def run(command, **kwargs):
        calls.append(tuple(command))
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 15
        return SimpleNamespace(stdout=json.dumps(_envelope()))

    monkeypatch.setattr(agentctl.subprocess, "run", run)

    assert agentctl.read_observation_snapshot().observations
    assert calls == [("agentctl", "job", "list")]


def test_agentctl_explicit_recovery_state_is_preserved_without_inference() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    payload = _envelope()
    payload["payload"]["value"]["jobs"][0]["state"]["recovery"] = "recovered"

    row = read_observation_snapshot(loader=lambda: payload).observations[0]
    assert row.recovery_state == "recovered"
