set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Rebuild the baseline analytics artefacts from canonical data.
baseline session_root="/realm/session/sinity-analysis/baseline-inputs/2025-10-23" health_root="/realm/data/health/processed" output_dir="artefacts/baseline/2025-10-23-baseline-rebuilt" web_bucket="":
    python pipelines/baseline/build_baseline.py \
      --session-root {{session_root}} \
      --health-root {{health_root}} \
      --output-dir {{output_dir}} \
      {{ if web_bucket != "" { "--include-web-sample --web-bucket " + web_bucket } else { "" } }}

# Refresh the machine-readable ledger of session docs.
session-index:
    python pipelines/ledgers/build_session_index.py \
      --sessions-dir docs/reference/sessions \
      --output artefacts/ledgers/session_index.csv

# Harvest asciinema metadata into artefacts/.
asciinema-metadata root="/realm/data/asciinema_recording" output="artefacts/instrumentation/asciinema_metadata.jsonl":
    python pipelines/instrumentation/collect_asciinema_metadata.py \
      --root {{root}} \
      --output {{output}}

# Harvest audio metadata into artefacts/.
audio-metadata root="/realm/data/audio/raw" output="artefacts/instrumentation/audio_metadata.jsonl":
    python pipelines/instrumentation/collect_audio_metadata.py \
      --root {{root}} \
      --output {{output}}

# Harvest screen metadata into artefacts/.
screen-metadata root="/realm/data/screenshot" output="artefacts/instrumentation/screen_metadata.jsonl":
    python pipelines/instrumentation/collect_screen_metadata.py \
      --root {{root}} \
      --output {{output}}

# Rebuild artefact ledger CSV from catalog JSON.
artefact-index catalog="pipelines/ledgers/artefact_catalog.json" output="artefacts/ledgers/artefact_index.csv":
    python pipelines/ledgers/build_artefact_index.py \
      --catalog {{catalog}} \
      --output {{output}}

# Generate focus portal HTML over a date range.
focus-portal start="2025-09-24" end="2025-10-23" output="artefacts/focus/portal/index.html":
    python pipelines/focus/build_focus_portal.py \
      --start {{start}} \
      --end {{end}} \
      --output {{output}}

# Generate weekly focus report (HTML) using built-in range.
weekly-focus-report:
    python pipelines/focus/generate_weekly_focus_report.py

# Refresh both the session index and artefact ledger.
refresh-ledgers:
    just session-index
    just artefact-index

# Refresh both dashboard exports.
refresh-dashboards:
    just focus-portal
    just weekly-focus-report

# Rebuild historical life timeline (2020-04 → 2023-04) derived metrics.
life-timeline start="2020-04" end="2023-04" output="artefacts/life-timeline/monthly_life_2020-04_to_2023-04.json" md_output="artefacts/life-timeline/life_2020-04_to_2023-04.generated.md":
    python pipelines/life-timeline/build_life_timeline.py \
      --start {{start}} \
      --end {{end}} \
      --output {{output}} \
      --markdown-output {{md_output}}
