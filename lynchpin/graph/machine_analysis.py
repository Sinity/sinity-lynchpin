"""Machine-analysis evidence graph nodes."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..analysis.core.io import load_json_if_exists, resolve_analysis_path
from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from ..core.parse import parse_datetime
from ..core.primitives import logical_date
from ..core.projects import canonical_project_name


def add_machine_analysis_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    start: date,
    end: date,
    selected: set[str],
    exclude_names: frozenset[str],
) -> None:
    artifacts = {
        "episodes": "machine_episode_analysis.json",
        "context": "machine_context_windows.json",
        "below": "machine_below_attribution.json",
        "baselines": "machine_observational_baselines.json",
        "claims": "machine_experiment_claims.json",
    }
    payloads = {
        key: load_json_if_exists(resolve_analysis_path(name))
        for key, name in artifacts.items()
        if name not in exclude_names
    }

    episode_ids: dict[tuple[str, str, str, str], str] = {}
    selected_episode_keys = _selected_machine_episode_keys(
        context_payload=payloads.get("context"),
        claims_payload=payloads.get("claims"),
        selected=selected,
    )
    episodes = _machine_rows(payloads.get("episodes"), "episodes")
    for row in episodes:
        episode_key = _machine_episode_key(row)
        if selected and episode_key not in selected_episode_keys:
            continue
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        node_id = _machine_episode_id(row)
        episode_ids[episode_key] = node_id
        kind = str(row.get("kind") or "unknown")
        subject = str(row.get("subject") or "")
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_episode",
                source="machine",
                date=logical_date(started_at),
                project=None,
                start=started_at,
                end=ended_at,
                summary=f"{kind}: {subject}".rstrip(": "),
                payload={
                    "kind": kind,
                    "host": row.get("host"),
                    "subject": row.get("subject"),
                    "severity": row.get("severity"),
                    "confidence": row.get("confidence"),
                    "sample_count": row.get("sample_count"),
                    "sources": row.get("sources") or (),
                    "evidence": row.get("evidence") or (),
                    "payload": row.get("payload") or {},
                },
                provenance=EvidenceProvenance("machine", "local-fast", path=artifacts["episodes"]),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )

    for row in _machine_rows(payloads.get("context"), "windows"):
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        projects = tuple(
            project
            for project in (canonical_project_name(str(value)) for value in row.get("projects", ()) if value)
            if project is not None
        )
        if selected and not set(projects).intersection(selected):
            continue
        project = projects[0] if len(projects) == 1 else None
        node_id = f"machine-context:{row.get('window_id') or started_at.isoformat()}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_context_window",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=str(row.get("summary") or row.get("interpretation") or "machine/work context window"),
                payload={
                    "window_id": row.get("window_id"),
                    "projects": projects,
                    "source": row.get("source"),
                    "work_kind": row.get("work_kind"),
                    "duration_seconds": row.get("duration_seconds"),
                    "episode_count": row.get("episode_count"),
                    "overlap_seconds": row.get("overlap_seconds"),
                    "interpretation": row.get("interpretation"),
                },
                provenance=EvidenceProvenance("machine", "local-fast", path=artifacts["context"]),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        for embedded in _machine_embedded_rows(row, "episodes"):
            target_id = episode_ids.get(_machine_episode_key(embedded)) or _machine_episode_id(embedded)
            edges.append(
                EvidenceEdge(
                    node_id,
                    target_id,
                    "overlaps_machine_pressure",
                    f"work window overlaps {embedded.get('kind')} for {embedded.get('overlap_seconds')}s",
                    _bounded_weight(embedded.get("overlap_seconds"), row.get("duration_seconds")),
                )
            )

    for row in _machine_rows(payloads.get("below"), "attributions"):
        started_at = _machine_dt(row.get("episode_started_at"))
        ended_at = _machine_dt(row.get("episode_ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        node_id = f"machine-below:{row.get('capture_id')}:{row.get('episode_kind')}:{started_at.isoformat()}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_below_attribution",
                source="below",
                date=logical_date(started_at),
                project=None,
                start=started_at,
                end=ended_at,
                summary=f"below attribution for {row.get('episode_kind')} in {row.get('capture_id')}",
                payload=row,
                provenance=EvidenceProvenance("below", "local-fast", path=artifacts["below"]),
                caveats=tuple(EvidenceCaveat("below", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        attributed_episode_id = episode_ids.get(_machine_attribution_episode_key(row))
        if attributed_episode_id is not None:
            edges.append(
                EvidenceEdge(
                    node_id,
                    attributed_episode_id,
                    "below_supports_episode",
                    f"bounded below capture overlaps {row.get('episode_kind')}",
                    _bounded_weight(row.get("overlap_seconds"), None),
                )
            )

    _add_machine_baseline_nodes(nodes, payloads.get("baselines"), start=start, end=end, selected=selected, artifact_name=artifacts["baselines"])
    _add_machine_claim_nodes(nodes, edges, payloads.get("claims"), start=start, end=end, selected=selected, episode_ids=episode_ids, artifact_name=artifacts["claims"])


def _add_machine_baseline_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    end: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    if not _machine_payload_overlaps(payload, start=start, end=end):
        return
    for section in ("by_hardware_regime", "work_context", "era_comparisons"):
        for idx, row in enumerate(_machine_rows(payload, section)):
            first = _machine_dt(row.get("first_observed_at") or row.get("boundary")) or datetime.combine(start, datetime.min.time()).astimezone()
            last = _machine_dt(row.get("last_observed_at")) or datetime.combine(end, datetime.max.time()).astimezone()
            if not _machine_overlaps(first, last, start=start, end=end):
                continue
            key = str(row.get("key") or row.get("boundary") or idx)
            project = canonical_project_name(key) if row.get("dimension") == "project" else None
            if project == "(unattributed)":
                project = None
            if selected and project is not None and project not in selected:
                continue
            nodes.append(
                EvidenceNode(
                    id=f"machine-baseline:{section}:{key}",
                    kind="machine_baseline",
                    source="machine",
                    date=logical_date(first),
                    project=project,
                    start=first,
                    end=last,
                    summary=f"{section} baseline: {key}",
                    payload={"section": section, **row},
                    provenance=EvidenceProvenance("machine", "local-fast", path=artifact_name),
                    caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
                )
            )


def _add_machine_claim_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    end: date,
    selected: set[str],
    episode_ids: dict[tuple[str, str, str, str], str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "claim_packs"):
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        project = _project_from_path(row.get("git_root") or row.get("cwd"))
        if selected and project is not None and project not in selected:
            continue
        node_id = f"machine-claim:{row.get('run_id') or started_at.isoformat()}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_experiment_claim",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=f"{row.get('claim_mode')}: {row.get('workload')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "local-fast", path=str(row.get("manifest_path") or artifact_name)),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        for embedded in _machine_embedded_rows(row, "episodes"):
            target_id = episode_ids.get(_machine_episode_key(embedded)) or _machine_episode_id(embedded)
            edges.append(
                EvidenceEdge(
                    node_id,
                    target_id,
                    "experiment_claim_support",
                    f"experiment claim includes {embedded.get('kind')} overlap",
                    _bounded_weight(embedded.get("overlap_seconds"), row.get("duration_seconds")),
                )
            )


def _machine_rows(payload: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _machine_embedded_rows(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = row.get(key)
    if not isinstance(rows, list):
        return []
    return [embedded for embedded in rows if isinstance(embedded, dict)]


def _machine_payload_overlaps(payload: object, *, start: date, end: date) -> bool:
    if not isinstance(payload, dict):
        return False
    generated_for = payload.get("generated_for")
    if not isinstance(generated_for, dict):
        return True
    payload_start = _date_value(generated_for.get("start"))
    payload_end = _date_value(generated_for.get("end"))
    if payload_start is None or payload_end is None:
        return True
    return payload_end >= start and payload_start <= end


def _selected_machine_episode_keys(
    *,
    context_payload: object,
    claims_payload: object,
    selected: set[str],
) -> set[tuple[str, str, str, str]]:
    if not selected:
        return set()
    keys: set[tuple[str, str, str, str]] = set()
    for row in _machine_rows(context_payload, "windows"):
        projects = {
            project
            for project in (canonical_project_name(str(value)) for value in row.get("projects", ()) if value)
            if project is not None
        }
        if not projects.intersection(selected):
            continue
        keys.update(_machine_episode_key(embedded) for embedded in _machine_embedded_rows(row, "episodes"))
    for row in _machine_rows(claims_payload, "claim_packs"):
        project = _project_from_path(row.get("git_root") or row.get("cwd"))
        if project not in selected:
            continue
        keys.update(_machine_episode_key(embedded) for embedded in _machine_embedded_rows(row, "episodes"))
    return keys


def _machine_dt(value: object) -> datetime | None:
    if value is None:
        return None
    return parse_datetime(str(value))


def _date_value(value: object) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        parsed = _machine_dt(value)
        return parsed.date() if parsed is not None else None


def _machine_overlaps(started_at: datetime, ended_at: datetime | None, *, start: date, end: date) -> bool:
    row_end = ended_at or started_at
    return row_end.date() >= start and started_at.date() <= end


def _machine_episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("kind") or ""),
        str(row.get("host") or ""),
        str(row.get("started_at") or ""),
        str(row.get("subject") or ""),
    )


def _machine_attribution_episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("episode_kind") or ""),
        str(row.get("host") or ""),
        str(row.get("episode_started_at") or ""),
        "",
    )


def _machine_episode_id(row: dict[str, Any]) -> str:
    kind, host, started, subject = _machine_episode_key(row)
    return f"machine-episode:{host}:{kind}:{started}:{subject}"


def _bounded_weight(numerator: object, denominator: object | None) -> float:
    try:
        value = float(str(numerator or 0.0))
        base = float(str(denominator or 0.0))
    except (TypeError, ValueError):
        return 0.5
    if base <= 0:
        return 0.7 if value > 0 else 0.4
    return max(0.1, min(1.0, value / base))


def _project_from_path(value: object) -> str | None:
    if not value:
        return None
    return canonical_project_name(Path(str(value)).name)
