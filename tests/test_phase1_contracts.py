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
        ["nix", "develop", "--command", "python", "-m", "lynchpin.system.life_timeline", "--help"],
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
    assert "lp_reddit.summarize_activity(" in text
    assert "lp_wykop.summarize_activity(" in text
    assert "lp_raindrop.summarize_bookmarks(" in text
    assert "lp_gitstats.summarize_commit_activity(" in text
    assert "lp_spotify.summarize_streaming(" in text
    assert "lp_knowledgebase.summarize_onenote_journal_entries(" in text
    assert "lp_takeout.resolve_archives(" in text
    assert "lp_takeout.load_youtube_oembed_cache(" in text


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
