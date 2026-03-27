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
        from lynchpin.views.warehouse.ops import _source_specs

        try:
            _source_specs(["does-not-exist"])
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
        from lynchpin.views.warehouse.ops import build_views
        from lynchpin.views.warehouse.specs import SOURCE_SPECS
        import tempfile
        from pathlib import Path
        import duckdb

        spec = SOURCE_SPECS[0]
        table_name = spec.tables[0].name
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "warehouse.duckdb"
            conn = duckdb.connect(str(db_path))
            conn.execute(f"CREATE VIEW {table_name} AS SELECT 1 AS stale_value")
            conn.close()

            build_views(
                output=db_path,
                root=tmp_path / "missing-root",
                output_format="parquet",
                sources=[spec.name],
            )

            conn = duckdb.connect(str(db_path))
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


def test_build_views_preserves_manifest_rows_for_unselected_sources() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path
        from lynchpin.views.warehouse.ops import build_views
        from lynchpin.views.warehouse.specs import SOURCE_SPECS
        import duckdb

        selected = SOURCE_SPECS[0]
        untouched = SOURCE_SPECS[1]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "warehouse.duckdb"
            conn = duckdb.connect(str(db_path))
            conn.execute(
                "CREATE TABLE warehouse_manifest ("
                "source TEXT, format TEXT, source_path TEXT, present_tables BIGINT, "
                "expected_tables BIGINT, updated_at TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO warehouse_manifest VALUES (?, ?, ?, ?, ?, now())",
                [untouched.name, "parquet", "/tmp/existing", 1, 1],
            )
            conn.close()

            build_views(
                output=db_path,
                root=tmp_path / "missing-root",
                output_format="parquet",
                sources=[selected.name],
            )

            conn = duckdb.connect(str(db_path))
            rows = conn.execute(
                "SELECT source FROM warehouse_manifest ORDER BY source"
            ).fetchall()
            conn.close()
            assert rows == sorted([(selected.name,), (untouched.name,)]), rows
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
        assert profiles["sinity-lynchpin"].classify("lynchpin/context/reports.py") == "analysis"
        assert profiles["knowledgebase"].classify("notes/today.md") == "docs"
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


def test_period_reports_live_in_context_layer() -> None:
    reports_text = (REPO_ROOT / "lynchpin/context/reports.py").read_text(encoding="utf-8")
    state_text = (REPO_ROOT / "lynchpin/context/current_state.py").read_text(encoding="utf-8")
    packet_text = (REPO_ROOT / "lynchpin/context/state_packets.py").read_text(encoding="utf-8")
    periods_text = (REPO_ROOT / "lynchpin/periods.py").read_text(encoding="utf-8")
    justfile_text = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/views/calendar_views.py").exists()
    assert not (REPO_ROOT / "lynchpin/retrospective/calendar.py").exists()
    assert not (REPO_ROOT / "lynchpin/context/state.py").exists()
    assert not (REPO_ROOT / "lynchpin/context/packets.py").exists()
    assert not (REPO_ROOT / "lynchpin/retrospective/temporal.py").exists()
    assert not (REPO_ROOT / "lynchpin/context/packet_builders.py").exists()
    assert "build_period_reports" in reports_text
    assert "build_period_evidence_bundle" in reports_text
    assert "query_evidence_range" in state_text
    assert "summarize_weeks" in state_text
    assert "summarize_months" in state_text
    assert "summarize_quarters" in state_text
    assert "summarize_years" in state_text
    assert "trajectory_day" not in state_text
    assert "trajectory_month" not in state_text
    assert "trajectory_episode" not in state_text
    assert "trajectory_anomaly" not in state_text
    assert "trajectory_chain" not in state_text
    assert "build_day_packet" in packet_text
    assert "build_week_packet" in packet_text
    assert "build_month_packet" in packet_text
    assert "build_thread_packets" in packet_text
    assert "period_keys_in_range" in periods_text
    assert "\ncontext-bundle " in justfile_text
    assert "python -m lynchpin.context.bundles" in justfile_text
    assert "context-state" not in justfile_text
    assert "python -m lynchpin.context.state" not in justfile_text


