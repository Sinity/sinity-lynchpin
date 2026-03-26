from __future__ import annotations

from datetime import date
from pathlib import Path

LIFE_ARTEFACT_ROOT = Path("artefacts/retrospective/life-range")
LATEST_LIFE_JSON = LIFE_ARTEFACT_ROOT / "monthly_life_latest.json"
LATEST_LIFE_DRILLDOWN_DIR = LIFE_ARTEFACT_ROOT / "life_drilldowns_latest"
LIFE_DIGEST_OUTPUT = LIFE_ARTEFACT_ROOT / "digests/life_earliest_to_now.monthly.md"
LIFE_ROLLUPS_OUTPUT = LIFE_ARTEFACT_ROOT / "narratives/life_auto_summary.md"
YOUTUBE_OEMBED_CACHE = LIFE_ARTEFACT_ROOT / "youtube_oembed_cache.jsonl"
DEFAULT_LIFE_START = "2013-10"


def current_month_key(today: date | None = None) -> str:
    now = today or date.today()
    return f"{now.year:04d}-{now.month:02d}"
