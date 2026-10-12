from datetime import date

import pytest

from lynchpin.analysis.active.substrate_promote_status import SourceSelection, record_source_status
from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.machine_lineage import record_machine_partition, sqlite_location_identity
from lynchpin.substrate.machine_recovery import recover_machine_partitions
from lynchpin.substrate.run_steps import record_run_step


@pytest.mark.parametrize("fault", [None, "count", "identity", "incomplete", "window"])
def test_recovery_admits_only_completed_matching_partition(tmp_path, fault):
    source = tmp_path / "source.sqlite"
    source.touch()
    retained = tmp_path / ("substrate.candidate-" + "a" * 32 + ".duckdb")
    start, end = date(2026, 5, 1), date(2026, 5, 2)
    identity = sqlite_location_identity(source)
    with connect(retained) as c:
        apply_schema(c)
        c.execute("INSERT INTO machine_metric_sample (observed_at,host,source,source_schema_version,refresh_id) VALUES ('2026-05-01T10:00:00Z','fixture','fixture',1,'old')")
        record_machine_partition(c, table="machine_metric_sample", refresh_id="old", window_start=start, window_end=end,
                                 logical_row_count=1, input_fingerprint="other" if fault == "identity" else identity)
        record_source_status(c, refresh_id="old", source="machine", status="ok", reason=None, row_count=2 if fault == "count" else 1,
                             window_start=date(2026, 5, 2) if fault == "window" else start, window_end=end)
        record_run_step(c, refresh_id="old", step="promote_machine_metric_sample", status="running" if fault == "incomplete" else "success", message="fixture")
        # A failed table alongside the completed one must never be copied.
        record_source_status(c, refresh_id="old", source="machine_gpu_sample", status="error", reason="fixture failure", row_count=0,
                             window_start=start, window_end=end)
    with connect(tmp_path / "new.duckdb") as c:
        apply_schema(c)
        kwargs = dict(path=retained, canonical=tmp_path / "substrate.duckdb", sqlite_path=source, window_start=start, window_end=end,
                      sources=SourceSelection.from_collection({"machine", "machine_gpu_sample"}))
        if fault is not None:
            with pytest.raises(ValueError):
                recover_machine_partitions(c, **kwargs)
            assert c.execute("SELECT COUNT(*) FROM machine_metric_sample").fetchone()[0] == 0
        else:
            assert recover_machine_partitions(c, **kwargs) == frozenset({"machine_metric_sample"})
            assert recover_machine_partitions(c, **kwargs) == frozenset({"machine_metric_sample"})
            assert c.execute("SELECT host FROM machine_metric_sample").fetchall() == [("fixture",)]
            assert c.execute("SELECT COUNT(*) FROM machine_gpu_sample").fetchone()[0] == 0
    with connect(retained) as c:
        assert c.execute("SELECT refresh_id,host FROM machine_metric_sample").fetchall() == [("old", "fixture")]


def test_recovery_refuses_arbitrary_store_path(tmp_path):
    source = tmp_path / "source.sqlite"
    source.touch()
    retained = tmp_path / "unowned.duckdb"
    with connect(retained) as c:
        apply_schema(c)
        with pytest.raises(ValueError, match="owner-retained candidate"):
            recover_machine_partitions(c, path=retained, canonical=tmp_path / "substrate.duckdb", sqlite_path=source,
                                       window_start=date(2026, 5, 1), window_end=date(2026, 5, 2), sources=SourceSelection())



def test_startup_cleanup_preserves_explicit_recovery_input(tmp_path):
    from lynchpin.substrate.connection import _archive_interrupted_candidates

    canonical = tmp_path / "substrate.duckdb"
    selected = tmp_path / ("substrate.candidate-" + "a" * 32 + ".duckdb")
    discarded = tmp_path / ("substrate.candidate-" + "b" * 32 + ".duckdb")
    selected.write_bytes(b"retained fixture")
    discarded.write_bytes(b"interrupted fixture")
    _archive_interrupted_candidates(canonical, protected_sources=frozenset({selected.resolve()}))
    assert selected.read_bytes() == b"retained fixture"
    assert not discarded.exists()
