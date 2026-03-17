"""Delta between two consecutive context state snapshots.

Surfaces what changed between prior and current, rather than absolute state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeltaPacket:
    """Summary of changes between two context states."""
    new_episodes: list[str]         # episode_id values that appear in current but not prior
    ended_episodes: list[str]       # episode_id values in prior but not current
    project_shifts: list[str]       # projects that entered or left top-3 between states
    mode_shifts: list[str]          # dominant_mode changes: e.g. "deep_work → planning"
    new_claims: list[str]           # claim statements in current but not prior
    anomaly_count_delta: int        # current anomaly count minus prior anomaly count
    active_hours_delta: float       # current recent active hours minus prior

    def to_dict(self) -> dict[str, object]:
        return {
            "new_episodes": self.new_episodes,
            "ended_episodes": self.ended_episodes,
            "project_shifts": self.project_shifts,
            "mode_shifts": self.mode_shifts,
            "new_claims": self.new_claims,
            "anomaly_count_delta": self.anomaly_count_delta,
            "active_hours_delta": round(self.active_hours_delta, 2),
        }


def build_delta(
    prior_state: dict[str, Any],
    current_state: dict[str, Any],
) -> DeltaPacket:
    """Build a delta packet from two consecutive context states.

    Both states should be dicts returned from build_current_state().
    """
    # Extract episode_ids from prior and current
    prior_episodes = set()
    current_episodes = set()

    if prior_state.get("episodes"):
        for ep in prior_state["episodes"]:
            if isinstance(ep, dict) and "episode_id" in ep:
                prior_episodes.add(ep["episode_id"])

    if current_state.get("episodes"):
        for ep in current_state["episodes"]:
            if isinstance(ep, dict) and "episode_id" in ep:
                current_episodes.add(ep["episode_id"])

    new_episodes = sorted(list(current_episodes - prior_episodes))
    ended_episodes = sorted(list(prior_episodes - current_episodes))

    # Project shifts: top-3 projects
    prior_projects = set()
    current_projects = set()

    prior_period = prior_state.get("period", {})
    if isinstance(prior_period, dict):
        top_projs = prior_period.get("top_projects")
        if isinstance(top_projs, list):
            prior_projects = {proj for proj, _ in top_projs[:3]}

    current_period = current_state.get("period", {})
    if isinstance(current_period, dict):
        top_projs = current_period.get("top_projects")
        if isinstance(top_projs, list):
            current_projects = {proj for proj, _ in top_projs[:3]}

    # Projects that entered or left
    entered = sorted(list(current_projects - prior_projects))
    left = sorted(list(prior_projects - current_projects))
    project_shifts = [f"{p} entered" for p in entered] + [f"{p} left" for p in left]

    # Mode shifts: current day dominant_mode
    prior_mode = None
    current_mode = None

    prior_current = prior_state.get("current")
    if isinstance(prior_current, dict):
        prior_mode = prior_current.get("dominant_mode")

    current_current = current_state.get("current")
    if isinstance(current_current, dict):
        current_mode = current_current.get("dominant_mode")

    mode_shifts = []
    if prior_mode and current_mode and prior_mode != current_mode:
        mode_shifts = [f"{prior_mode} → {current_mode}"]

    # New claims: extract statement strings
    prior_claims = set()
    current_claims = set()

    prior_claims_packet = prior_state.get("claims", {})
    if isinstance(prior_claims_packet, dict):
        claims_list = prior_claims_packet.get("claims")
        if isinstance(claims_list, list):
            for claim in claims_list:
                if isinstance(claim, dict) and "statement" in claim:
                    prior_claims.add(claim["statement"])

    current_claims_packet = current_state.get("claims", {})
    if isinstance(current_claims_packet, dict):
        claims_list = current_claims_packet.get("claims")
        if isinstance(claims_list, list):
            for claim in claims_list:
                if isinstance(claim, dict) and "statement" in claim:
                    current_claims.add(claim["statement"])

    new_claims = sorted(list(current_claims - prior_claims))

    # Anomaly count delta from coverage packet
    prior_anomaly_count = 0
    current_anomaly_count = 0
    prior_cov = prior_state.get("coverage", {})
    if isinstance(prior_cov, dict):
        val = prior_cov.get("anomaly_count")
        if isinstance(val, int):
            prior_anomaly_count = val
    current_cov = current_state.get("coverage", {})
    if isinstance(current_cov, dict):
        val = current_cov.get("anomaly_count")
        if isinstance(val, int):
            current_anomaly_count = val
    anomaly_count_delta = current_anomaly_count - prior_anomaly_count

    # Active hours delta from period
    prior_active_hours = 0.0
    current_active_hours = 0.0

    if isinstance(prior_period, dict):
        val = prior_period.get("active_hours")
        if isinstance(val, (int, float)):
            prior_active_hours = float(val)
        elif isinstance(prior_period.get("active_seconds"), (int, float)):
            prior_active_hours = float(prior_period["active_seconds"]) / 3600.0

    if isinstance(current_period, dict):
        val = current_period.get("active_hours")
        if isinstance(val, (int, float)):
            current_active_hours = float(val)
        elif isinstance(current_period.get("active_seconds"), (int, float)):
            current_active_hours = float(current_period["active_seconds"]) / 3600.0

    active_hours_delta = current_active_hours - prior_active_hours

    return DeltaPacket(
        new_episodes=new_episodes,
        ended_episodes=ended_episodes,
        project_shifts=project_shifts,
        mode_shifts=mode_shifts,
        new_claims=new_claims,
        anomaly_count_delta=anomaly_count_delta,
        active_hours_delta=active_hours_delta,
    )
