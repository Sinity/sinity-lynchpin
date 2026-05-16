#!/usr/bin/env python3
"""Process Samsung Health exports into unified health JSONL.

Reads all Samsung Health export directories (including unexpanded archives)
AND the Samsung GDPR cloud export, deduplicates by datauuid (record with more
populated fields wins), and writes:

  In-app + GDPR merged:
  - health_sleep.jsonl              — all sleep records (naps + full sleep)
  - health_stress.jsonl             — stress measurements
  - health_steps.jsonl              — daily step counts
  - health_hrv.jsonl                — heart rate variability with SDNN/RMSSD
  - health_vitality.jsonl           — daily vitality scores
  - health_weight.jsonl             — body composition measurements
  - health_skin_temperature.jsonl   — skin temperature readings
  - health_floors.jsonl             — floors climbed
  - health_mood.jsonl               — mood entries (1-5 scale)
  - health_snoring.jsonl            — sleep snoring durations
  - health_heart_rate.jsonl         — heart rate measurements
  - health_spo2.jsonl               — blood oxygen saturation

  GDPR-only categories:
  - health_sleep_stages.jsonl       — per-stage sleep data (awake/light/deep/REM)
  - health_activity_summary.jsonl   — daily activity summaries
  - health_movement.jsonl           — movement episodes
  - health_ecg.jsonl                — electrocardiogram readings
  - health_calories.jsonl           — daily calories burned
  - health_naps.jsonl               — nap data with vitality scores

Usage:
    python -m lynchpin.cli.process_health [--dry-run]
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from lynchpin.cli.health_gdpr_processors import (
    process_activity_summary,
    process_calories,
    process_ecg,
    process_movement,
    process_naps,
    process_respiratory_rate,
    process_sleep_stages,
)
from lynchpin.cli.health_io import PROCESSED
from lynchpin.cli.health_sleep_processor import process_sleep
from lynchpin.cli.health_signal_processors import (
    process_floors,
    process_heart_rate,
    process_hrv,
    process_mood,
    process_skin_temperature,
    process_snoring,
    process_spo2,
    process_steps,
    process_stress,
    process_vitality,
    process_weight,
)

Processor = Callable[[bool], int]

MERGED_PROCESSORS: tuple[Processor, ...] = (
    process_sleep,
    process_stress,
    process_steps,
    process_hrv,
    process_vitality,
    process_weight,
    process_skin_temperature,
    process_floors,
    process_mood,
    process_snoring,
    process_heart_rate,
    process_spo2,
)

GDPR_ONLY_PROCESSORS: tuple[Processor, ...] = (
    process_respiratory_rate,
    process_sleep_stages,
    process_activity_summary,
    process_movement,
    process_ecg,
    process_calories,
    process_naps,
)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    dry_run = "--dry-run" in sys.argv
    PROCESSED.mkdir(parents=True, exist_ok=True)

    for processor in (*MERGED_PROCESSORS, *GDPR_ONLY_PROCESSORS):
        processor(dry_run)


if __name__ == "__main__":
    main()
