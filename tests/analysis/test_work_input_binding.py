import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from lynchpin.analysis.core.work_input_binding import current_work_input_binding, binding_key


def _sources(tmp_path, monkeypatch):
    ledger = tmp_path / 'history.sqlite'
    ledger.write_bytes(b'neutral fixture')
    lane = tmp_path / 'evidence.jsonl'
    receipt = dict(kind='polylogue.verification-receipt', schema_version=1,
                   run_id='fixture-good', status='passed', semantic_status='passed',
                   started_at='2026-05-01T12:00:00+00:00', source_revision='a' * 40)
    invalid = {**receipt, 'run_id': 'fixture-rejected', 'source_revision': None}
    lane.write_text(json.dumps(receipt) + '\n' + json.dumps(invalid) + '\n')
    monkeypatch.setattr('lynchpin.sources.xtask_history.xtask_history_path', lambda: ledger)
    monkeypatch.setattr('lynchpin.sources.polylogue_verification.verification_lane_path', lambda: lane)
    snapshot = SimpleNamespace(observations=())
    monkeypatch.setattr('lynchpin.sources.agentctl.read_observation_snapshot', lambda: snapshot)
    for reader in ['iter_all_invocations', 'iter_all_stage_timings', 'iter_all_test_results']:
        monkeypatch.setattr('lynchpin.sources.xtask_history.' + reader, lambda **kwargs: iter(()))
    return ledger, lane, snapshot


def test_work_binding_detects_owner_inputs_code_and_unavailable_route(tmp_path, monkeypatch):
    from lynchpin.sources.agentctl import AgentctlObservationUnavailable
    ledger, lane, snapshot = _sources(tmp_path, monkeypatch)
    baseline = current_work_input_binding()
    assert baseline is not None and current_work_input_binding() == baseline
    ledger.write_bytes(b'updated neutral fixture')
    changed = current_work_input_binding()
    assert changed is not None and changed['inputs'] != baseline['inputs']
    wal = tmp_path / 'history.sqlite-wal'
    wal.write_bytes(b'new ledger transaction')
    assert current_work_input_binding() != changed
    changed = current_work_input_binding()
    lane.write_text(lane.read_text() + '\n')
    assert current_work_input_binding() != changed
    changed = current_work_input_binding()
    snapshot.observations = (SimpleNamespace(source_id='agentctl:1', source_revision='sha256:fixture'),)
    assert current_work_input_binding() != changed
    changed = current_work_input_binding()
    monkeypatch.setattr('lynchpin.analysis.core.work_input_binding._implementation_digest', lambda: 'new-code')
    after_code = current_work_input_binding()
    assert after_code is not None and changed is not None
    assert after_code['inputs'] == changed['inputs']
    assert after_code['implementation'] != changed['implementation']
    def unavailable():
        raise AgentctlObservationUnavailable('synthetic offline route')
    monkeypatch.setattr('lynchpin.sources.agentctl.read_observation_snapshot', unavailable)
    assert current_work_input_binding() is None


