from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest


def _rows(*, phase: str = "succeeded", terminal: bool = True) -> list[dict]:
    return [
        {
            "job_id": 111,
            "label": "lynchpin:check",
            "kind": "declared-operation",
            "project": "lynchpin",
            "operation": "check",
            "group": "normal",
            "phase": phase,
            "terminal": terminal,
            "result": "Success" if phase == "succeeded" else None,
            "exit_code": 0 if phase == "succeeded" else None,
            "path": "/private/worktree",
            "reference": "private-reference",
            "enqueued_at": "2026-08-24T00:00:00+00:00",
            "started_at": "2026-08-24T00:00:01+00:00",
            "ended_at": "2026-08-24T00:01:01+00:00" if terminal else None,
        }
    ]


def test_agentctl_native_adapter_projects_only_public_observation_fields() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    snapshot = read_observation_snapshot(loader=lambda: _rows())
    row = snapshot.observations[0]

    assert snapshot.contract_schema == 3
    assert row.source_id == "agentctl:111"
    assert row.project == "lynchpin"
    assert row.operation == "check"
    assert row.status == "succeeded"
    assert row.exit_code == 0
    assert row.duration_s == 60.0
    assert row.ended_at is not None
    assert row.host == "unknown"
    assert row.command == ()
    assert row.cwd is None
    assert row.args_json == "{}"
    assert json.loads(row.artifact_refs_json) == []
    serialized = json.dumps(row.__dict__, default=str)
    assert "/private/worktree" not in serialized
    assert "private-reference" not in serialized


@pytest.mark.parametrize(
    ("phase", "terminal", "expected_outcome", "expected_exit"),
    [
        ("queued", False, None, None),
        ("running", False, None, None),
        ("cancelled", True, True, None),
        ("launch-failed", True, True, None),
    ],
)
def test_agentctl_states_preserve_unknown_and_cancellation_truth(
    phase: str, terminal: bool, expected_outcome: bool | None, expected_exit: int | None
) -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    row = read_observation_snapshot(loader=lambda: _rows(phase=phase, terminal=terminal)).observations[0]

    assert row.status == phase
    assert row.outcome_known is expected_outcome
    assert row.exit_code == expected_exit
    assert row.recovery_state is None
    assert "snapshot identity" in row.caveats_json
    if phase == "cancelled":
        assert "cancellation" in row.caveats_json


def test_agentctl_uses_enqueue_time_when_process_start_is_absent() -> None:
    from lynchpin.sources.agentctl import read_observation_snapshot

    payload = _rows()
    payload[0]["started_at"] = None
    payload[0]["ended_at"] = "2026-08-24T00:01:01+00:00"

    row = read_observation_snapshot(loader=lambda: payload).observations[0]

    assert row.started_at.isoformat() == "2026-08-24T00:00:00+00:00"
    assert row.ended_at is not None
    assert row.duration_s is None
    assert "enqueued_at is used" in row.caveats_json


def test_agentctl_rejects_non_list_response() -> None:
    from lynchpin.sources.agentctl import AgentctlObservationContractError, read_observation_snapshot

    with pytest.raises(AgentctlObservationContractError, match="non-list"):
        read_observation_snapshot(loader=lambda: {"jobs": _rows()})


def test_agentctl_rejects_invalid_contract_record() -> None:
    from lynchpin.sources.agentctl import AgentctlObservationContractError, read_observation_snapshot

    payload = deepcopy(_rows())
    payload[0]["enqueued_at"] = "not-a-timestamp"
    with pytest.raises(AgentctlObservationContractError, match="enqueued_at"):
        read_observation_snapshot(loader=lambda: payload)


def test_agentctl_reader_has_no_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.sources import agentctl

    calls: list[tuple[str, ...]] = []

    def run(command, **kwargs):
        calls.append(tuple(command))
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 15
        return SimpleNamespace(stdout=json.dumps(_rows()))

    monkeypatch.setattr(agentctl.subprocess, "run", run)

    assert agentctl.read_observation_snapshot().observations
    assert calls == [("agentctl", "job", "list", "--json", "--all")]


def test_agentctl_observation_carries_operation_from_the_job_list_row() -> None:
    """The declared operation name survives into the observation.

    Anti-vacuity: reverting the adapter to drop ``operation`` (as it did before,
    using it only to compute ``source_revision``) makes ``row.operation`` None
    and this test red. A job row without an operation must stay None rather
    than inventing one from ``label``.
    """
    from lynchpin.sources.agentctl import read_observation_snapshot

    rows = _rows()
    rows[0]["operation"] = "verify_all"
    row = read_observation_snapshot(loader=lambda: rows).observations[0]
    assert row.operation == "verify_all"

    unlabelled = _rows()
    del unlabelled[0]["operation"]
    missing = read_observation_snapshot(loader=lambda: unlabelled).observations[0]
    assert missing.operation is None
    # ``label`` is project:operation and is deliberately not carried separately.
    assert not hasattr(missing, "label")
