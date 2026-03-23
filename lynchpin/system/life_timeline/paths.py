from __future__ import annotations

from datetime import date
from pathlib import Path

LIFE_TIMELINE_ROOT = Path("artefacts/lifelog/life-timeline")
LATEST_LIFE_TIMELINE_JSON = LIFE_TIMELINE_ROOT / "monthly_life_latest.json"
LATEST_LIFE_TIMELINE_DRILLDOWN_DIR = LIFE_TIMELINE_ROOT / "life_drilldowns_latest"
LIFE_TIMELINE_DIGEST_OUTPUT = LIFE_TIMELINE_ROOT / "digests/life_earliest_to_now.monthly.md"
LIFE_TIMELINE_NARRATIVE_OUTPUT = LIFE_TIMELINE_ROOT / "narratives/life_auto_summary.md"
YOUTUBE_OEMBED_CACHE = LIFE_TIMELINE_ROOT / "youtube_oembed_cache.jsonl"
DEFAULT_LIFE_TIMELINE_START = "2013-10"


def current_month_key(today: date | None = None) -> str:
    now = today or date.today()
    return f"{now.year:04d}-{now.month:02d}"