def test_completed_degraded_attempt_skips_without_rewriting_rejected_evidence(tmp_path, monkeypatch):
    from lynchpin.analysis.active.substrate_promote_work import promote_work_sources
    from lynchpin.analysis.active.substrate_promote_status import SourceSelection, record_source_status
    from lynchpin.analysis.core.dag import DAG, Step
    from lynchpin.analysis.core.materialization_intelligence import (
        MaterializationStepPolicy, materialization_plan_for_dag, _substrate_source_status,
    )
    from lynchpin.substrate.connection import apply_schema
    from lynchpin.sources.polylogue_verification import read_verification_snapshot
    _, lane, _ = _sources(tmp_path, monkeypatch)
    original = lane.read_bytes()
    database = tmp_path / 'substrate.duckdb'
    import duckdb
    with duckdb.connect(str(database)) as conn:
        apply_schema(conn)
        promote_work_sources(conn, refresh_id='fixture', window_start=date(2026, 5, 1),
                             window_end=date(2026, 5, 2), counts={},
                             selection=SourceSelection.from_collection({'work_observations'}))
        assert conn.execute("SELECT status,row_count FROM substrate_source_status WHERE source='work_observations'").fetchone() == ('degraded', 1)
        assert conn.execute("SELECT source_id FROM work_observation").fetchall() == [('polylogue-verification:fixture-good',)]
        assert conn.execute('SELECT value FROM substrate_meta WHERE key=?', [binding_key('fixture')]).fetchone()
        # A more recent inventory observation must not shadow the completed stage.
        record_source_status(conn, refresh_id='inventory', source='work_observations', kind='inventory',
                             status='unavailable', reason='synthetic inventory', row_count=None)
    monkeypatch.setattr('lynchpin.substrate.connection.substrate_path', lambda: database)
    status = _substrate_source_status('work_observations', now=datetime.now(timezone.utc))
    assert status['status'] == 'degraded' and status['input_binding_state'] == 'unchanged'
    assert 'polylogue_verification_rejected=1' in status['reason']
    dag = DAG('fixture')
    dag.add(Step('promote', 'test:noop'))
    policy = {'promote': MaterializationStepPolicy('promote', substrate_sources=('work_observations',), max_age_seconds=300)}
    def plan(*, now=None):
        return materialization_plan_for_dag(dag, policies=policy, now=now,
            requested_window=(date(2026, 5, 1), date(2026, 5, 2)))[0]
    assert plan().action == 'skip'
    assert plan().action == 'skip'
    assert lane.read_bytes() == original
    assert len(read_verification_snapshot().rejected) == 1
    lane.write_bytes(original + b'\n')
    assert plan().action == 'skip'
    assert plan(now=datetime.now(timezone.utc) + timedelta(seconds=301)).action == 'run'
    monkeypatch.setattr('lynchpin.analysis.core.work_input_binding._implementation_digest', lambda: 'changed-code')
    assert plan().action == 'run'
    assert len(read_verification_snapshot().rejected) == 1
    with duckdb.connect(str(database)) as conn:
        conn.execute('UPDATE substrate_meta SET value=? WHERE key=?',
                     [json.dumps({'inputs': 'invalid', 'implementation': 'invalid'}), binding_key('fixture')])
    status = _substrate_source_status('work_observations', now=datetime.now(timezone.utc))
    assert status['input_binding_state'] == 'missing'
    assert plan().action == 'run'


def test_changed_inputs_during_promotion_do_not_publish_stale_binding(tmp_path, monkeypatch):
    from lynchpin.analysis.active.substrate_promote_work import promote_work_sources
    from lynchpin.analysis.active.substrate_promote_status import SourceSelection
    from lynchpin.substrate.connection import apply_schema, connect
    _sources(tmp_path, monkeypatch)
    bindings = iter([{'inputs': 'before', 'implementation': 'same'}, {'inputs': 'after', 'implementation': 'same'}])
    monkeypatch.setattr('lynchpin.analysis.core.work_input_binding.current_work_input_binding', lambda: next(bindings))
    with connect(tmp_path / 'substrate.duckdb') as conn:
        apply_schema(conn)
        conn.execute('INSERT INTO substrate_meta VALUES (?, ?)', [binding_key('fixture'), 'old binding'])
        promote_work_sources(conn, refresh_id='fixture', window_start=date(2026, 5, 1),
                             window_end=date(2026, 5, 2), counts={},
                             selection=SourceSelection.from_collection({'work_observations'}))
        assert conn.execute('SELECT value FROM substrate_meta WHERE key=?', [binding_key('fixture')]).fetchone() is None
        assert conn.execute("SELECT status FROM substrate_source_status WHERE source='work_observations'").fetchone() == ('degraded',)


def test_implementation_binding_matches_relocated_identical_code(tmp_path):
    from lynchpin.analysis.core.work_input_binding import _implementation_digest
    first, second = tmp_path / 'checkout', tmp_path / 'installed'
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / 'module.py').write_text('value = 1\n')
    assert _implementation_digest(first) == _implementation_digest(second)
    (second / 'module.py').write_text('value = 2\n')
    assert _implementation_digest(first) != _implementation_digest(second)
