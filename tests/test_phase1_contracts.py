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


def test_build_views_preserves_manifest_rows_for_unselected_sources() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path
        from lynchpin.views import warehouse

        selected = warehouse.SOURCE_SPECS[0]
        untouched = warehouse.SOURCE_SPECS[1]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "warehouse.duckdb"
            conn = warehouse.duckdb.connect(str(db_path))
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

            warehouse.build_views(
                output=db_path,
                root=tmp_path / "missing-root",
                output_format="parquet",
                sources=[selected.name],
            )

            conn = warehouse.duckdb.connect(str(db_path))
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
        assert profiles["sinity-lynchpin"].classify("lynchpin/views/calendar_views.py") == "analysis"
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


def test_calendar_views_and_retrospective_api_use_shared_trajectory_layer() -> None:
    views_text = (REPO_ROOT / "lynchpin/views/calendar_views.py").read_text(encoding="utf-8")
    calendar_text = (REPO_ROOT / "lynchpin/retrospective/calendar.py").read_text(encoding="utf-8")
    retrospective_text = (REPO_ROOT / "lynchpin/retrospective/narrative.py").read_text(encoding="utf-8")
    assert not (REPO_ROOT / "lynchpin/views/calendar_narratives.py").exists()
    assert "build_calendar_views" in views_text
    assert "CalendarScale.day" in views_text
    assert "load_date_window" in calendar_text
    assert "summarize_window_months" in calendar_text
    assert "generate_date_range_narrative" in retrospective_text
    assert "load_date_window" in retrospective_text
    assert "NarrativeKind.range" in retrospective_text


def test_calendar_docs_track_trajectory_surface() -> None:
    text = (REPO_ROOT / "docs/reference/calendar-views.md").read_text(encoding="utf-8")
    assert "context.calendar" not in text
    assert "TrajectoryDay.to_dict()" in text
    assert "lynchpin.views.calendar_views" in text


def test_project_analysis_wrappers_are_thin_materializers() -> None:
    velocity_api = (REPO_ROOT / "lynchpin/analysis/projects/velocity.py").read_text(encoding="utf-8")
    bundles_api = (REPO_ROOT / "lynchpin/analysis/projects/bundles.py").read_text(encoding="utf-8")
    rich_bundles_api = (REPO_ROOT / "lynchpin/analysis/projects/rich_bundles.py").read_text(encoding="utf-8")
    projects_cli = (REPO_ROOT / "lynchpin/analysis/projects/cli.py").read_text(encoding="utf-8")
    justfile_text = (REPO_ROOT / "justfile").read_text(encoding="utf-8")

    assert not (REPO_ROOT / "lynchpin/views/velocity.py").exists()
    assert not (REPO_ROOT / "lynchpin/views/project_bundles.py").exists()
    assert "build_velocity_dashboard(" in velocity_api
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

    assert "lynchpin.analysis.projects.build_velocity_dashboard" in velocity_text
    assert "lynchpin.analysis.projects.velocity" in velocity_text
    assert "lynchpin.analysis.projects import build_project_bundles" in bundles_text
    assert "lynchpin.analysis.projects import build_rich_project_bundles" in bundles_text
    assert "python -m lynchpin.analysis.projects velocity" in velocity_text
    assert "python -m lynchpin.analysis.projects bundles" in bundles_text
    assert "python -m lynchpin.analysis.projects rich-bundles" in bundles_text
    assert "lynchpin.analysis.projects.build_velocity_dashboard(...)" in readme_text
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


