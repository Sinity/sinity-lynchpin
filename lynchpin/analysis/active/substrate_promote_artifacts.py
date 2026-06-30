"""JSON artifact promotion for the materialization DAG substrate step."""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any

from .substrate_promote_loaders import (
    _load_commit_facts,
    _load_file_change_facts,
    _load_symbol_change_rows,
    _merge_ai_attribution,
)
from .substrate_promote_status import (
    SOURCE_COMMITS,
    SOURCE_FILE_CHANGES,
    SOURCE_SYMBOLS,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_artifact_sources(
    conn: Any,
    *,
    refresh_id: str,
    commit_facts_file: str,
    file_changes_file: str,
    symbol_changes_file: str,
    ai_attribution_file: str | None,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_COMMITS, SOURCE_FILE_CHANGES, SOURCE_SYMBOLS):
        return

    from lynchpin.substrate.work_commits import promote_commits
    from lynchpin.substrate.work_files import promote_file_changes
    from lynchpin.substrate.work_symbols import promote_symbol_changes

    # ── commits: read JSON, hydrate to GitCommitFact, promote ────────────
    if selection.includes(SOURCE_COMMITS):
        try:
            commit_facts, commit_annotations = _load_commit_facts(commit_facts_file)
            _merge_ai_attribution(commit_annotations, ai_attribution_file)
        except Exception as exc:
            log.warning("substrate_promote: commit facts hydration failed: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_COMMITS,
                status="error",
                reason=str(exc),
                row_count=0,
            )
            commit_facts = []
            commit_annotations = {}

        if commit_facts:
            try:
                counts["commits"] = promote_commits(
                    conn,
                    refresh_id=refresh_id,
                    facts=commit_facts,
                    annotations=commit_annotations,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_COMMITS,
                    status="ok",
                    reason=None,
                    row_count=counts["commits"],
                )
            except Exception as exc:
                log.warning("substrate_promote: commit promotion failed: %s", exc)
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_COMMITS,
                    status="error",
                    reason=str(exc),
                    row_count=0,
                )
        else:
            log.debug("substrate_promote: no commit facts to promote")
            if not Path(commit_facts_file).exists():
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_COMMITS,
                    status="unavailable",
                    reason="active_commit_facts.json missing",
                    row_count=0,
                )
            else:
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_COMMITS,
                    status="empty",
                    reason="no commits in active facts payload",
                    row_count=0,
                )
        del commit_facts, commit_annotations
        gc.collect()

    # ── file_changes: same pattern ────────────────────────────────────────
    if selection.includes(SOURCE_FILE_CHANGES):
        try:
            fc_facts, fc_annotations = _load_file_change_facts(file_changes_file)
        except Exception as exc:
            log.warning("substrate_promote: file change hydration failed: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_FILE_CHANGES,
                status="error",
                reason=str(exc),
                row_count=0,
            )
            fc_facts = []
            fc_annotations = {}

        if fc_facts:
            try:
                counts["file_changes"] = promote_file_changes(
                    conn,
                    refresh_id=refresh_id,
                    facts=fc_facts,
                    annotations=fc_annotations,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_FILE_CHANGES,
                    status="ok",
                    reason=None,
                    row_count=counts["file_changes"],
                )
            except Exception as exc:
                log.warning("substrate_promote: file change promotion failed: %s", exc)
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_FILE_CHANGES,
                    status="error",
                    reason=str(exc),
                    row_count=0,
                )
        else:
            log.debug("substrate_promote: no file change facts to promote")
            if not Path(file_changes_file).exists():
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_FILE_CHANGES,
                    status="unavailable",
                    reason="active_file_change_facts.json missing",
                    row_count=0,
                )
            else:
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_FILE_CHANGES,
                    status="empty",
                    reason="no file changes in active facts payload",
                    row_count=0,
                )
        del fc_facts, fc_annotations
        gc.collect()

    # ── symbol_changes: load events list, promote ─────────────────────────
    if selection.includes(SOURCE_SYMBOLS):
        if Path(symbol_changes_file).exists():
            try:
                counts["symbols"] = promote_symbol_changes(
                    conn,
                    refresh_id=refresh_id,
                    rows=_load_symbol_change_rows(symbol_changes_file),
                )
                status = "ok" if counts["symbols"] > 0 else "empty"
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SYMBOLS,
                    status=status,
                    reason=None if status == "ok" else "no symbol events in payload",
                    row_count=counts["symbols"],
                )
            except Exception as exc:
                log.warning("substrate_promote: symbol change promotion failed: %s", exc)
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SYMBOLS,
                    status="error",
                    reason=str(exc),
                    row_count=0,
                )
        else:
            log.debug(
                "substrate_promote: active_symbol_changes.json unavailable"
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_SYMBOLS,
                status="unavailable",
                reason="active_symbol_changes.json missing",
                row_count=0,
            )
        gc.collect()
