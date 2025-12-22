set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Rebuild the baseline analytics artefacts from canonical data.
baseline session_root="/realm/data/sinity-analysis/baseline-inputs/latest" health_root="/realm/data/health/processed" output_dir="artefacts/baseline/latest" mode="auto" full="true" since="" until="" window_days="90" web_bucket="":
    python pipelines/baseline/build_baseline.py \
      --session-root {{session_root}} \
      --health-root {{health_root}} \
      --output-dir {{output_dir}} \
      --mode {{mode}} \
      {{ if full == "true" { "--full" } else { "" } }} \
      {{ if since != "" { "--since " + since } else { "" } }} \
      {{ if until != "" { "--until " + until } else { "" } }} \
      {{ if full == "true" { "" } else { "--window-days " + window_days } }} \
      {{ if web_bucket != "" { "--include-web-sample --web-bucket " + web_bucket } else { "" } }}

# Refresh the machine-readable ledger of session docs.
session-index:
    python pipelines/ledgers/build_session_index.py \
      --sessions-dir docs/reference/sessions \
      --output artefacts/ledgers/session_index.csv

# Produce a JSON summary of a Markdown transcript (uses OpenAI API by default).
summarise-session input_path output="" model="gpt-4o-mini" api_base="https://api.openai.com/v1":
    python pipelines/sessions/summarise_session.py {{input_path}} \
      --model {{model}} \
      --api-base {{api_base}} \
      {{ if output != "" { "--output " + output } else { "" } }}

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
focus-portal start="" end="" compact="false" output="artefacts/focus/portal/index.html":
    python pipelines/focus/build_focus_portal.py \
      {{ if compact == "true" { "--compact" } else { "" } }} \
      {{ if start != "" { "--start " + start } else { "" } }} \
      {{ if end != "" { "--end " + end } else { "" } }} \
      --output {{output}}

# Generate a compact portal over the full available range (can be slow).
focus-portal-all output="artefacts/focus/portal/all_time.html":
    just focus-portal start=1970-01-01 compact=true output={{output}}

# Generate daily focus narrative (Markdown) from baseline timeline.
daily-focus timeline="artefacts/baseline/latest/activity_timeline.json" start="" end="" output="artefacts/focus/daily_focus_latest.md":
    python pipelines/focus/generate_daily_focus.py \
      --timeline {{timeline}} \
      {{ if start != "" { "--start " + start } else { "" } }} \
      {{ if end != "" { "--end " + end } else { "" } }} \
      --output {{output}}

# Build a per-minute ActivityWatch timeline + viewer bundle (large; regenerable).
minute-timeline since="" until="" output_dir="artefacts/activitywatch-minute-timeline" db="~/.local/share/activitywatch/aw-server-rust/sqlite.db":
    mkdir -p {{output_dir}}
    cp pipelines/activitywatch-minute-timeline/index.html {{output_dir}}/index.html
    cp pipelines/activitywatch-minute-timeline/analysis.html {{output_dir}}/analysis.html
    python pipelines/activitywatch-minute-timeline/build_timeline.py \
      --db {{db}} \
      --output-json {{output_dir}}/timeline_data.json \
      --output-js {{output_dir}}/timeline_data.js \
      {{ if since != "" { "--since " + since } else { "" } }} {{ if until != "" { "--until " + until } else { "" } }}

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

# Generate project bundles for repos (regenerable; ignored).
project-bundles projects="":
    python pipelines/project-bundles/generate_project_bundles.py \
      {{ if projects != "" { "--projects " + projects } else { "" } }}

# Export Wykop activity into canonical `/realm/data/...` JSONLs (resumable).
wykop-export username="Sinity" backend="auto" out_dir="/realm/data/personal-data/my_external_exports/wykop" extras="true":
    python pipelines/wykop/scrape_wykop.py \
      --username {{username}} \
      --out-dir {{out_dir}} \
      --backend {{backend}} \
      {{ if extras == "true" { "--extras" } else { "--no-extras" } }}

