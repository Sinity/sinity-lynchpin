"""Persistence helpers for narrative evidence bundles and query provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..periods import hierarchical_relpath


@dataclass(frozen=True)
class QueryArtifact:
    query_id: str
    title: str
    sql: str
    params: list[Any]
    row_count: int
    output_file: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "title": self.title,
            "sql": self.sql,
            "params": self.params,
            "row_count": self.row_count,
            "output_file": self.output_file,
            "error": self.error,
        }


def evidence_dir_for(scale: Any, key: str, *, root: Path | None = None) -> Path:
    cfg = get_config()
    base = Path(root or (cfg.repo_root / "artefacts/retrospective/narratives"))
    rel = hierarchical_relpath(scale, key)
    if rel is None:
        raise ValueError(f"No canonical evidence path for scale={scale!r} key={key!r}")
    return base / rel.parent / f"{rel.stem}.evidence"


def write_evidence_bundle(
    *,
    scale: Any,
    key: str,
    bundle_payload: dict[str, Any],
    query_payloads: list[dict[str, Any]],
    summary_markdown: str,
    root: Path | None = None,
) -> Path:
    evidence_dir = evidence_dir_for(scale, key, root=root)
    queries_dir = evidence_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)

    query_artifacts: list[QueryArtifact] = []
    for query in query_payloads:
        query_id = str(query["query_id"])
        query_file = queries_dir / f"{query_id}.json"
        query_file.write_text(_to_json(query), encoding="utf-8")
        query_artifacts.append(
            QueryArtifact(
                query_id=query_id,
                title=str(query.get("title", query_id)),
                sql=str(query.get("sql", "")),
                params=list(query.get("params", [])),
                row_count=int(query.get("row_count", 0)),
                output_file=str(query_file.relative_to(evidence_dir)),
                error=query.get("error"),
            ),
        )

    index = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scale": getattr(scale, "value", scale),
        "key": key,
        "bundle_file": "bundle.json",
        "summary_file": "summary.md",
        "queries": [artifact.to_dict() for artifact in query_artifacts],
    }
    (evidence_dir / "bundle.json").write_text(_to_json(bundle_payload), encoding="utf-8")
    (evidence_dir / "summary.md").write_text(summary_markdown.rstrip() + "\n", encoding="utf-8")
    (evidence_dir / "index.json").write_text(_to_json(index), encoding="utf-8")
    return evidence_dir


def relative_bundle_ref(path: Path) -> str:
    cfg = get_config()
    try:
        return str(path.relative_to(cfg.repo_root))
    except ValueError:
        return str(path)


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, default=_json_default) + "\n"


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