def test_calendar_views_root_cli_still_builds_day_views_without_subcommand() -> None:
    result = subprocess.run(
        [
            "nix",
            "develop",
            "--command",
            "python",
            "-m",
            "lynchpin.views.calendar_views",
            "2026-03-16",
            "2026-03-17",
            "--no-write-files",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "\"date\": \"2026-03-16\"" in result.stdout


def test_warehouse_docs_list_trajectory_and_session_summary_tables() -> None:
    text = (REPO_ROOT / "docs/reference/warehouse.md").read_text(encoding="utf-8")
    assert "trajectory_day" in text
    assert "trajectory_week" in text
    assert "session_summaries" in text


def test_personal_trajectory_program_no_longer_refers_to_deleted_calendar_bridge() -> None:
    text = (REPO_ROOT / "docs/plans/personal-trajectory-program.md").read_text(encoding="utf-8")
    assert "lynchpin.context.calendar" not in text
    assert "TrajectoryDay" in text
    assert "trajectory.week" in text


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


def test_life_timeline_week_narrative_uses_iso_week_identifier() -> None:
    text = (REPO_ROOT / "lynchpin/retrospective/narrative.py").read_text(encoding="utf-8")
    assert "week.iso_week" in text
    assert "w.week" not in text


def test_baseline_module_writes_core_git_output() -> None:
    baseline_text = (REPO_ROOT / "lynchpin/system/baseline.py").read_text(encoding="utf-8")
    assert "git_numstat.jsonl" in baseline_text


def test_baseline_orchestration_uses_internal_subsystem_modules() -> None:
    baseline_text = (REPO_ROOT / "lynchpin/system/baseline.py").read_text(encoding="utf-8")
    assert "from ._baseline import" in baseline_text
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
        )}

        selected = validate._select_hpi_modules(modules=[], registry=registry)
        assert selected == list(validate.ACTIVE_HPI_MODULES)
        assert "my.fbmessenger" in selected
        assert "my.browser" in selected
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
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_life_timeline_cli_defaults_to_latest_surface() -> None:
    script = textwrap.dedent(
        """
        from lynchpin.system.life_timeline_paths import (
            DEFAULT_LIFE_TIMELINE_START,
            LATEST_LIFE_TIMELINE_JSON,
        )

        assert DEFAULT_LIFE_TIMELINE_START == "2013-10"
        assert LATEST_LIFE_TIMELINE_JSON.name == "monthly_life_latest.json"
        """
    )
    script_result = _run_repo_python(script)
    assert script_result.returncode == 0, script_result.stderr

    result = subprocess.run(
        ["nix", "develop", "--command", "python", "-m", "lynchpin.system.life_timeline", "build", "--help"],
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

        from lynchpin.sources.exports import takeout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20250124T064512Z-002.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
                "ezodev_takeout-20200106T040337Z-001.tgz",
            ):
                (root / name).touch()

            discovered = [path.name for path in takeout.discover_seed_archives(root)]
            assert discovered == [
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
            ], discovered

            resolved = [path.name for path in takeout.resolve_archives(explicit_seeds=[], root=root)]
            assert resolved == [
                "takeout-20250124T064512Z-001.tgz",
                "takeout-20250124T064512Z-002.tgz",
                "takeout-20251214T223432Z-11-001.tgz",
            ], resolved
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_life_timeline_uses_source_aggregation_helpers() -> None:
    text = (REPO_ROOT / "lynchpin/system/life_timeline.py").read_text(encoding="utf-8")
    api_text = (REPO_ROOT / "lynchpin/retrospective/life_pipeline.py").read_text(encoding="utf-8")
    assert "retrospective.run_life_timeline(" in text
    assert "retrospective.generate_scale_narratives(" in text
    assert "lp_reddit.summarize_activity(" in api_text
    assert "lp_wykop.summarize_activity(" in api_text
    assert "lp_raindrop.summarize_bookmarks(" in api_text
    assert "lp_gitstats.summarize_commit_activity(" in api_text
    assert "lp_spotify.summarize_streaming(" in api_text
    assert "lp_spotify.top_names(" in api_text
    assert "build_recent_trajectory_summaries(" in api_text
    assert "build_month_summary(" in api_text
    assert "build_output_summary(" in api_text
    assert "build_work_summary(" in api_text
    assert "build_intake_summary(" in api_text
    assert "build_mail_summary(" in api_text
    assert "build_location_summary(" in api_text
    assert "build_money_summary(" in api_text
    assert "build_health_summary(" in api_text
    assert "build_notes_summary(" in api_text
    assert "render_markdown(" in api_text
    assert "lp_takeout.tokenize_topic" in api_text
    assert "lp_takeout.summarize_youtube_watch_history_month(" in api_text
    assert "lp_takeout.phrase_topic_tokens(" in api_text
    assert "lp_takeout.parse_life_timeline_takeouts(" in api_text
    assert "lp_knowledgebase.summarize_onenote_journal_entries(" in api_text
    assert "lp_takeout.resolve_archives(" in api_text
    assert "lp_takeout.load_youtube_oembed_cache(" in api_text
    assert "def tokenize_topic(" not in api_text


def test_takeout_life_timeline_bundle_parser_handles_sparse_archive() -> None:
    script = textwrap.dedent(
        """
        import io
        import tarfile
        import tempfile
        from pathlib import Path

        from lynchpin.sources.exports import takeout

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "takeout-20260101-001.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                data = b"not relevant"
                info = tarfile.TarInfo("Takeout/Archive Browser.html")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

            bundle = takeout.parse_life_timeline_takeouts(
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

        from lynchpin.sources.exports import takeout

        video_ids = Counter({"abc123xyz00": 2, "bad": 9})
        titles = Counter()
        channels = Counter()
        cache = {"abc123xyz00": {"ok": True, "title": "DuckDB Deep Dive", "author_name": "Data Channel"}}

        top_ids, resolved_titles, resolved_channels, title_tokens = takeout.summarize_youtube_watch_history_month(
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

        from lynchpin.sources.exports import spotify, takeout

        tokens = takeout.phrase_topic_tokens(Counter({"DuckDB json": 2, "the and": 5}))
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

        from lynchpin.sources.exports import takeout

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

            cache = takeout.load_youtube_oembed_cache(cache_path)
            title, channel = takeout.resolve_youtube_video_meta(
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