# Build a DuckDB knowledge-graph snapshot (regenerable; ignored).
knowledge-graph output="artefacts/knowledge-graph/knowledge_graph.duckdb" manifest="artefacts/knowledge-graph/manifest.json" parquet_dir="":
    python pipelines/knowledge-graph/build_knowledge_graph.py \
      --output {{output}} \
      --manifest {{manifest}} \
      {{ if parquet_dir != "" { "--parquet-dir " + parquet_dir } else { "" } }}

# Rebuild historical life timeline (2020-04 → 2023-04) derived metrics.
life-timeline start="2020-04" end="2023-04" output="artefacts/life-timeline/monthly_life_2020-04_to_2023-04.json" md_output="artefacts/life-timeline/life_2020-04_to_2023-04.generated.md":
    python pipelines/life-timeline/build_life_timeline.py \
      --start {{start}} \
      --end {{end}} \
      --output {{output}} \
      --markdown-output {{md_output}}

# Rebuild life timeline for an arbitrary month range.
life-timeline-range start end:
    python pipelines/life-timeline/build_life_timeline.py \
      --start {{start}} \
      --end {{end}} \
      --output artefacts/life-timeline/monthly_life_{{start}}_to_{{end}}.json \
      --markdown-output artefacts/life-timeline/life_{{start}}_to_{{end}}.generated.md

# Rebuild life timeline with per-year drilldowns (recommended for long ranges).
life-timeline-drilldowns start="2013-10" end="" output="" md_dir="":
    start="{{start}}"; \
    end="{{end}}"; \
    if [[ -z "$end" ]]; then end="$(date -u +%Y-%m)"; fi; \
    output="{{output}}"; \
    if [[ -z "$output" ]]; then output="artefacts/life-timeline/monthly_life_${start}_to_${end}.json"; fi; \
    md_dir="{{md_dir}}"; \
    if [[ -z "$md_dir" ]]; then md_dir="artefacts/life-timeline/life_drilldowns_${start}_to_${end}"; fi; \
    mkdir -p artefacts/life-timeline; \
    python pipelines/life-timeline/build_life_timeline.py \
      --start "$start" \
      --end "$end" \
      --output "$output" \
      --markdown-output-dir "$md_dir"; \
    ln -sfn "$(realpath "$output")" "artefacts/life-timeline/monthly_life_latest.json"; \
    ln -sfn "$(realpath "$md_dir")" "artefacts/life-timeline/life_drilldowns_latest"; \
    printf '%s\\n' "${start}_to_${end}" > "artefacts/life-timeline/life_timeline_latest_range.txt"

# Render the canonical (tracked) full-range month-by-month digest from the latest life JSON.
life-digest output="docs/analysis/life_earliest_to_now.monthly.md":
    python pipelines/life-timeline/render_monthly_digest.py --output {{output}}

# Idempotent rebuild: regenerate latest life timeline + refresh the tracked digest.
life-refresh start="2013-10" end="" digest_output="docs/analysis/life_earliest_to_now.monthly.md":
    just life-timeline-drilldowns {{start}} {{end}}
    just life-digest {{digest_output}}

# Enrich YouTube watch-history video IDs into titles/channels (oEmbed cache; resumable).
youtube-oembed start="2013-10" end="" life_json="" cache="artefacts/life-timeline/youtube_oembed_cache.jsonl" qps="10" workers="32":
    start="{{start}}"; \
    end="{{end}}"; \
    if [[ -z "$end" ]]; then end="$(date -u +%Y-%m)"; fi; \
    life_json="{{life_json}}"; \
    if [[ -z "$life_json" ]]; then life_json="artefacts/life-timeline/monthly_life_${start}_to_${end}.json"; fi; \
    python pipelines/life-timeline/enrich_youtube_oembed.py \
      --life-json "$life_json" \
      --cache {{cache}} \
      --start "$start" \
      --end "$end" \
      --qps {{qps}} \
      --workers {{workers}}

# Remove regenerable outputs/caches from the working tree (safe: ignored by Git).
clean-generated:
    rm -rf artefacts tmp scratch
    find pipelines -type d -name '__pycache__' -prune -exec rm -rf {} +
