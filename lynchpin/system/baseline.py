#!/usr/bin/env python3
"""Rebuild baseline analytics artifacts from local canonical data sources.

This script mirrors the handcrafted 2025-10-23 baseline by wiring together
ActivityWatch windows/AFK, Codex session metadata, Atuin history, git stats,
and merged wearable sleep segments. It supports both:

- bundle mode: read exports under `--session-root`
- live mode: query canonical local sources (sqlite DBs, `~/.codex/sessions`, local git repos)

`--mode auto` prefers the bundle when present and falls back to live extraction.

Each output lands in the requested `--output-dir` (defaults to
`artefacts/core/baseline/latest` but can be pointed to any dated folder).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
import typer
from typing_extensions import Annotated

from lynchpin.sources.exports import sleep as lp_sleep

from ._baseline import (
    build_activity_timeline,
    build_activitywatch_afk_summary,
    build_activitywatch_afk_window,
    build_activitywatch_window_summary,
    build_atuin_summary,
    build_codex_summary,
    build_command_category_pivot,
    build_git_summary,
    build_git_supporting_summary,
    build_sleep_summary_from_entries,
    build_sleep_summary_from_file,
    load_activitywatch_afk,
    load_activitywatch_windows,
    load_atuin_history,
    load_codex_sessions,
    load_git_numstat,
    parse_timestamp,
    resolve_window,
    snapshot_web_bucket,
    write_json,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


@dataclass(frozen=True)
class BaselineResult:
    """Structured return payload from the baseline rebuild workflow."""

    output_dir: Path
    mode: str
    since_ts: str
    until_ts: str
    source_rows: Dict[str, int]
    artifact_paths: Dict[str, Path]


DEFAULT_GIT_REPOS = (
    Path("/realm/project/sinex"),
    Path("/realm/project/intercept-bounce"),
    Path("/realm/project/sinnix"),
    Path("/realm/project/knowledgebase"),
    Path("/realm/project/polylogue"),
    Path("/realm/project/scribe-tap"),
    Path("/realm/project/pwrank"),
)


def _noop(_message: str) -> None:
    pass


def run_baseline(
    session_root: Annotated[
        Path, typer.Option(help="Path containing ActivityWatch/Git/Codex exports")
    ] = Path("/realm/data/sinity-lynchpin/baseline-inputs/latest"),
    health_root: Annotated[
        Path, typer.Option(help="Directory with merged wearable exports")
    ] = Path("/realm/data/exports/health/processed"),
    output_dir: Annotated[
        Path, typer.Option(help="Directory to place JSON outputs")
    ] = Path("artefacts/core/baseline/latest"),
    mode: Annotated[
        str, typer.Option(help="Input mode: auto (prefer bundle), bundle, or live")
    ] = "auto",
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help="Use full available history (ignores --window-days when --since is omitted)",
        ),
    ] = False,
    since: Annotated[
        Optional[str], typer.Option(help="Start timestamp for live extraction (ISO8601)")
    ] = None,
    until: Annotated[
        Optional[str], typer.Option(help="End timestamp for live extraction (ISO8601)")
    ] = None,
    window_days: Annotated[
        int, typer.Option(help="Default live window size when --since is omitted")
    ] = 90,
    activitywatch_db: Annotated[
        Path, typer.Option(help="ActivityWatch sqlite DB path (aw-server-rust)")
    ] = Path("~/.local/share/activitywatch/aw-server-rust/sqlite.db"),
    atuin_db: Annotated[
        Path, typer.Option(help="Atuin history sqlite DB path")
    ] = Path("~/.local/share/atuin/history.db"),
    codex_sessions_root: Annotated[
        Path, typer.Option(help="Codex sessions root (for live extraction)")
    ] = Path("~/.codex/sessions"),
    git_repo: Annotated[
        List[Path],
        typer.Option(
            "--git-repo",
            help="Git repositories to include (repeatable). Default: common /realm repos.",
        ),
    ] = [],
    git_since: Annotated[
        Optional[str],
        typer.Option(help="Lower bound for git history (ISO8601). Default: full history."),
    ] = None,
    skip_git: Annotated[
        bool, typer.Option("--skip-git", help="Skip git summaries (faster)")
    ] = False,
    include_web_sample: Annotated[
        bool, typer.Option("--include-web-sample", help="Query ActivityWatch web bucket")
    ] = False,
    web_bucket: Annotated[
        Optional[str], typer.Option(help="ActivityWatch web bucket name")
    ] = None,
    activitywatch_api: Annotated[
        str, typer.Option(help="ActivityWatch API base URL")
    ] = "http://127.0.0.1:5600/api/0",
    log: Optional[Callable[[str], None]] = None,
) -> BaselineResult:
    """Rebuild the baseline analytics suite and return a typed result manifest."""
    if log is None:
        log = _noop

    output_dir.mkdir(parents=True, exist_ok=True)

    mode = mode.strip().lower()
    if mode not in {"auto", "bundle", "live"}:
        raise ValueError("--mode must be one of: auto, bundle, live")

    since_ts, until_ts = resolve_window(since, until, window_days, full)
    git_since_ts = parse_timestamp(git_since, "--git-since")

    windows_path = session_root / "activitywatch_windows.jsonl"
    afk_path = session_root / "activitywatch_afk.jsonl"
    codex_path = session_root / "codex_sessions.jsonl"
    atuin_path = session_root / "atuin_history_last90.csv"
    git_numstat_path = session_root / "git_numstat.jsonl"
    sleep_path = health_root / "sleep_merged.jsonl"
    source_rows: Dict[str, int] = {}
    artifact_paths: Dict[str, Path] = {}

    log("→ ActivityWatch windows")
    windows_df = load_activitywatch_windows(
        windows_path,
        mode,
        activitywatch_db,
        since_ts,
        until_ts,
    )
    window_rows = int(windows_df.shape[0])
    source_rows["activitywatch_windows"] = window_rows
    log(
        f"   source={'bundle' if (windows_path.exists() and mode in {'bundle', 'auto'}) else 'live'} rows={window_rows}"
    )
    window_summary = build_activitywatch_window_summary(windows_df)
    artifact_paths["activitywatch_window_summary"] = (
        output_dir / "activitywatch_window_summary.json"
    )
    write_json(artifact_paths["activitywatch_window_summary"], window_summary)

    log("→ ActivityWatch AFK")
    afk_df = load_activitywatch_afk(afk_path, mode, activitywatch_db, since_ts, until_ts)
    afk_rows = int(afk_df.shape[0])
    source_rows["activitywatch_afk"] = afk_rows
    log(
        f"   source={'bundle' if (afk_path.exists() and mode in {'bundle', 'auto'}) else 'live'} rows={afk_rows}"
    )
    afk_summary = build_activitywatch_afk_summary(afk_df)
    artifact_paths["activitywatch_afk_summary"] = output_dir / "activitywatch_afk_summary.json"
    write_json(artifact_paths["activitywatch_afk_summary"], afk_summary)

    afk_window_stats = build_activitywatch_afk_window(afk_df)
    artifact_paths["activitywatch_afk_window"] = output_dir / "activitywatch_afk_window.json"
    write_json(artifact_paths["activitywatch_afk_window"], afk_window_stats)

    log("→ Codex sessions")
    codex_df = load_codex_sessions(codex_path, mode, codex_sessions_root, since_ts, until_ts)
    codex_rows = int(codex_df.shape[0])
    source_rows["codex_sessions"] = codex_rows
    log(
        f"   source={'bundle' if (codex_path.exists() and mode in {'bundle', 'auto'}) else 'live'} rows={codex_rows}"
    )
    codex_summary = build_codex_summary(codex_df)
    artifact_paths["codex_sessions_summary"] = output_dir / "codex_sessions_summary.json"
    write_json(artifact_paths["codex_sessions_summary"], codex_summary)

    log("→ Atuin history")
    atuin_df = load_atuin_history(atuin_path, mode, atuin_db, since_ts, until_ts)
    atuin_rows = int(atuin_df.shape[0])
    source_rows["atuin_history"] = atuin_rows
    log(
        f"   source={'bundle' if (atuin_path.exists() and mode in {'bundle', 'auto'}) else 'live'} rows={atuin_rows}"
    )
    atuin_summary = build_atuin_summary(atuin_df)
    artifact_paths["atuin_summary"] = output_dir / "atuin_summary.json"
    write_json(artifact_paths["atuin_summary"], atuin_summary)

    repos_used = list(git_repo) if git_repo else list(DEFAULT_GIT_REPOS)
    df_git = pd.DataFrame(
        columns=["date", "repo", "lines_added", "lines_deleted", "files_changed"]
    )

    if skip_git:
        log("→ Git activity (skipped)")
    else:
        log("→ Git activity")
        df_git = load_git_numstat(
            git_numstat_path,
            mode,
            repos_used,
            git_since_ts,
            until_ts,
        )
        git_rows = int(df_git.shape[0])
        source_rows["git_numstat"] = git_rows
        log(
            f"   source={'bundle' if (git_numstat_path.exists() and mode in {'bundle', 'auto'}) else 'live'} rows={git_rows}"
        )
        git_summary = build_git_summary(df_git)
        artifact_paths["git_activity_summary"] = output_dir / "git_activity_summary.json"
        write_json(artifact_paths["git_activity_summary"], git_summary)

        if not df_git.empty:
            if git_numstat_path.exists() and mode in {"bundle", "auto"}:
                shutil.copy2(git_numstat_path, output_dir / "git_numstat.jsonl")
            else:
                df_git.to_json(
                    output_dir / "git_numstat.jsonl",
                    orient="records",
                    lines=True,
                    date_format="iso",
                    force_ascii=False,
                )
            artifact_paths["git_numstat"] = output_dir / "git_numstat.jsonl"
            git_supporting = build_git_supporting_summary(df_git)
            supporting_dir = output_dir / "supporting"
            supporting_dir.mkdir(exist_ok=True)
            artifact_paths["git_numstat_supporting"] = (
                supporting_dir / "git_numstat_summary.json"
            )
            write_json(artifact_paths["git_numstat_supporting"], git_supporting)

    log(f"→ Summarising merged sleep segments from {sleep_path}")
    if mode in {"bundle", "auto"} and sleep_path.exists():
        sleep_summary = build_sleep_summary_from_file(sleep_path)
    else:
        sleep_summary = build_sleep_summary_from_entries(
            lp_sleep.iter_sleep(path=sleep_path)
        )
    artifact_paths["sleep_summary"] = output_dir / "sleep_summary.json"
    write_json(artifact_paths["sleep_summary"], sleep_summary)

    log("→ Building daily activity timeline")
    command_categories = build_command_category_pivot(atuin_df)
    timeline = build_activity_timeline(
        window_summary.get("daily_totals", []),
        afk_summary.get("daily", []),
        codex_summary.get("daily_counts", []),
        atuin_summary.get("daily_counts", []),
        command_categories,
    )
    artifact_paths["activity_timeline"] = output_dir / "activity_timeline.json"
    write_json(artifact_paths["activity_timeline"], timeline)

    if include_web_sample and web_bucket:
        log(f"→ Sampling ActivityWatch web bucket {web_bucket}")
        sample = snapshot_web_bucket(activitywatch_api, web_bucket)
        if sample:
            artifact_paths["activitywatch_web_sample"] = (
                output_dir / "activitywatch_web_sample.json"
            )
            write_json(artifact_paths["activitywatch_web_sample"], sample)
        else:
            log("   ! Unable to fetch web bucket data; skipping.")

    log(f"✓ Baseline rebuild complete → {output_dir}")
    return BaselineResult(
        output_dir=output_dir,
        mode=mode,
        since_ts=since_ts.isoformat(),
        until_ts=until_ts.isoformat(),
        source_rows=source_rows,
        artifact_paths=artifact_paths,
    )


@app.command()
def baseline(
    session_root: Annotated[
        Path, typer.Option(help="Path containing ActivityWatch/Git/Codex exports")
    ] = Path("/realm/data/sinity-lynchpin/baseline-inputs/latest"),
    health_root: Annotated[
        Path, typer.Option(help="Directory with merged wearable exports")
    ] = Path("/realm/data/exports/health/processed"),
    output_dir: Annotated[
        Path, typer.Option(help="Directory to place JSON outputs")
    ] = Path("artefacts/core/baseline/latest"),
    mode: Annotated[
        str, typer.Option(help="Input mode: auto (prefer bundle), bundle, or live")
    ] = "auto",
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help="Use full available history (ignores --window-days when --since is omitted)",
        ),
    ] = False,
    since: Annotated[
        Optional[str], typer.Option(help="Start timestamp for live extraction (ISO8601)")
    ] = None,
    until: Annotated[
        Optional[str], typer.Option(help="End timestamp for live extraction (ISO8601)")
    ] = None,
    window_days: Annotated[
        int, typer.Option(help="Default live window size when --since is omitted")
    ] = 90,
    activitywatch_db: Annotated[
        Path, typer.Option(help="ActivityWatch sqlite DB path (aw-server-rust)")
    ] = Path("~/.local/share/activitywatch/aw-server-rust/sqlite.db"),
    atuin_db: Annotated[
        Path, typer.Option(help="Atuin history sqlite DB path")
    ] = Path("~/.local/share/atuin/history.db"),
    codex_sessions_root: Annotated[
        Path, typer.Option(help="Codex sessions root (for live extraction)")
    ] = Path("~/.codex/sessions"),
    git_repo: Annotated[
        List[Path],
        typer.Option(
            "--git-repo",
            help="Git repositories to include (repeatable). Default: common /realm repos.",
        ),
    ] = [],
    git_since: Annotated[
        Optional[str],
        typer.Option(help="Lower bound for git history (ISO8601). Default: full history."),
    ] = None,
    skip_git: Annotated[
        bool, typer.Option("--skip-git", help="Skip git summaries (faster)")
    ] = False,
    include_web_sample: Annotated[
        bool, typer.Option("--include-web-sample", help="Query ActivityWatch web bucket")
    ] = False,
    web_bucket: Annotated[
        Optional[str], typer.Option(help="ActivityWatch web bucket name")
    ] = None,
    activitywatch_api: Annotated[
        str, typer.Option(help="ActivityWatch API base URL")
    ] = "http://127.0.0.1:5600/api/0",
) -> None:
    """Rebuild the baseline analytics suite from local datasets."""
    try:
        result = run_baseline(
            session_root=session_root,
            health_root=health_root,
            output_dir=output_dir,
            mode=mode,
            full=full,
            since=since,
            until=until,
            window_days=window_days,
            activitywatch_db=activitywatch_db,
            atuin_db=atuin_db,
            codex_sessions_root=codex_sessions_root,
            git_repo=git_repo,
            git_since=git_since,
            skip_git=skip_git,
            include_web_sample=include_web_sample,
            web_bucket=web_bucket,
            activitywatch_api=activitywatch_api,
            log=typer.echo,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    else:
        typer.echo(f"✓ Baseline rebuild complete → {result.output_dir}")


if __name__ == "__main__":
    app()
