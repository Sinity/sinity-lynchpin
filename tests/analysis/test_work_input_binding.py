from datetime import date
from types import SimpleNamespace

from lynchpin.analysis.core.work_input_binding import current_work_input_binding, binding_key


def _sources(tmp_path, monkeypatch):
    ledger = tmp_path / 'history.sqlite'
    ledger.write_bytes(b'neutral fixture')
    monkeypatch.setattr('lynchpin.sources.xtask_history.xtask_history_path', lambda: ledger)
    snapshot = SimpleNamespace(observations=())
    monkeypatch.setattr('lynchpin.sources.agentctl.read_observation_snapshot', lambda: snapshot)
    for reader in ['iter_all_invocations', 'iter_all_stage_timings', 'iter_all_test_results']:
        monkeypatch.setattr('lynchpin.sources.xtask_history.' + reader, lambda **kwargs: iter(()))
    return ledger, snapshot


def test_work_binding_detects_owner_inputs_code_and_unavailable_route(tmp_path, monkeypatch):
    from lynchpin.sources.agentctl import AgentctlObservationUnavailable
    ledger, snapshot = _sources(tmp_path, monkeypatch)
    baseline = current_work_input_binding()
    assert baseline is not None and current_work_input_binding() == baseline
    ledger.write_bytes(b'updated neutral fixture')
    changed = current_work_input_binding()
    assert changed is not None and changed['inputs'] != baseline['inputs']
    wal = tmp_path / 'history.sqlite-wal'
    wal.write_bytes(b'new ledger transaction')
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


def test_changed_inputs_during_promotion_do_not_publish_stale_binding(tmp_path, monkeypatch):
    from lynchpin.analysis.active.substrate_promote_work import promote_work_sources
    from lynchpin.substrate.source_status import SourceSelection
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
        assert 'binding changed or unavailable' in conn.execute("SELECT reason FROM substrate_source_status WHERE source='work_observations'").fetchone()[0]


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