def test_narrative_surface_has_no_standalone_runner_or_generic_orchestration_package() -> None:
    narrative_text = (REPO_ROOT / "lynchpin/retrospective/narrative.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "scripts/run_dynamic_narratives.py").exists()
    assert not (REPO_ROOT / "scripts/run-full-retrospective.sh").exists()
    assert not (REPO_ROOT / "lynchpin/orchestration/__init__.py").exists()
    assert "jsonl" not in narrative_text.lower()
    assert "flat path" not in narrative_text.lower()


def test_period_report_docs_point_at_context_reports() -> None:
    text = (REPO_ROOT / "docs/reference/period-reports.md").read_text(encoding="utf-8")
    assert "calendar dossier" not in text.lower()
    assert "lynchpin.context.reports" in text
    assert "build_period_reports" in text


def test_project_analysis_surfaces_have_no_velocity_wrapper() -> None:
    velocity_analysis = (REPO_ROOT / "lynchpin/analysis/projects/velocity_analysis.py").read_text(encoding="utf-8")
    velocity_renderer = (REPO_ROOT / "lynchpin/analysis/projects/velocity_renderer.py").read_text(encoding="utf-8")
    bundles_api = (REPO_ROOT / "lynchpin/analysis/projects/bundles.py").read_text(encoding="utf-8")
    rich_bundles_api = (REPO_ROOT / "lynchpin/analysis/projects/rich_bundles.py").read_text(encoding="utf-8")
    projects_cli = (REPO_ROOT / "lynchpin/analysis/projects/cli.py").read_text(encoding="utf-8")
    justfile_text = (REPO_ROOT / "justfile").read_text(encoding="utf-8")

    assert not (REPO_ROOT / "lynchpin/analysis/projects/velocity.py").exists()
    assert not (REPO_ROOT / "lynchpin/views/velocity.py").exists()
    assert not (REPO_ROOT / "lynchpin/views/project_bundles.py").exists()
    assert "select_project_profiles(" in velocity_analysis
    assert "build_velocity_dashboard(" in velocity_renderer
    assert "generate_project_bundle(" in bundles_api
    assert "generate_rich_project_bundle(" in rich_bundles_api
    assert "argparse" not in bundles_api
    assert "argparse" not in rich_bundles_api
    assert "argparse" in projects_cli
    assert "\nvelocity " in justfile_text
    assert "\nproject-bundles " in justfile_text
    assert "\nproject-bundles-rich " in justfile_text
    assert "python -m lynchpin.analysis.projects velocity" in justfile_text
    assert "python -m lynchpin.analysis.projects bundles" in justfile_text
    assert "python -m lynchpin.analysis.projects rich-bundles" in justfile_text
    assert "python -c 'from pathlib import Path; from lynchpin.analysis.projects" not in justfile_text


def test_project_analysis_docs_point_at_reusable_apis() -> None:
    velocity_text = (REPO_ROOT / "docs/reference/velocity.md").read_text(encoding="utf-8")
    bundles_text = (REPO_ROOT / "docs/reference/project-bundles.md").read_text(encoding="utf-8")
    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "lynchpin.analysis.projects.velocity_renderer.build_velocity_dashboard" in velocity_text
    assert "lynchpin.analysis.projects.velocity_analysis" in velocity_text
    assert "lynchpin.analysis.projects.bundles import build_project_bundles" in bundles_text
    assert "lynchpin.analysis.projects.rich_bundles import build_rich_project_bundles" in bundles_text
    assert "python -m lynchpin.analysis.projects velocity" in velocity_text
    assert "python -m lynchpin.analysis.projects bundles" in bundles_text
    assert "python -m lynchpin.analysis.projects rich-bundles" in bundles_text
    assert "lynchpin.analysis.projects.velocity_renderer.build_velocity_dashboard(...)" in readme_text
    assert "just velocity" in velocity_text
    assert "just project-bundles" in bundles_text
    assert "just project-bundles-rich" in bundles_text


def test_knowledge_materializers_use_api_plus_just() -> None:
    justfile_text = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    sessions_text = (REPO_ROOT / "docs/reference/sessions/README.md").read_text(encoding="utf-8")
    ledgers_text = (REPO_ROOT / "docs/reference/ledgers/README.md").read_text(encoding="utf-8")
    knowledge_cli_text = (REPO_ROOT / "lynchpin/analysis/knowledge/cli.py").read_text(encoding="utf-8")

    assert not (REPO_ROOT / "lynchpin/views/ledgers.py").exists()
    assert not (REPO_ROOT / "lynchpin/views/session_summaries.py").exists()
    assert "argparse" in knowledge_cli_text
    assert "\nsession-index " in justfile_text
    assert "\nartefact-index " in justfile_text
    assert "\nsummarise-session " in justfile_text
    assert "python -m lynchpin.analysis.knowledge session-index" in justfile_text
    assert "python -m lynchpin.analysis.knowledge artefact-index" in justfile_text
    assert "python -m lynchpin.analysis.knowledge summarise-session" in justfile_text
    assert "python -c 'from pathlib import Path; from lynchpin.analysis.knowledge" not in justfile_text
    assert "python -m lynchpin.analysis.knowledge summarise-session" in sessions_text
    assert "python -m lynchpin.analysis.knowledge artefact-index" in ledgers_text
    assert "just summarise-session" in sessions_text
    assert "just artefact-index" in ledgers_text


def test_package_init_files_are_markers_not_barrel_exports() -> None:
    for relpath in (
        "lynchpin/__init__.py",
        "lynchpin/context/__init__.py",
        "lynchpin/metrics/__init__.py",
        "lynchpin/retrospective/__init__.py",
        "lynchpin/views/warehouse/__init__.py",
        "lynchpin/analysis/projects/__init__.py",
        "lynchpin/analysis/knowledge/__init__.py",
        "lynchpin/system/_baseline/__init__.py",
    ):
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert "from ." not in text
        assert "__all__" not in text


def test_period_reports_cli_builds_day_reports_from_root() -> None:
    result = subprocess.run(
        [
            "nix",
            "develop",
            "--command",
            "python",
            "-m",
            "lynchpin.context.reports",
            "2026-03-16",
            "2026-03-17",
            "--scale",
            "day",
            "--no-write-files",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "\"key\": \"2026-03-16\"" in result.stdout


def test_warehouse_docs_list_context_and_session_summary_tables() -> None:
    text = (REPO_ROOT / "docs/reference/warehouse.md").read_text(encoding="utf-8")
    assert "context_day" in text
    assert "context_week" in text
    assert "session_summaries" in text


def test_personal_trajectory_program_no_longer_refers_to_deleted_calendar_bridge() -> None:
    text = (REPO_ROOT / "docs/plans/personal-trajectory-program.md").read_text(encoding="utf-8")
    assert "lynchpin.context.calendar" not in text
    assert "TrajectoryDay" not in text
    assert "trajectory.week" not in text
    assert "day/week/month semantics" in text


def test_lynchpin_roadmap_frames_sinex_as_handoff_contracts() -> None:
    text = (REPO_ROOT / "docs/plans/lynchpin-roadmap.md").read_text(encoding="utf-8")
    assert "direct module imports or warehouse tables" not in text
    assert "reference implementation" in text
    assert "canonical inputs" in text


def test_sinex_integration_plan_avoids_runtime_adapter_language() -> None:
    text = (REPO_ROOT / "docs/plans/sinex-integration.md").read_text(encoding="utf-8")
    assert "gateway" not in text
    assert "Dual Run" not in text
    assert "reference implementation" in text
    assert "input contracts" in text


def test_baseline_module_writes_core_git_output() -> None:
    baseline_text = (REPO_ROOT / "lynchpin/system/baseline.py").read_text(encoding="utf-8")
    assert "git_numstat.jsonl" in baseline_text


def test_baseline_orchestration_uses_internal_subsystem_modules() -> None:
    baseline_text = (REPO_ROOT / "lynchpin/system/baseline.py").read_text(encoding="utf-8")
    assert "from ._baseline.activitywatch import" in baseline_text
    assert "from ._baseline.shared import" in baseline_text
    assert (REPO_ROOT / "lynchpin/system/_baseline/activitywatch.py").exists()
    assert (REPO_ROOT / "lynchpin/system/_baseline/git.py").exists()


def test_analysis_governance_points_at_absorbed_artifact_layout() -> None:
    for relpath in [
        "lynchpin/analysis/governance/claim_registry.py",
        "lynchpin/analysis/governance/analysis_status.py",
        "lynchpin/analysis/governance/denominator_registry.py",
    ]:
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert "artefacts/analysis/derived/" in text


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


def test_hpi_validation_defaults_to_supported_set() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.system import validate_hpi

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
        )}

        selected = validate_hpi._select_hpi_modules(modules=[], registry=registry)
        assert selected == list(validate_hpi.ACTIVE_HPI_MODULES)
        assert "my.fbmessenger" in selected
        assert "my.browser" in selected
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_validate_surface_is_split_by_owner() -> None:
    validate_text = (REPO_ROOT / "lynchpin/system/validate.py").read_text(encoding="utf-8")
    assert (REPO_ROOT / "lynchpin/system/validate_common.py").exists()
    assert (REPO_ROOT / "lynchpin/system/validate_hpi.py").exists()
    assert "from .validate_common import" in validate_text
    assert "from .validate_hpi import" in validate_text
    assert "class CheckResult" not in validate_text
    assert "def _run_check(" not in validate_text
    assert "def _count_iter(" not in validate_text
    assert "def _sample_iter(" not in validate_text
    assert "ACTIVE_HPI_MODULES: tuple[str, ...]" not in validate_text


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
        from lynchpin.sources.captures.terminal_capture import TerminalAuditSummary
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
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_capture_instrumentation_surface_is_split_by_domain() -> None:
    idempotency_text = (REPO_ROOT / "docs/reference/lynchpin-idempotency.md").read_text(encoding="utf-8")
    temporal_text = (REPO_ROOT / "docs/reference/knowledge-graph/temporal-encoding.md").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/sources/captures/instrumentation.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/captures/terminal_capture.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/captures/media_capture.py").exists()
    assert "lynchpin.sources.captures.terminal_capture" in idempotency_text
    assert "lynchpin.sources.captures.media_capture" in idempotency_text
    assert "sources.captures.terminal_capture" in temporal_text
    assert "sources.captures.media_capture" in temporal_text


def test_terminal_capture_surface_is_split_by_owner() -> None:
    terminal_capture_text = (
        REPO_ROOT / "lynchpin/sources/captures/terminal_capture.py"
    ).read_text(encoding="utf-8")
    assert (REPO_ROOT / "lynchpin/sources/captures/terminal_capture_types.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/captures/terminal_capture_support.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/captures/terminal_capture_parsers.py").exists()
    assert "def _parse_terminal_session(" not in terminal_capture_text
    assert "def _audit_terminal_session(" not in terminal_capture_text
    assert "def _scan_cast_timings(" not in terminal_capture_text
    assert "from .terminal_capture_parsers import" in terminal_capture_text
    assert "from .terminal_capture_support import" in terminal_capture_text
    assert "from .terminal_capture_types import" in terminal_capture_text


def test_life_range_cli_defaults_to_latest_surface() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.retrospective.life_paths import (
            DEFAULT_LIFE_START,
            LATEST_LIFE_JSON,
        )

        assert DEFAULT_LIFE_START == "2013-10"
        assert LATEST_LIFE_JSON.name == "monthly_life_latest.json"
        assert str(LATEST_LIFE_JSON).startswith("artefacts/retrospective/life-range/")
        """
    )
    script_result = _run_repo_python(script)
    assert script_result.returncode == 0, script_result.stderr

    result = subprocess.run(
        ["nix", "develop", "--command", "python", "-m", "lynchpin.retrospective.life", "build", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--takeout-root" in result.stdout
    assert "--takeout" in result.stdout
    assert "2013-10" in result.stdout
    assert "--revolut-annotated" in result.stdout


def test_takeout_source_discovery_prefers_seed_archives() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import takeout_archives

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20250124T064512Z-002.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
                "ezodev_takeout-20200106T040337Z-001.tgz",
            ):
                (root / name).touch()

            discovered = [path.name for path in takeout_archives.discover_seed_archives(root)]
            assert discovered == [
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
            ], discovered

            resolved = [path.name for path in takeout_archives.resolve_archives(explicit_seeds=[], root=root)]
            assert resolved == [
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20250124T064512Z-002.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
            ], resolved
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_wykop_export_cli_lives_under_ingest_boundary() -> None:
    result = subprocess.run(
        ["python", "-m", "lynchpin.ingest.wykop_export", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--backend" in result.stdout
    assert "--collection" in result.stdout
    assert not (REPO_ROOT / "lynchpin/sources/exports/wykop_export.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/wykop_export.py").exists()


def test_wykop_api_surface_stays_split_by_owner() -> None:
    api_text = (REPO_ROOT / "lynchpin/ingest/wykop_api.py").read_text(encoding="utf-8")
    parse_text = (REPO_ROOT / "lynchpin/ingest/wykop_api_parse.py").read_text(encoding="utf-8")
    extras_text = (REPO_ROOT / "lynchpin/ingest/wykop_api_extras.py").read_text(encoding="utf-8")
    export_text = (REPO_ROOT / "lynchpin/ingest/wykop_export.py").read_text(encoding="utf-8")

    assert "class WykopApiClient" in api_text
    assert "def api_iter_pages" in api_text
    assert "def parse_api_entries" not in api_text
    assert "def scrape_api_extras" not in api_text
    assert "API_SPECS" in parse_text
    assert "parse_api_entry_comments" in parse_text
    assert "def scrape_api_extras" in extras_text
    assert "from .wykop_api import WykopApiClient" in export_text
    assert "from .wykop_api_parse import API_SPECS" in export_text
    assert "from .wykop_api_extras import scrape_api_extras" in export_text


def test_webhistory_cli_stays_split_by_owner() -> None:
    result = subprocess.run(
        ["python", "-m", "lynchpin.ingest.webhistory", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "full-history" in result.stdout
    assert "compare" in result.stdout
    assert "audit" in result.stdout

    cli_text = (REPO_ROOT / "lynchpin/ingest/webhistory.py").read_text(encoding="utf-8")
    assert "def _write_dedup_output" not in cli_text
    assert "def _compare_gestalt" not in cli_text
    assert "def _audit_webhistory" not in cli_text
    assert (REPO_ROOT / "lynchpin/ingest/webhistory_dedup.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/webhistory_compare.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/webhistory_audit.py").exists()


def test_knowledge_graph_cli_stays_split_by_owner() -> None:
    result = subprocess.run(
        ["python", "-m", "lynchpin.views.knowledge_graph", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "build-temporal" in result.stdout

    cli_text = (REPO_ROOT / "lynchpin/views/knowledge_graph.py").read_text(encoding="utf-8")
    assert "class Node" not in cli_text
    assert "def parse_markdown" not in cli_text
    assert "def build_temporal_edges" not in cli_text
    assert (REPO_ROOT / "lynchpin/views/knowledge_graph_markdown.py").exists()
    assert (REPO_ROOT / "lynchpin/views/knowledge_graph_temporal.py").exists()


def test_fbmessenger_export_cli_stays_split_by_owner() -> None:
    result = subprocess.run(
        ["python", "-m", "lynchpin.ingest.fbmessenger_export", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--cookie-db" in result.stdout
    assert "--launch-debug-chrome" in result.stdout

    cli_text = (REPO_ROOT / "lynchpin/ingest/fbmessenger_export.py").read_text(encoding="utf-8")
    assert "class MessengerExportDb" not in cli_text
    assert "def resolve_cookie_json" not in cli_text
    assert "fbchat._state" not in cli_text
    assert (REPO_ROOT / "lynchpin/ingest/fbmessenger_db.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/fbmessenger_chrome.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/fbmessenger_cookies.py").exists()
    assert (REPO_ROOT / "lynchpin/ingest/fbmessenger_patch.py").exists()


def test_warehouse_specs_are_split_by_domain() -> None:
    specs_text = (REPO_ROOT / "lynchpin/views/warehouse/specs.py").read_text(encoding="utf-8")
    assert "from .specs_analysis import ANALYSIS_TABLE_SPECS" in specs_text
    assert "from .specs_sources import SOURCE_TABLE_SPECS" in specs_text
    assert "from .specs_context import CONTEXT_TABLE_SPECS" in specs_text
    assert "from .specs_processed import PROCESSED_TABLE_SPECS" in specs_text
    assert "from .rows_sources import" not in specs_text
    assert "from .rows_context_rollups import" not in specs_text
    assert "from .rows_processed import" not in specs_text
    assert "from .rows_analysis import" not in specs_text
    assert (REPO_ROOT / "lynchpin/views/warehouse/specs_analysis.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/specs_sources.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/specs_context.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/specs_processed.py").exists()


def test_warehouse_source_rows_are_split_by_domain() -> None:
    specs_sources_text = (REPO_ROOT / "lynchpin/views/warehouse/specs_sources.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/views/warehouse/rows_sources.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_sources_captures.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_sources_exports.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_sources_indices.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_sources_libraries.py").exists()
    assert "from .rows_sources_captures import" in specs_sources_text
    assert "from .rows_sources_exports import" in specs_sources_text
    assert "from .rows_sources_indices import" in specs_sources_text
    assert "from .rows_sources_libraries import" in specs_sources_text
    assert "from .rows_sources import" not in specs_sources_text


def test_warehouse_processed_rows_are_split_by_owner() -> None:
    specs_text = (REPO_ROOT / "lynchpin/views/warehouse/specs_processed.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/views/warehouse/rows_processed.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_processed_range.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_processed_activity.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_processed_git.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_processed_metrics.py").exists()
    assert "from .rows_processed_activity import" in specs_text
    assert "from .rows_processed_git import" in specs_text
    assert "from .rows_processed_metrics import" in specs_text
    assert "from .rows_processed import" not in specs_text


def test_warehouse_context_rows_are_split_by_owner() -> None:
    specs_text = (REPO_ROOT / "lynchpin/views/warehouse/specs_context.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/views/warehouse/rows_context_rollups.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_context_snapshot.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_context_day.py").exists()
    assert (REPO_ROOT / "lynchpin/views/warehouse/rows_context_periods.py").exists()
    assert "from .rows_context_day import" in specs_text
    assert "from .rows_context_periods import" in specs_text
    assert "from .rows_context_rollups import" not in specs_text


def test_life_range_surface_uses_source_aggregation_helpers() -> None:
    text = (REPO_ROOT / "lynchpin/retrospective/life.py").read_text(encoding="utf-8")
    api_text = (REPO_ROOT / "lynchpin/retrospective/life_range.py").read_text(encoding="utf-8")
    sources_text = (REPO_ROOT / "lynchpin/retrospective/life_range_sources.py").read_text(encoding="utf-8")
    payload_text = (REPO_ROOT / "lynchpin/retrospective/life_range_payload.py").read_text(encoding="utf-8")
    outputs_text = (REPO_ROOT / "lynchpin/retrospective/life_range_outputs.py").read_text(encoding="utf-8")
    assert "build_life_range(" in text
    assert not (REPO_ROOT / "lynchpin/system/life_timeline/__init__.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_periods.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_range_models.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_range_sources.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_range_payload.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_range_outputs.py").exists()
    assert "collect_life_range_evidence(" in api_text
    assert "build_life_range_payload(" in api_text
    assert "write_life_range_outputs(" in api_text
    assert "lp_reddit.summarize_activity(" not in api_text
    assert "build_month_summary(" not in api_text
    assert "render_markdown(" not in api_text
    assert "lp_reddit.summarize_activity(" in sources_text
    assert "lp_wykop.summarize_activity(" in sources_text
    assert "lp_raindrop.summarize_bookmarks(" in sources_text
    assert "lp_gitstats.summarize_commit_activity(" in sources_text
    assert "lp_spotify.summarize_streaming(" in sources_text
    assert "build_recent_context_summaries(" in sources_text
    assert "lp_spotify.top_names(" in payload_text
    assert "build_month_summary(" in payload_text
    assert "build_output_summary(" in payload_text
    assert "render_markdown(" in outputs_text


def test_life_summary_surface_is_split_by_owner() -> None:
    payload_text = (REPO_ROOT / "lynchpin/retrospective/life_range_payload.py").read_text(encoding="utf-8")
    sources_text = (REPO_ROOT / "lynchpin/retrospective/life_range_sources.py").read_text(encoding="utf-8")
    outputs_text = (REPO_ROOT / "lynchpin/retrospective/life_range_outputs.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/retrospective/life_summary.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_summary_models.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_summary_builders.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_summary_context.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_summary_rendering.py").exists()
    assert (REPO_ROOT / "lynchpin/retrospective/life_summary_utils.py").exists()
    assert "build_work_summary(" in payload_text
    assert "build_intake_summary(" in payload_text
    assert "build_mail_summary(" in payload_text
    assert "build_location_summary(" in payload_text
    assert "build_money_summary(" in payload_text
    assert "build_health_summary(" in payload_text
    assert "build_notes_summary(" in payload_text
    assert "render_markdown(" in outputs_text
    assert "lp_takeout_common.tokenize_topic" in sources_text or "lp_takeout_common.tokenize_topic" in payload_text
    assert "lp_takeout_youtube.summarize_youtube_watch_history_month(" in payload_text
    assert "lp_takeout_youtube.phrase_topic_tokens(" in payload_text
    assert "lp_takeout_life.parse_life_takeouts(" in sources_text
    assert "lp_knowledgebase.summarize_onenote_journal_entries(" in sources_text
    assert "lp_takeout_archives.resolve_archives(" in sources_text
    assert "lp_takeout_youtube.load_youtube_oembed_cache(" in sources_text
    assert "def tokenize_topic(" not in payload_text
    assert not (REPO_ROOT / "lynchpin/sources/exports/takeout.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/exports/takeout_archives.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/exports/takeout_common.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/exports/takeout_life.py").exists()
    assert (REPO_ROOT / "lynchpin/sources/exports/takeout_youtube.py").exists()


def test_takeout_life_bundle_parser_handles_sparse_archive() -> None:
    script = textwrap.dedent(
        """
        import io
        import tarfile
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import takeout_life

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "takeout-20260101-001.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                data = b"not relevant"
                info = tarfile.TarInfo("Takeout/Archive Browser.html")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

            bundle = takeout_life.parse_life_takeouts(
                [archive],
                start_month="2026-01",
                end_month="2026-03",
            )

            assert bundle.google_search_counts == {}
            assert bundle.youtube_video_titles == {}
            assert bundle.chrome_history_counts == {}
            assert bundle.location_takeout_path is None
            assert bundle.gmail_takeout_path is None
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_takeout_youtube_watch_history_month_helper_resolves_oembed_fallbacks() -> None:
    script = textwrap.dedent(
        """
        from collections import Counter

        from lynchpin.sources.exports import takeout_youtube

        video_ids = Counter({"abc123xyz00": 2, "bad": 9})
        titles = Counter()
        channels = Counter()
        cache = {"abc123xyz00": {"ok": True, "title": "DuckDB Deep Dive", "author_name": "Data Channel"}}

        top_ids, resolved_titles, resolved_channels, title_tokens = takeout_youtube.summarize_youtube_watch_history_month(
            video_ids,
            titles,
            channels,
            takeout_titles={},
            oembed_cache=cache,
        )

        assert top_ids[0] == ("bad", 9)
        assert resolved_titles["DuckDB Deep Dive"] == 2
        assert resolved_channels["Data Channel"] == 2
        assert title_tokens["duckdb"] == 2
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_takeout_phrase_topic_tokens_and_spotify_top_names() -> None:
    script = textwrap.dedent(
        """
        from collections import Counter

        from lynchpin.sources.exports import spotify, takeout_youtube

        tokens = takeout_youtube.phrase_topic_tokens(Counter({"DuckDB json": 2, "the and": 5}))
        top = spotify.top_names({"2026-03": Counter({"Autechre": 30, "Biosphere": 20})}, "2026-03", limit=1)

        assert tokens["duckdb"] == 2
        assert tokens["json"] == 2
        assert "the" not in tokens
        assert top == ["Autechre"]
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_reddit_source_summarize_activity() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import reddit

        def tokenize(text: str):
            return text.lower().split()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comments = root / "comments.csv"
            comments.write_text(
                "id,date,subreddit,body,permalink,parent,gildings\\n"
                "c1,2024-01-02T03:04:05+00:00,Python,hello world,/r/python,p0,\\n"
                "c2,2024-02-02T03:04:05+00:00,Python,other words,/r/python,p1,\\n",
                encoding="utf-8",
            )
            posts = root / "posts.csv"
            posts.write_text(
                "id,date,subreddit,title,body,url,gildings\\n"
                "p1,2024-01-03T03:04:05+00:00,Python,title,body,https://example.com,\\n",
                encoding="utf-8",
            )
            messages = root / "messages.csv"
            messages.write_text(
                "id,date,thread_id,from,to,permalink\\n"
                "m1,2024-01-04T03:04:05+00:00,t1,a,b,https://example.com\\n",
                encoding="utf-8",
            )

            summary = reddit.summarize_activity(
                "2024-01",
                "2024-02",
                comments_paths=[comments],
                posts_paths=[posts],
                message_paths=[messages],
                tokenize_text=tokenize,
            )

            assert summary.comment_counts == {"2024-01": 1, "2024-02": 1}
            assert summary.post_counts == {"2024-01": 1}
            assert summary.message_counts == {"2024-01": 1}
            assert summary.comment_subreddits["2024-01"]["Python"] == 1
            assert summary.comment_tokens["2024-01"]["hello"] == 1
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_wykop_source_summarize_activity() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import wykop

        def tokenize(text: str):
            return text.lower().split()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link_comments = root / "link_comments.jsonl"
            link_comments.write_text(
                '{"comment_id": 1, "comment_created_at": "2024-01-10 12:00:00", "comment_content": "alpha beta", "link_tags": ["tag1"]}\\n',
                encoding="utf-8",
            )
            entries = root / "entries.jsonl"
            entries.write_text(
                '{"entry_id": 2, "entry_created_at": "2024-01-11 12:00:00", "entry_content": "gamma delta", "entry_tags": ["tag2"]}\\n',
                encoding="utf-8",
            )
            entry_comments = root / "entry_comments.jsonl"
            entry_comments.write_text(
                '{"comment_id": 3, "comment_created_at": "2024-02-11 12:00:00", "comment_content": "epsilon zeta"}\\n',
                encoding="utf-8",
            )

            summary = wykop.summarize_activity(
                "2024-01",
                "2024-02",
                link_comments_path=link_comments,
                entries_path=entries,
                entry_comments_path=entry_comments,
                tokenize_text=tokenize,
            )

            assert summary.link_comment_counts == {"2024-01": 1}
            assert summary.entry_counts == {"2024-01": 1}
            assert summary.entry_comment_counts == {"2024-02": 1}
            assert summary.link_comment_tags["2024-01"]["tag1"] == 1
            assert summary.entry_tokens["2024-01"]["gamma"] == 1
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_raindrop_source_summarize_bookmarks() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import raindrop

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raindrop.csv"
            path.write_text(
                "id,title,url,folder,tags,created,note,excerpt,cover,favorite\\n"
                "1,One,https://example.com,Root,tag,2024-01-15T00:00:00+00:00,,,,false\\n"
                "2,Two,https://example.org,Root,tag,2024-02-15T00:00:00+00:00,,,,false\\n",
                encoding="utf-8",
            )

            summary = raindrop.summarize_bookmarks("2024-01", "2024-02", csv_path=path)
            assert summary == {"2024-01": 1, "2024-02": 1}
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_spotify_source_summarize_streaming() -> None:
    script = textwrap.dedent(
        """
        import json
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import spotify

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root / "Spotify Account Data"
            account.mkdir(parents=True)
            (account / "StreamingHistory_music_0.json").write_text(
                json.dumps(
                    [
                        {
                            "endTime": "2024-01-03 10:00",
                            "artistName": "Artist One",
                            "trackName": "Track One",
                            "msPlayed": 180000,
                        },
                        {
                            "endTime": "2024-02-03 10:00",
                            "artistName": "Artist Two",
                            "trackName": "Track Two",
                            "msPlayed": 360000,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            summary = spotify.summarize_streaming("2024-01", "2024-02", root=root)
            assert round(summary.hours["2024-01"], 3) == 0.05
            assert round(summary.hours["2024-02"], 3) == 0.1
            assert summary.artists["2024-01"]["Artist One"] == 180000
            assert summary.tracks["2024-02"]["Track Two"] == 360000
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_knowledgebase_source_summaries() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        from lynchpin.sources.libraries import knowledgebase

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onenote = root / "onenote.md"
            onenote.write_text(
                "### 02.01.2024\\nentry\\n### 10.02.2024\\nentry\\n",
                encoding="utf-8",
            )
            substance = root / "substance.md"
            substance.write_text(
                "#### 20.05.2022 to 24.07.2022 - stretch\\n#### 23.10.2022\\n",
                encoding="utf-8",
            )

            onenote_counts = knowledgebase.summarize_onenote_journal_entries(onenote, "2024-01", "2024-02")
            substance_counts = knowledgebase.summarize_substance_log_headings(substance, "2022-05", "2022-10")

            assert onenote_counts == {"2024-01": 1, "2024-02": 1}
            assert substance_counts == {"2022-05": 1, "2022-06": 1, "2022-07": 1, "2022-10": 1}
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_takeout_source_youtube_oembed_helpers() -> None:
    script = textwrap.dedent(
        """
        import json
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import takeout_youtube

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "youtube_oembed_cache.jsonl"
            cache_path.write_text(
                json.dumps(
                    {
                        "video_id": "abc123xyz09",
                        "ok": True,
                        "title": "Recovered Title",
                        "author_name": "Recovered Channel",
                    }
                ) + "\\n",
                encoding="utf-8",
            )

            cache = takeout_youtube.load_youtube_oembed_cache(cache_path)
            title, channel = takeout_youtube.resolve_youtube_video_meta(
                "abc123xyz09",
                takeout_titles={},
                oembed_cache=cache,
            )

            assert title == "Recovered Title"
            assert channel == "Recovered Channel"
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr
