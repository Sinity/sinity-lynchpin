"""Refresh-DAG step: promote source data + evidence graph to DuckDB substrate.

Writes to the substrate as a side effect of refresh; does not change any
existing read path. Read-side cutover comes in Arc 3.

Arc 2.6 cutover: substrate becomes populated by default on every refresh run,
ready for Arc 4 (MCP server) to read from.

Per-source readiness (Arc 2.7): every source's outcome is recorded in
``substrate_source_status`` (status: ok | empty | unavailable | error). This
fixes the prior silent-failure mode where a stale polylogue archive →
``ai_work_event=0`` looked indistinguishable from a successful promote with
no events in the window.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from typing import Any

log = logging.getLogger(__name__)


# Source identifiers used in substrate_source_status.source — kept as constants
# so MCP/readiness consumers can match against a stable vocabulary.
SOURCE_COMMITS = "commits"
SOURCE_FILE_CHANGES = "file_changes"
SOURCE_SYMBOLS = "symbols"
SOURCE_AI_WORK_EVENTS = "ai_work_events"
SOURCE_EVIDENCE_GRAPH = "evidence_graph"
SOURCE_PR_REVIEW = "pr_review"
SOURCE_CALENDAR = "calendar"
SOURCE_SPOTIFY_DAILY = "spotify_daily"
SOURCE_MACHINE = "machine"
SOURCE_MACHINE_NETWORK = "machine_network_sample"
SOURCE_MACHINE_SERVICE_STATE = "machine_service_state"
SOURCE_MACHINE_EXPERIMENTS = "machine_experiments"


def run_substrate_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None = None,
    ai_attribution_file: str | None = None,
    spec_path: str | None = None,
    refresh_id: str | None = None,
    write_evidence_graph: bool = True,
) -> dict[str, int]:
    """Promote produced JSON artifacts + work events to the substrate.

    Reads already-materialized JSON outputs from the DAG (commit facts, file
    changes, symbol changes) and live polylogue work_events for the current
    window, then promotes all to DuckDB.

    Returns per-table row counts.
    """
    try:
        return _do_promote(
            commit_facts_file=commit_facts_file,
            file_changes_file=file_changes_file,
            symbol_changes_file=symbol_changes_file,
            pr_review_file=pr_review_file,
            refresh_id=refresh_id,
            write_evidence_graph=write_evidence_graph,
        )
    except Exception as exc:  # noqa: BLE001 — refresh promotion must be best-effort
        log.warning("substrate_promote: substrate promotion failed: %s", exc)
        return {}


def _record_status(
    conn: Any,
    *,
    refresh_id: str,
    source: str,
    status: str,
    reason: str | None,
    row_count: int,
    window_start: date | None = None,
    window_end: date | None = None,
) -> None:
    """Upsert a per-source status row into ``substrate_source_status``."""
    conn.execute(
        "DELETE FROM substrate_source_status WHERE refresh_id = ? AND source = ?",
        [refresh_id, source],
    )
    conn.execute(
        """
        INSERT INTO substrate_source_status
        (refresh_id, source, status, reason, row_count, window_start, window_end, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            source,
            status,
            reason,
            int(row_count),
            window_start,
            window_end,
            datetime.now(timezone.utc),
        ],
    )


def _do_promote(
    *,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    pr_review_file: str | None,
    refresh_id: str | None,
    write_evidence_graph: bool,
) -> dict[str, int]:
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path
    from lynchpin.substrate.promote import (
        promote_ai_work_events,
        promote_calendar_events,
        promote_commits,
        promote_evidence_graph,
        promote_file_changes,
        promote_machine_experiment_runs,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_service_states,
        promote_pr_review_rows,
        promote_spotify_daily,
        promote_symbol_changes,
    )
    from lynchpin.graph.work_event_kind import overlay_label

    refresh_id = refresh_id or f"dag:{datetime.now(timezone.utc).isoformat()}"
    counts: dict[str, int] = {}

    # Hard-coded window: current month + previous month (covers ~30-60 days of
    # recent AI activity).  Refine via spec config in a follow-up.
    today = date.today()
    if today.month == 1:
        prev_month_start = today.replace(year=today.year - 1, month=12, day=1)
    else:
        prev_month_start = today.replace(month=today.month - 1, day=1)
    window_start = prev_month_start
    window_end = today

    with connect(substrate_path()) as conn:
        apply_schema(conn)

        # ── commits: read JSON, hydrate to GitCommitFact, promote ────────────
        try:
            commit_facts, commit_annotations = _load_commit_facts(commit_facts_file)
            commit_facts = list(commit_facts)
        except Exception as exc:
            log.warning("substrate_promote: commit facts hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                status="error", reason=str(exc), row_count=0,
            )
            commit_facts = []
            commit_annotations = {}

        if commit_facts:
            try:
                counts["commits"] = promote_commits(
                    conn, refresh_id=refresh_id, facts=commit_facts,
                    annotations=commit_annotations,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="ok", reason=None, row_count=counts["commits"],
                )
            except Exception as exc:
                log.warning("substrate_promote: commit promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: no commit facts to promote")
            if not Path(commit_facts_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="unavailable", reason="active_commit_facts.json missing",
                    row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_COMMITS,
                    status="empty", reason="no commits in active facts payload",
                    row_count=0,
                )

        # ── file_changes: same pattern ────────────────────────────────────────
        try:
            fc_facts, fc_annotations = _load_file_change_facts(file_changes_file)
            fc_facts = list(fc_facts)
        except Exception as exc:
            log.warning("substrate_promote: file change hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                status="error", reason=str(exc), row_count=0,
            )
            fc_facts = []
            fc_annotations = {}

        if fc_facts:
            try:
                counts["file_changes"] = promote_file_changes(
                    conn, refresh_id=refresh_id, facts=fc_facts,
                    annotations=fc_annotations,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="ok", reason=None, row_count=counts["file_changes"],
                )
            except Exception as exc:
                log.warning("substrate_promote: file change promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: no file change facts to promote")
            if not Path(file_changes_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="unavailable",
                    reason="active_file_change_facts.json missing", row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_FILE_CHANGES,
                    status="empty", reason="no file changes in active facts payload",
                    row_count=0,
                )

        # ── symbol_changes: load events list, promote ─────────────────────────
        try:
            symbol_rows = list(_load_symbol_change_rows(symbol_changes_file))
        except Exception as exc:
            log.warning("substrate_promote: symbol change hydration failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                status="error", reason=str(exc), row_count=0,
            )
            symbol_rows = []

        if symbol_rows:
            try:
                counts["symbols"] = promote_symbol_changes(
                    conn, refresh_id=refresh_id, rows=symbol_rows,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="ok", reason=None, row_count=counts["symbols"],
                )
            except Exception as exc:
                log.warning("substrate_promote: symbol promotion failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="error", reason=str(exc), row_count=0,
                )
        else:
            log.debug("substrate_promote: active_symbol_changes.json missing or empty — skipping")
            if not Path(symbol_changes_file).exists():
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="unavailable",
                    reason="active_symbol_changes.json missing", row_count=0,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SYMBOLS,
                    status="empty", reason="no symbol events in payload",
                    row_count=0,
                )

        # ── ai_work_events: pull via polylogue source ─────────────────────────
        # Window covers current + previous month to ensure recent sessions are
        # captured even when the refresh runs near month boundaries.
        try:
            from lynchpin.sources.polylogue import work_events

            def _classify(ev: Any) -> Any:
                return overlay_label(
                    polylogue_kind=ev.kind,
                    polylogue_confidence=float(ev.confidence or 0.0),
                    file_paths=ev.file_paths,
                    tools_used=ev.tools_used,
                    duration_ms=int(ev.duration_ms or 0),
                )

            events = list(work_events(start=window_start, end=window_end))
            if events:
                counts["ai_work_events"] = promote_ai_work_events(
                    conn, refresh_id=refresh_id, events=events, classifier=_classify,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                    status="ok", reason=None, row_count=counts["ai_work_events"],
                    window_start=window_start, window_end=window_end,
                )
            else:
                # Distinguish "facade returned []" (could be stale-and-silent) from
                # "facade raised". sources.polylogue.work_events() catches the
                # ArchiveInsightUnavailableError, logs a warning, and returns [],
                # so an empty list here may mean either:
                #   - the polylogue archive is genuinely empty in the window, OR
                #   - the polylogue session_insight rows are stale (the actual
                #     bug we hit during 2026-05-08 I.1 prep).
                # We probe archive_readiness to disambiguate.
                from lynchpin.sources.polylogue import archive_readiness

                readiness = archive_readiness()
                if readiness.work_event_count == 0:
                    status = "empty"
                    reason = "polylogue archive has no work events in window"
                elif readiness.status != "ready":
                    status = "unavailable"
                    reason = (
                        f"polylogue not ready (status={readiness.status}): "
                        f"{readiness.reason}"
                    )
                else:
                    # Archive reports ready but facade returned []. Could be a
                    # window-mismatch or a stale-rows readiness flag the probe
                    # didn't surface. Mark unavailable so it's visible.
                    status = "unavailable"
                    reason = (
                        "polylogue archive_readiness=ready but work_events() "
                        "returned [] — likely stale insight rows; run "
                        "`polylogue doctor --repair --target session_insights`"
                    )
                log.warning(
                    "substrate_promote: ai_work_events empty in window %s–%s (%s: %s)",
                    window_start, window_end, status, reason,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                    status=status, reason=reason, row_count=0,
                    window_start=window_start, window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: AI work events promotion failed: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

        # ── evidence graph: build + promote ───────────────────────────────────
        if write_evidence_graph:
            try:
                from lynchpin.graph.evidence_graph import build_evidence_graph

                graph = build_evidence_graph(
                    start=window_start, end=window_end, mode="local-fast",
                )
                graph_counts = promote_evidence_graph(
                    conn, refresh_id=refresh_id, graph=graph,
                )
                counts["evidence_graph_nodes"] = graph_counts.get("nodes", 0)
                counts["evidence_graph_edges"] = graph_counts.get("edges", 0)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_EVIDENCE_GRAPH,
                    status="ok", reason=None,
                    row_count=counts["evidence_graph_nodes"],
                    window_start=window_start, window_end=window_end,
                )
            except Exception as exc:
                log.warning("substrate_promote: evidence_graph promotion skipped: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_EVIDENCE_GRAPH,
                    status="error", reason=str(exc), row_count=0,
                    window_start=window_start, window_end=window_end,
                )

        # ── pr_review_rows: promote if active_pr_review_topology.json present ─
        # M.7 (pr_review_topology) is standalone, not in the refresh DAG.
        # When its output file is present (because the user ran it manually
        # or as part of current_state's --github-frontier flag), pick it up.
        # When absent, mark unavailable so the readiness report shows the gap
        # rather than silently masking it.
        if pr_review_file is None:
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                status="unavailable",
                reason="pr_review_file not provided to substrate_promote",
                row_count=0,
            )
        else:
            try:
                pr_rows = list(_load_pr_review_rows(pr_review_file))
            except Exception as exc:
                log.warning("substrate_promote: pr_review hydration failed: %s", exc)
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                    status="error", reason=str(exc), row_count=0,
                )
                pr_rows = []

            if pr_rows:
                try:
                    counts["pr_review_rows"] = promote_pr_review_rows(
                        conn, refresh_id=refresh_id, rows=pr_rows,
                    )
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="ok", reason=None,
                        row_count=counts["pr_review_rows"],
                    )
                except Exception as exc:
                    log.warning("substrate_promote: pr_review promotion failed: %s", exc)
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="error", reason=str(exc), row_count=0,
                    )
            else:
                if not Path(pr_review_file).exists():
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="unavailable",
                        reason="active_pr_review_topology.json missing — run pr_review_topology",
                        row_count=0,
                    )
                else:
                    _record_status(
                        conn, refresh_id=refresh_id, source=SOURCE_PR_REVIEW,
                        status="empty", reason="no PR rows in payload",
                        row_count=0,
                    )

        # ── calendar_events: best-effort promotion from JSONL source ──────────
        try:
            from lynchpin.sources.calendar import iter_events
            from lynchpin.core.config import get_config

            cal_path = get_config().calendar_jsonl
            calendar_events = list(iter_events(start=window_start, end=window_end))
            if calendar_events:
                counts["calendar_events"] = promote_calendar_events(
                    conn, refresh_id=refresh_id, events=calendar_events,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                    status="ok", reason=None,
                    row_count=counts["calendar_events"],
                    window_start=window_start, window_end=window_end,
                )
            else:
                cal_exists = cal_path.exists()
                status = "unavailable" if not cal_exists else "empty"
                reason = (
                    f"calendar JSONL not found at {cal_path}"
                    if not cal_exists
                    else "no calendar events in window"
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                    status=status, reason=reason, row_count=0,
                    window_start=window_start, window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: calendar promotion skipped: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_CALENDAR,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

        # ── spotify_daily: best-effort promotion from streaming history ──────
        try:
            from lynchpin.sources.spotify import iter_streams

            streams = list(iter_streams())
            if streams:
                counts["spotify_daily"] = promote_spotify_daily(
                    conn, refresh_id=refresh_id, streams=streams,
                )
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SPOTIFY_DAILY,
                    status="ok", reason=None,
                    row_count=counts["spotify_daily"],
                    window_start=window_start, window_end=window_end,
                )
            else:
                _record_status(
                    conn, refresh_id=refresh_id, source=SOURCE_SPOTIFY_DAILY,
                    status="empty", reason="no Spotify streams in window",
                    row_count=0,
                    window_start=window_start, window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: spotify_daily promotion skipped: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_SPOTIFY_DAILY,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

        # ── machine telemetry: live SQLite capture ───────────────────────────
        try:
            from lynchpin.sources.machine import metric_samples, network_samples, readiness as machine_readiness, service_states

            machine_ready = machine_readiness()
            live_count = promote_machine_metric_samples(
                conn,
                refresh_id=refresh_id,
                samples=metric_samples(start=window_start, end=window_end),
            )
            counts["machine_metric_samples"] = live_count
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE,
                status="ok" if live_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason,
                row_count=live_count,
                window_start=window_start, window_end=window_end,
            )
            service_count = promote_machine_service_states(
                conn,
                refresh_id=refresh_id,
                states=service_states(start=window_start, end=window_end),
            )
            counts["machine_service_states"] = service_count
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_SERVICE_STATE,
                status="ok" if service_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason,
                row_count=service_count,
                window_start=window_start, window_end=window_end,
            )
            network_count = promote_machine_network_samples(
                conn,
                refresh_id=refresh_id,
                samples=network_samples(start=window_start, end=window_end),
            )
            counts["machine_network_samples"] = network_count
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_NETWORK,
                status="ok" if network_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason,
                row_count=network_count,
                window_start=window_start, window_end=window_end,
            )
        except Exception as exc:
            log.warning("substrate_promote: machine telemetry promotion skipped: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_SERVICE_STATE,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

        # ── machine experiments: immutable benchmark/stress manifests ───────
        try:
            from lynchpin.sources.machine_experiments import experiment_root, experiment_runs

            exp_root = experiment_root()
            runs = list(experiment_runs(start=window_start, end=window_end))
            run_count = promote_machine_experiment_runs(
                conn,
                refresh_id=refresh_id,
                runs=runs,
            )
            counts["machine_experiment_runs"] = run_count
            exp_reason: str | None
            if run_count:
                status = "ok"
                exp_reason = None
            elif exp_root.exists():
                status = "empty"
                exp_reason = "no machine experiment manifests in window"
            else:
                status = "unavailable"
                exp_reason = f"machine experiment root not found at {exp_root}"
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_EXPERIMENTS,
                status=status, reason=exp_reason, row_count=run_count,
                window_start=window_start, window_end=window_end,
            )
        except Exception as exc:
            log.warning("substrate_promote: machine experiment promotion skipped: %s", exc)
            _record_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_EXPERIMENTS,
                status="error", reason=str(exc), row_count=0,
                window_start=window_start, window_end=window_end,
            )

    log.info(
        "substrate promotion complete: refresh_id=%s counts=%s",
        refresh_id, counts,
    )
    return counts


def _load_commit_facts(path: str) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Hydrate active_commit_facts.json → (facts, annotations).

    Returns (Iterable[GitCommitFact], dict[str, dict]) where annotations
    maps commit sha → enrichment fields from the JSON (conventional_*,
    github_refs, categories, change_types, classified_files_changed,
    parent_count, default_branch, head).

    Line counts are zero (churn_caveat: not present in active facts).
    """
    from lynchpin.sources.git import GitCommitFact

    p = Path(path)
    if not p.exists():
        return [], {}
    with p.open() as f:
        data = json.load(f)
    facts: list[GitCommitFact] = []
    annotations: dict[str, dict[str, Any]] = {}
    for entry in data.get("commits", []):
        sha = entry.get("sha") or ""
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        path_roots_raw = entry.get("path_roots") or {}
        path_roots_tuple: tuple[str, ...] = (
            tuple(path_roots_raw.keys())
            if isinstance(path_roots_raw, dict)
            else tuple(path_roots_raw)
        )
        facts.append(GitCommitFact(
            repo=entry.get("project") or "",
            commit=sha,
            authored_at=authored_at,
            author=entry.get("author") or "",
            subject=entry.get("subject") or "",
            lines_added=0,
            lines_deleted=0,
            lines_changed=0,
            files_changed=int(entry.get("files_changed") or 0),
            paths=tuple(entry.get("paths") or ()),
            path_roots=path_roots_tuple,
        ))
        annotations[sha] = {
            "conventional_kind": entry.get("conventional_kind"),
            "conventional_scope": entry.get("conventional_scope"),
            "conventional_signature": entry.get("conventional_signature"),
            "breaking_change": entry.get("breaking_change", False),
            "github_refs": entry.get("github_refs"),
            "categories": entry.get("categories"),
            "change_types": entry.get("change_types"),
            "classified_files_changed": entry.get("classified_files_changed"),
            "parent_count": entry.get("parent_count"),
            "default_branch": entry.get("default_branch"),
            "head": entry.get("head"),
        }
    return facts, annotations


def _load_file_change_facts(path: str) -> tuple[list[Any], dict[tuple[str, str], dict[str, Any]]]:
    """Hydrate active_file_change_facts.json → (facts, annotations).

    Returns (Iterable[GitFileChangeFact], dict[(sha, path), dict]) where
    annotations maps (sha, path) → {change_type, status_code, previous_path}.
    """
    from lynchpin.sources.git import GitFileChangeFact

    p = Path(path)
    if not p.exists():
        return [], {}
    with p.open() as f:
        data = json.load(f)
    facts: list[GitFileChangeFact] = []
    annotations: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in data.get("file_changes", []):
        sha = entry.get("sha") or ""
        fpath = entry.get("path") or ""
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        facts.append(GitFileChangeFact(
            repo=entry.get("project") or "",
            commit=sha,
            authored_at=authored_at,
            path=fpath,
            path_root=entry.get("path_root") or "",
            lines_added=0,
            lines_deleted=0,
            lines_changed=0,
        ))
        annotations[(sha, fpath)] = {
            "change_type": entry.get("change_type"),
            "status_code": entry.get("status_code"),
            "previous_path": entry.get("previous_path"),
        }
    return facts, annotations


def _load_symbol_change_rows(path: str) -> Iterator[dict[str, Any]]:
    """Yield active_symbol_changes.json events as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("events", []):
        if isinstance(entry, dict):
            yield entry


def _load_pr_review_rows(path: str) -> Iterator[dict[str, Any]]:
    """Yield active_pr_review_topology.json prs as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("prs", []):
        if isinstance(entry, dict):
            yield entry


__all__ = [
    "run_substrate_promote",
    "SOURCE_COMMITS",
    "SOURCE_FILE_CHANGES",
    "SOURCE_SYMBOLS",
    "SOURCE_AI_WORK_EVENTS",
    "SOURCE_EVIDENCE_GRAPH",
    "SOURCE_PR_REVIEW",
    "SOURCE_MACHINE",
    "SOURCE_MACHINE_SERVICE_STATE",
    "SOURCE_MACHINE_EXPERIMENTS",
]
