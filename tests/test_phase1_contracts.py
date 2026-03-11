from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_repo_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nix", "develop", "--command", "python", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def test_unknown_warehouse_source_raises() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.views import warehouse
        try:
            warehouse._source_specs(["does-not-exist"])
        except ValueError as exc:
            assert "Unknown warehouse source" in str(exc)
        else:
            raise AssertionError("unknown warehouse source should fail fast")
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_build_views_drops_stale_managed_relation_for_selected_source() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path
        from lynchpin.views import warehouse

        spec = warehouse.SOURCE_SPECS[0]
        table_name = spec.tables[0].name
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "warehouse.duckdb"
            conn = warehouse.duckdb.connect(str(db_path))
            conn.execute(f"CREATE VIEW {table_name} AS SELECT 1 AS stale_value")
            conn.close()

            warehouse.build_views(
                output=db_path,
                root=tmp_path / "missing-root",
                output_format="parquet",
                sources=[spec.name],
            )

            conn = warehouse.duckdb.connect(str(db_path))
            rows = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [table_name],
            ).fetchone()
            conn.close()
            assert rows == (0,), rows
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_project_registry_exposes_shared_profiles() -> None:
    script = textwrap.dedent(
        """
        from pathlib import Path
        from lynchpin.core.projects import project_profiles

        profiles = project_profiles()
        assert profiles["sinnix"].path == Path("/realm/project/sinnix").resolve()
        assert profiles["sinnix"].classify("modules/services/example.nix") == "module"
        assert profiles["sinex"].classify("crate/foo/tests/bar_test.rs") == "tests"
        assert profiles["_analysis"].classify("history_cleanup/main.py") == "analysis"
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_sessions_source_reads_markdown_docs_directly() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.sources.indices import sessions

        records = list(sessions.iter_sessions())
        assert records, "expected session records from docs/reference/sessions"
        first = records[0]
        assert "docs/reference/sessions/" in first.doc_path, first.doc_path
        assert first.date.isoformat().startswith("2025-"), first.date
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_dashboard_export_uses_calendar_summary_shape() -> None:
    script = textwrap.dedent(
        """
        import json
        import tempfile
        from pathlib import Path

        from lynchpin.views.export_dashboard_data import export_dashboard_data

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dashboard.json"
            export_dashboard_data(output, timeline_days=3, recent_days=2)
            payload = json.loads(output.read_text(encoding="utf-8"))

        assert len(payload["timeline"]) == 3, payload["timeline"]
        assert len(payload["recent_calendar"]) == 2, payload["recent_calendar"]
        first_day = payload["recent_calendar"][0]
        assert "focus_minutes" in first_day, first_day
        assert "command_total" in first_day, first_day
        assert "git_commits" in first_day, first_day
        assert "content" not in first_day, first_day
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr
