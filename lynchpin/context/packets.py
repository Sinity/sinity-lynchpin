from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..trajectory import chains as trajectory_chains
from ..trajectory import day as trajectory_day
from ..trajectory import period as trajectory_period
from ..trajectory import signal as trajectory_signal


def build_recent_state(
    *,
    days: int = 14,
    end: Optional[datetime] = None,
) -> dict[str, object]:
    window_start, window_end = trajectory_signal.resolve_window(end=end, days=days)
    signals = trajectory_signal.load_signals(start=window_start, end=window_end, days=days)
    chains = trajectory_chains.build_chains(signals)
    day_summaries = trajectory_day.summarize_days(
        signals=signals,
        chains=chains,
        start=window_start,
        end=window_end,
        days=days,
    )
    period = trajectory_period.summarize_period(day_summaries)
    recent_chain_cutoff = window_end - timedelta(days=min(days, 3))
    recent_chains = [
        _chain_packet(chain)
        for chain in sorted(chains, key=lambda chain: (chain.start, chain.chain_id), reverse=True)
        if chain.end >= recent_chain_cutoff
    ][:15]
    return {
        "schema": "lynchpin-context-state-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": days,
        },
        "coverage": {
            "signal_count": len(signals),
            "chain_count": len(chains),
            "day_count": len(day_summaries),
        },
        "period": period.to_dict(),
        "current": day_summaries[-1].to_dict() if day_summaries else None,
        "days": [day.to_dict() for day in day_summaries],
        "recent_chains": recent_chains,
    }


def _chain_packet(chain) -> dict[str, object]:
    return {
        "chain_id": chain.chain_id,
        "start": chain.start.isoformat(),
        "end": chain.end.isoformat(),
        "duration_minutes": round(chain.duration_seconds / 60.0, 2),
        "mode": chain.mode,
        "project": chain.project,
        "sources": list(chain.sources),
        "apps": list(chain.apps),
        "domains": list(chain.domains),
        "titles": list(chain.titles[:3]),
        "reasons": list(chain.reasons),
    }
