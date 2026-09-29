"""Default entry paths of the commit-fact analysis builders.

The builders read the published substrate read-only.  Each test seeds a serving
substrate with two overlapping promotion generations, so a reader that opened a
canonical writer or merged generations fails here.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from lynchpin.analysis.active.ai_assist_density import run_active_ai_assist_density
from lynchpin.analysis.active.ai_attribution import run_active_ai_attribution
from lynchpin.analysis.change.commit_capsules import (
    run_active_commit_hunks,
    run_active_commit_semantics,
)
from lynchpin.analysis.change.work_packages import (
    build_active_work_packages,
    run_active_work_packages,
)
from lynchpin.analysis.cli import build_app
from lynchpin.analysis.ecosystem.ai_attribution_history import (
    run_active_ai_attribution_history,
)
from lynchpin.substrate.connection import (
    CandidateGenerationRejected,
    _substrate_path_override,
    apply_schema,
    connect,
    substrate_path,
)
from lynchpin.substrate.work_commits import read_commit_facts

UTC = timezone.utc
START = date(2026, 5, 1)
END = date(2026, 5, 31)
PROJECT = "demo-project"


def _seed(path: Path, generations: dict[str, list[str]]) -> None:
    """Write one ``commit_fact`` partition and ``ok`` status per generation."""
    with duckdb.connect(str(path)) as conn:
        apply_schema(conn)
        for offset, (refresh_id, shas) in enumerate(generations.items()):
            for index, sha in enumerate(shas):
                conn.execute(
                    """
                    INSERT INTO commit_fact (
                        sha, repo, project, authored_at, author, subject,
                        paths, path_roots, conventional_kind,
                        conventional_signature, refresh_id
                    )
                    VALUES (?, ?, ?, ?, 'Tester', ?, ['src/a.py'], ['src'],
                            'feat', 'feat(core)', ?)
                    """,
                    [
                        sha,
                        PROJECT,
                        PROJECT,
                        datetime(2026, 5, 10 + index, 12, tzinfo=UTC),
                        f"feat(core): change {sha[:4]}",
                        refresh_id,
                    ],
                )
            conn.execute(
                """
                INSERT INTO substrate_source_status
                (refresh_id, source, kind, status, reason, row_count,
                 window_start, window_end, recorded_at)
                VALUES (?, 'commits', 'stage', 'ok', NULL, ?, ?, ?, ?)
                """,
                [
                    refresh_id,
                    len(shas),
                    START,
                    END,
                    datetime(2026, 6, 1 + offset, tzinfo=UTC),
                ],
            )


@pytest.fixture
def serving_substrate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_LOCK_ROOT", str(tmp_path / "locks"))
    target = substrate_path()
    _seed(
        target,
        {
            "gen-older": ["a" * 40],
            "gen-newer": ["a" * 40, "b" * 40],
        },
    )
    return target


def _no_polylogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.active.ai_attribution.iter_session_profiles",
        lambda **_: iter(()),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.ecosystem.ai_attribution_history.iter_session_profiles",
        lambda **_: iter(()),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.ai_assist_density.work_events",
        lambda **_: iter(()),
    )


def test_serving_substrate_rejects_writer_connections(serving_substrate: Path) -> None:
    with pytest.raises(CandidateGenerationRejected):
        with connect(substrate_path()):
            pass


def test_read_commit_facts_selects_one_generation(serving_substrate: Path) -> None:
    with connect(substrate_path(), read_only=True) as conn:
        payload = read_commit_facts(conn, start=START, end=END)
        older = read_commit_facts(conn, start=START, end=END, refresh_id="gen-older")

    assert payload["refresh_id"] == "gen-newer"
    assert sorted(row["sha"] for row in payload["commits"]) == ["a" * 40, "b" * 40]
    assert [row["sha"] for row in older["commits"]] == ["a" * 40]


def test_read_commit_facts_without_promotions_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_LOCK_ROOT", str(tmp_path / "locks"))
    with duckdb.connect(str(substrate_path())) as conn:
        apply_schema(conn)
    with connect(substrate_path(), read_only=True) as conn:
        payload = read_commit_facts(conn, start=START, end=END)
    assert payload["commits"] == []
    assert payload["refresh_id"] is None


def test_default_builders_read_serving_commit_facts(
    serving_substrate: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_polylogue(monkeypatch)
    window = {"start": START, "end": END, "projects": [PROJECT]}

    packages = run_active_work_packages(tmp_path / "packages.json", **window)
    hunks = run_active_commit_hunks(tmp_path / "hunks.json", **window)
    semantics = run_active_commit_semantics(tmp_path / "semantics.json", **window)
    attribution = run_active_ai_attribution(tmp_path / "attribution.json", **window)
    density = run_active_ai_assist_density(tmp_path / "density.json", **window)
    history = run_active_ai_attribution_history(
        tmp_path / "history.json", projects=[PROJECT]
    )

    assert sum(row["commit_count"] for row in packages["projects"]) == 2
    assert hunks["commit_count"] == 2
    assert semantics["commit_count"] == 2
    assert len(attribution["commits"]) == 2
    assert len(density["commits"]) == 2
    assert sum(row["total_commits"] for row in history["monthly"]) == 2


def test_candidate_context_reads_its_staged_generation(
    serving_substrate: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = tmp_path / "candidate.duckdb"
    _seed(candidate, {"gen-candidate": ["c" * 40]})
    token = _substrate_path_override.set(candidate)
    try:
        payload = build_active_work_packages(start=START, end=END, projects=[PROJECT])
    finally:
        _substrate_path_override.reset(token)

    assert sum(row["commit_count"] for row in payload["projects"]) == 1


def _commit_facts_file(tmp_path: Path) -> Path:
    path = tmp_path / "selected_commit_facts.json"
    path.write_text(
        json.dumps(
            {
                "commits": [
                    {
                        "project": PROJECT,
                        "sha": "d" * 40,
                        "short_sha": "d" * 7,
                        "timestamp": "2026-05-20T12:00:00+00:00",
                        "date": "2026-05-20",
                        "subject": "fix(core): selected input",
                        "conventional_kind": "fix",
                        "conventional_signature": "fix(core)",
                        "paths": ["src/d.py"],
                        "path_roots": ["src"],
                    }
                ],
                "projects": [{"project": PROJECT, "default_branch": "main"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _commit_shas(value: object) -> set[str]:
    if isinstance(value, dict):
        found = {str(value["sha"])} if value.get("sha") else set()
        found.update(str(sha) for sha in value.get("commit_shas") or ())
        for child in value.values():
            found |= _commit_shas(child)
        return found
    if isinstance(value, list):
        return set().union(*(_commit_shas(item) for item in value))
    return set()


@pytest.mark.parametrize(
    "command",
    ["active-work-packages", "active-commit-semantics", "active-ai-attribution"],
)
def test_commit_facts_option_selects_its_input(
    serving_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
) -> None:
    _no_polylogue(monkeypatch)
    out = tmp_path / f"{command}.json"
    result = CliRunner().invoke(
        build_app(),
        [
            command,
            "--start", START.isoformat(),
            "--end", END.isoformat(),
            "--commit-facts", str(_commit_facts_file(tmp_path)),
            "--out", str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    shas = _commit_shas(json.loads(out.read_text(encoding="utf-8")))
    assert "d" * 40 in shas
    assert all(("d" * 40).startswith(sha) for sha in shas)
