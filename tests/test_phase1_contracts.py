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
        assert profiles["sinity-lynchpin"].classify("lynchpin/views/calendar_views.py") == "analysis"
        assert profiles["knowledgebase"].classify("notes/today.md") == "docs"
        assert "_analysis" not in profiles
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


def test_calendar_views_and_narratives_use_summary_helpers() -> None:
    views_text = (REPO_ROOT / "lynchpin/views/calendar_views.py").read_text(encoding="utf-8")
    narratives_text = (REPO_ROOT / "lynchpin/views/calendar_narratives.py").read_text(encoding="utf-8")
    assert "load_day_summary" in views_text
    assert "terminal_capture_overview_line" in views_text
    assert "load_day_summary" in narratives_text
    assert "summarize_range" in narratives_text
    assert "_instrumentation_line" not in narratives_text


def test_baseline_module_writes_core_git_output() -> None:
    baseline_text = (REPO_ROOT / "lynchpin/system/baseline.py").read_text(encoding="utf-8")
    assert "git_numstat.jsonl" in baseline_text


def test_analysis_governance_points_at_absorbed_artifact_layout() -> None:
    for relpath in [
        "lynchpin/analysis/governance/claim_registry.py",
        "lynchpin/analysis/governance/analysis_status.py",
        "lynchpin/analysis/governance/denominator_registry.py",
    ]:
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert "artefacts/analysis/derived/" in text
        assert "data/derived/" not in text


def test_warehouse_cli_uses_documented_subcommands() -> None:
    for command in ("build", "materialize", "refresh"):
        result = subprocess.run(
            ["nix", "develop", "--command", "python", "-m", "lynchpin.views.warehouse", command, "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "--sources" in result.stdout


def test_hpi_validation_defaults_to_curated_active_profile() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.system import validate

        registry = {name: None for name in (
            "my.coding.commits",
            "my.calendar.holidays",
            "my.fbmessenger",
            "my.smscalls",
            "my.sleep.manual",
            "my.money",
            "my.webhistory",
            "my.browser",
            "my.google.takeout.parser",
            "my.goodreads",
            "my.spotify.gdpr",
            "my.activitywatch",
            "my.activitywatch.active_window",
            "my.atuin",
            "my.github.all",
            "my.zsh",
        )}

        selected = validate._select_hpi_modules(profile="active", modules=[], registry=registry)
        assert selected == list(validate.ACTIVE_HPI_MODULES)
        assert "my.fbmessenger" in selected
        assert "my.browser" in selected
        assert "my.github.all" not in selected
        assert "my.zsh" not in selected
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_browser_hpi_config_accepts_multi_path_env_override() -> None:
    script = textwrap.dedent(
        """
        import os
        from pathlib import Path

        os.environ["HPI_BROWSER_EXPORT"] = "/tmp/browser/*.json:/tmp/browser/*.csv"

        from lynchpin.core.vendor import add_vendor_paths

        add_vendor_paths()

        from my.browser import export as browser_export

        assert isinstance(browser_export.config.export_path, tuple)
        assert browser_export.config.export_path == (
            Path("/tmp/browser/*.json"),
            Path("/tmp/browser/*.csv"),
        )
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_terminal_audit_detail_tracks_current_summary_fields() -> None:
    script = textwrap.dedent(
        """
        from pathlib import Path
        from lynchpin.sources.captures.instrumentation import TerminalAuditSummary
        from lynchpin.system.validate import _format_terminal_audit_detail

        summary = TerminalAuditSummary(
            cast_count=5,
            manifest_count=4,
            events_count=3,
            unreadable_header_count=1,
            missing_activity_estimate_count=2,
            header_only_count=1,
            degraded_count=1,
            damaged_count=1,
            quarantine_candidate_count=1,
            counts_by_generation={"modern": 5},
            counts_by_status={"ok": 3, "damaged": 1},
        )

        detail = _format_terminal_audit_detail(summary, Path("/tmp/asciinema"))
        assert "root=/tmp/asciinema" in detail
        assert "unreadable=1" in detail
        assert "damaged=1" in detail
        assert "legacy_meta_count" not in detail
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr
