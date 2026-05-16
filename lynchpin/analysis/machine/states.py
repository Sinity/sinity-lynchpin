"""Typed machine/work state windows over machine context artifacts."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from lynchpin.analysis.core.io import load_json_if_exists, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime


PRESSURE_KINDS = frozenset({"load_pressure", "cpu_saturation", "memory_pressure", "io_pressure", "blocked_task_pressure"})

WORK_STATE_DEFINITIONS = {
    "ai_agent_work": "AI-agent or Polylogue-associated work window.",
    "build_workload": "Build command or build-classified work window.",
    "deep_work": "ActivityWatch deep-work window without a more specific command class.",
    "devshell_activation": "Direnv or nix develop environment activation/setup window.",
    "general_work": "Attributed work window without a more specific command/source class.",
    "git_commit_work": "Git commit-session work window.",
    "nix_workload": "Nix command workload other than devshell activation.",
    "terminal_work": "Terminal-session work window without a more specific command class.",
    "test_workload": "Test command or test-classified work window.",
}

PRESSURE_STATE_DEFINITIONS = {
    "hardware_regime": "Non-pressure hardware-state episode, usually GPU PCIe link regime.",
    "mixed_pressure": "More than one pressure episode kind overlaps the work window.",
    "non_pressure_episode": "Machine episode overlaps, but it is neither pressure nor a named hardware/service state.",
    "quiet": "No machine episode overlaps the work window.",
    "service_instability": "A sampled systemd service failure overlaps the work window.",
    **{kind: f"Pressure episode dominated by {kind}." for kind in sorted(PRESSURE_KINDS)},
}

REPO_STATE_DEFINITIONS = {
    "mixed_project": "Window is attributed to more than one canonical project.",
    "unattributed": "Window carries no project attribution.",
}


@dataclass(frozen=True)
class MachineStateDefinition:
    category: str
    state: str
    definition: str


@dataclass(frozen=True)
class MachineWorkStateWindow:
    window_id: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    projects: tuple[str, ...]
    source: str
    work_kind: str | None
    work_state: str
    repo_state: str
    pressure_state: str
    hardware_regimes: tuple[str, ...]
    pressure_kinds: tuple[str, ...]
    dominant_episode_kind: str | None
    episode_count: int
    overlap_seconds: float
    pressure_overlap_seconds: float
    confidence: float
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineWorkStateAnalysis:
    generated_for: dict[str, Any]
    window_count: int
    pressure_state_counts: dict[str, int]
    work_state_counts: dict[str, int]
    repo_state_counts: dict[str, int]
    hardware_regime_counts: dict[str, int]
    state_definitions: list[MachineStateDefinition]
    windows: list[MachineWorkStateWindow]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_work_states(
    *,
    start: date | None = None,
    end: date | None = None,
    context_path: Path | None = None,
) -> MachineWorkStateAnalysis:
    payload = load_json_if_exists(context_path or resolve_analysis_path("machine_context_windows.json"))
    if not isinstance(payload, dict):
        return MachineWorkStateAnalysis(
            generated_for=_generated_for(start, end),
            window_count=0,
            pressure_state_counts={},
            work_state_counts={},
            repo_state_counts={},
            hardware_regime_counts={},
            state_definitions=_state_definitions([]),
            windows=[],
            caveats=["machine_context_windows.json absent; machine work-state segmentation skipped"],
        )

    rows = payload.get("windows")
    if not isinstance(rows, list):
        rows = []
    windows = [
        state
        for row in rows
        if isinstance(row, dict)
        for state in [_state_window(row)]
        if state is not None and _overlaps(state, start=start, end=end)
    ]
    windows.sort(key=lambda row: (row.started_at, row.source, row.window_id))
    caveats = list(payload.get("caveats") or [])
    if not windows:
        caveats.append("no machine context windows matched the requested range")
    return MachineWorkStateAnalysis(
        generated_for=_generated_for(start, end),
        window_count=len(windows),
        pressure_state_counts=_counts(row.pressure_state for row in windows),
        work_state_counts=_counts(row.work_state for row in windows),
        repo_state_counts=_counts(row.repo_state for row in windows),
        hardware_regime_counts=_counts(regime for row in windows for regime in row.hardware_regimes),
        state_definitions=_state_definitions(windows),
        windows=windows,
        caveats=sorted(dict.fromkeys(str(caveat) for caveat in caveats if caveat)),
    )


def write_machine_work_state_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    context_path: Path | None = None,
) -> MachineWorkStateAnalysis:
    analysis = analyze_machine_work_states(start=start, end=end, context_path=context_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _state_window(row: dict[str, Any]) -> MachineWorkStateWindow | None:
    started_at = _dt(row.get("started_at"))
    ended_at = _dt(row.get("ended_at"))
    if started_at is None or ended_at is None or ended_at < started_at:
        return None
    episodes = [episode for episode in row.get("episodes", []) if isinstance(episode, dict)]
    pressure_episodes = [episode for episode in episodes if str(episode.get("kind") or "") in PRESSURE_KINDS]
    pressure_kinds = tuple(sorted({str(episode.get("kind")) for episode in pressure_episodes if episode.get("kind")}))
    hardware_regimes = tuple(sorted({str(episode.get("subject")) for episode in episodes if episode.get("kind") == "gpu_link_regime" and episode.get("subject")}))
    duration_seconds = _float(row.get("duration_seconds"))
    pressure_overlap = sum(_float(episode.get("overlap_seconds")) for episode in pressure_episodes)
    dominant = _dominant_episode_kind(episodes)
    return MachineWorkStateWindow(
        window_id=str(row.get("window_id") or f"{started_at.isoformat()}:{row.get('source') or 'unknown'}"),
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        projects=tuple(str(project) for project in row.get("projects", ()) if project),
        source=str(row.get("source") or "unknown"),
        work_kind=str(row.get("work_kind")) if row.get("work_kind") else None,
        work_state=_work_state(row),
        repo_state=_repo_state(row),
        pressure_state=_pressure_state(episodes, pressure_episodes),
        hardware_regimes=hardware_regimes,
        pressure_kinds=pressure_kinds,
        dominant_episode_kind=dominant,
        episode_count=int(row.get("episode_count") or len(episodes)),
        overlap_seconds=_float(row.get("overlap_seconds")),
        pressure_overlap_seconds=round(pressure_overlap, 3),
        confidence=_confidence(duration_seconds, episodes, pressure_overlap),
        caveats=tuple(str(caveat) for caveat in row.get("caveats", ()) if caveat),
    )


def _work_state(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    work_kind = str(row.get("work_kind") or "")
    summary = str(row.get("summary") or "")
    tokens = _word_tokens(" ".join((source, work_kind, summary)))
    terminal_tokens = _word_tokens(summary) if source == "terminal_session" else tokens
    if "direnv" in terminal_tokens or {"nix", "develop"} <= terminal_tokens:
        return "devshell_activation"
    if "nix" in terminal_tokens:
        return "nix_workload"
    if "pytest" in terminal_tokens or work_kind == "test":
        return "test_workload"
    if "cargo" in terminal_tokens or work_kind == "build":
        return "build_workload"
    if {"polylogue", "ai"} & tokens:
        return "ai_agent_work"
    if "commit" in tokens or row.get("source") == "git_commit_session":
        return "git_commit_work"
    if row.get("source") == "terminal_session":
        return "terminal_work"
    if row.get("source") == "deep_work":
        return "deep_work"
    return "general_work"


def _word_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _repo_state(row: dict[str, Any]) -> str:
    projects = tuple(project for project in row.get("projects", ()) if project)
    if not projects:
        return "unattributed"
    if len(projects) == 1:
        return str(projects[0])
    return "mixed_project"


def _pressure_state(episodes: list[dict[str, Any]], pressure_episodes: list[dict[str, Any]]) -> str:
    if not episodes:
        return "quiet"
    if pressure_episodes:
        kinds = {str(episode.get("kind") or "") for episode in pressure_episodes}
        if len(kinds) > 1:
            return "mixed_pressure"
        return next(iter(kinds))
    if any(episode.get("kind") == "service_instability" for episode in episodes):
        return "service_instability"
    if any(episode.get("kind") == "gpu_link_regime" for episode in episodes):
        return "hardware_regime"
    return "non_pressure_episode"


def _dominant_episode_kind(episodes: list[dict[str, Any]]) -> str | None:
    if not episodes:
        return None
    counts: dict[str, float] = {}
    for episode in episodes:
        kind = episode.get("kind")
        if kind:
            key = str(kind)
            counts[key] = counts.get(key, 0.0) + max(_float(episode.get("overlap_seconds")), 1.0)
    return max(counts.items(), key=lambda item: item[1])[0] if counts else None


def _confidence(duration_seconds: float, episodes: list[dict[str, Any]], pressure_overlap: float) -> float:
    if duration_seconds <= 0:
        return 0.3
    if not episodes:
        return 0.6
    overlap_ratio = min(1.0, max(0.0, pressure_overlap / duration_seconds))
    return round(0.65 + overlap_ratio * 0.3, 3)


def _dt(value: object) -> datetime | None:
    return parse_datetime(value)


def _float(value: object) -> float:
    try:
        return float(str(value or 0.0))
    except ValueError:
        return 0.0


def _overlaps(row: MachineWorkStateWindow, *, start: date | None, end: date | None) -> bool:
    if start is not None and row.ended_at.date() < start:
        return False
    if end is not None and row.started_at.date() > end:
        return False
    return True


def _counts(values: Any) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value).items()))


def _generated_for(start: date | None, end: date | None) -> dict[str, Any]:
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "source": "machine_context_windows.json",
    }


def _state_definitions(windows: list[MachineWorkStateWindow]) -> list[MachineStateDefinition]:
    definitions: list[MachineStateDefinition] = []
    for state in sorted({row.work_state for row in windows} | set(WORK_STATE_DEFINITIONS)):
        definitions.append(
            MachineStateDefinition(
                category="work_state",
                state=state,
                definition=WORK_STATE_DEFINITIONS.get(state, "Work-state label emitted by the classifier."),
            )
        )
    for state in sorted({row.pressure_state for row in windows} | set(PRESSURE_STATE_DEFINITIONS)):
        definitions.append(
            MachineStateDefinition(
                category="pressure_state",
                state=state,
                definition=PRESSURE_STATE_DEFINITIONS.get(state, "Machine pressure-state label emitted by the classifier."),
            )
        )
    for state in sorted({row.repo_state for row in windows} | set(REPO_STATE_DEFINITIONS)):
        definitions.append(
            MachineStateDefinition(
                category="repo_state",
                state=state,
                definition=REPO_STATE_DEFINITIONS.get(state, "Canonical project slug for a singly attributed work window."),
            )
        )
    return definitions
