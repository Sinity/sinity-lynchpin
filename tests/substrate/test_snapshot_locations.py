"""Placement changes preserve captured times, identities and unrelated columns."""
import duckdb
import pytest

from lynchpin.cli.snapshot_locations import apply_location_changes, location_changes


def test_current_lookup_relocation_preserves_capture_and_history():
    with duckdb.connect(":memory:") as conn:
        conn.execute("CREATE TABLE code_snapshot_run(project VARCHAR, refresh_id VARCHAR, output_dir VARCHAR, run_at VARCHAR, git_commit VARCHAR)")
        conn.execute("CREATE TABLE code_snapshot_slice(project VARCHAR, refresh_id VARCHAR, path VARCHAR, size_bytes BIGINT)")
        conn.execute("INSERT INTO code_snapshot_run VALUES ('fixture', 'latest', '/old/code/fixture', 'capture-time', 'commit'), ('fixture', 'history', '/old/code/fixture', 'older-time', 'old-commit')")
        conn.execute("INSERT INTO code_snapshot_slice VALUES ('fixture', 'latest', '/old/code/fixture/source/a.py', 42), ('other', 'latest', '/old/code-prefix/b.py', 3)")
        moves = [{"source": "/old/code", "destination": "/new/shared"},
                 {"source": "/old/code/fixture", "destination": "/new/fixture/current"}]
        changes = location_changes(conn, moves)
        assert len(changes) == 2
        apply_location_changes(conn, changes)
        assert conn.execute("SELECT * FROM code_snapshot_run WHERE refresh_id='latest'").fetchone() == ('fixture', 'latest', '/new/fixture/current', 'capture-time', 'commit')
        assert conn.execute("SELECT output_dir FROM code_snapshot_run WHERE refresh_id='history'").fetchone() == ('/old/code/fixture',)
        assert conn.execute("SELECT path,size_bytes FROM code_snapshot_slice WHERE project='fixture'").fetchone() == ('/new/fixture/current/source/a.py', 42)
        assert location_changes(conn, moves) == []
        with pytest.raises(ValueError, match="only snapshot"):
            apply_location_changes(conn, [{"table": "code_snapshot_run", "column": "run_at"}])
