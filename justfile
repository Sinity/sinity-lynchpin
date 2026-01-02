set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# --- Core baselines & ledgers -------------------------------------------------------

baseline session_root="/realm/data/sinity-lynchpin/baseline-inputs/latest" health_root="/realm/data/health/processed" output_dir="artefacts/core/baseline/latest" mode="auto" full="true" since="" until="" window_days="90" web_bucket="":
    mkdir -p {{output_dir}}
    python pipelines/core/baseline/build_baseline.py \
      --session-root {{session_root}} \
      --health-root {{health_root}} \
      --output-dir {{output_dir}} \
      --mode {{mode}} \
      {{ if full == "true" { "--full" } else { "" } }} \
      {{ if since != "" { "--since " + since } else { "" } }} \
      {{ if until != "" { "--until " + until } else { "" } }} \
      {{ if full == "true" { "" } else { "--window-days " + window_days } }} \
      {{ if web_bucket != "" { "--include-web-sample --web-bucket " + web_bucket } else { "" } }}

session-index sessions_dir="docs/reference/sessions" output="artefacts/knowledge/ledgers/session_index.csv":
    just ledgers target=session sessions_dir={{sessions_dir}} output={{output}}

artefact-index catalog="pipelines/knowledge/ledgers/artefact_catalog.json" output="artefacts/knowledge/ledgers/artefact_index.csv":
    just ledgers target=artefact catalog={{catalog}} output={{output}}

refresh-ledgers:
    just session-index
    just artefact-index

ledgers target="session" sessions_dir="docs/reference/sessions" catalog="pipelines/knowledge/ledgers/artefact_catalog.json" output="":
    target="{{target}}"
    sessions_dir="{{sessions_dir}}"
    catalog="{{catalog}}"
    output="{{output}}"
    if [[ "$target" == "session" ]]; then
    if [[ -z "$output" ]]; then output="artefacts/knowledge/ledgers/session_index.csv"; fi
    mkdir -p "$(dirname "$output")"
    python pipelines/knowledge/ledgers/build_session_index.py \
      --sessions-dir "$sessions_dir" \
      --output "$output"
    elif [[ "$target" == "artefact" ]]; then
    if [[ -z "$output" ]]; then output="artefacts/knowledge/ledgers/artefact_index.csv"; fi
    mkdir -p "$(dirname "$output")"
    python pipelines/knowledge/ledgers/build_artefact_index.py \
      --catalog "$catalog" \
      --output "$output"
    else
    echo "Unknown ledger target: $target" >&2
    exit 1
    fi

# --- Session summaries & context ----------------------------------------------------

summarise-session input_path output="" model="gpt-4o-mini" api_base="https://api.openai.com/v1":
    python pipelines/knowledge/sessions/summarise_session.py {{input_path}} \
      --model {{model}} \
      --api-base {{api_base}} \
      {{ if output != "" { "--output " + output } else { "" } }}

# --- Instrumentation metadata -------------------------------------------------------

asciinema-metadata root="/realm/data/asciinema_recording" output="artefacts/ingest/instrumentation/asciinema_metadata.jsonl":
    python -m lynchpin.instrumentation asciinema --root {{root}} --output {{output}}

audio-metadata root="/realm/data/audio/raw" output="artefacts/ingest/instrumentation/audio_metadata.jsonl":
    python -m lynchpin.instrumentation audio --root {{root}} --output {{output}}

screen-metadata root="/realm/data/screenshot" output="artefacts/ingest/instrumentation/screen_metadata.jsonl":
    python -m lynchpin.instrumentation screen --root {{root}} --output {{output}}

# --- Lynchpin helpers -------------------------------------------------------------

lynchpin-warehouse:
    python -m lynchpin.warehouse

lynchpin-datasette:
    if ! command -v datasette >/dev/null 2>&1; then echo "datasette CLI not found; install via 'pipx install datasette' or add it to the devshell." >&2; exit 1; fi
    datasette artefacts/lynchpin/warehouse.duckdb

# --- Calendar views & narratives ---------------------------------------------------

calendar-refresh start="" end="" output_dir="artefacts/calendar/views" write_files="true" json="false":
    if [[ -z "$end" ]]; then end="$(date -u +%F)"; fi
    if [[ -z "$start" ]]; then start="$(date -u -d "$end -6 days" +%F)"; fi
    echo "[calendar-refresh] Rendering Lynchpin views for $start → $end"
    python pipelines/focus/calendar/view_builder.py \
      "$start" \
      "$end" \
      --output "$output_dir" \
      $(if [[ "$write_files" == "true" ]]; then echo "--write-files"; else echo "--no-write-files"; fi) \
      $(if [[ "$json" == "true" ]]; then echo "--json"; fi)

calendar-narrative start end mode="reflective" output="" prompt_only="false" model="":
    python pipelines/focus/calendar/generate_narrative.py \
      {{start}} \
      {{end}} \
      --mode {{mode}} \
      {{ if output != "" { "--output " + output } else { "" } }} \
      {{ if prompt_only == "true" { "--prompt-only" } else { "" } }} \
      {{ if model != "" { "--model " + model } else { "" } }}

# --- Context bundles & repo metrics -------------------------------------------------

project-bundles projects="":
    mkdir -p artefacts/context/project-bundles
    python pipelines/context/project-bundles/generate_project_bundles.py \
      {{ if projects != "" { "--projects " + projects } else { "" } }}

velocity:
    python pipelines/meta/velocity/plot_velocity.py

# --- Data exports & knowledge graph -------------------------------------------------

wykop-export username="Sinity" backend="auto" out_dir="/realm/data/wykop" extras="true":
    mkdir -p {{out_dir}}
    python pipelines/lifelog/wykop/scrape_wykop.py \
      --username {{username}} \
      --out-dir {{out_dir}} \
      --backend {{backend}} \
      {{ if extras == "true" { "--extras" } else { "--no-extras" } }}

knowledge-graph output="artefacts/knowledge/graph/knowledge_graph.duckdb" manifest="artefacts/knowledge/graph/manifest.json" parquet_dir="":
    mkdir -p "$(dirname {{output}})"
    python pipelines/knowledge/graph/build_knowledge_graph.py \
      --output {{output}} \
      --manifest {{manifest}} \
      {{ if parquet_dir != "" { "--parquet-dir " + parquet_dir } else { "" } }}

# --- Life timeline family -----------------------------------------------------------

life-timeline start="2020-04" end="2023-04" output="artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json" md_output="artefacts/lifelog/life-timeline/life_2020-04_to_2023-04.generated.md":
    mkdir -p artefacts/lifelog/life-timeline
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
      --start {{start}} \
      --end {{end}} \
      --output {{output}} \
      --markdown-output {{md_output}}

life-timeline-range start end:
    mkdir -p artefacts/lifelog/life-timeline
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
      --start {{start}} \
      --end {{end}} \
      --output artefacts/lifelog/life-timeline/monthly_life_{{start}}_to_{{end}}.json \
      --markdown-output artefacts/lifelog/life-timeline/life_{{start}}_to_{{end}}.generated.md

life-timeline-drilldowns start="2013-10" end="" output="" md_dir="":
    start="{{start}}"; \
    end="{{end}}"; \
    if [[ -z "$end" ]]; then end="$(date -u +%Y-%m)"; fi; \
    output="{{output}}"; \
    if [[ -z "$output" ]]; then output="artefacts/lifelog/life-timeline/monthly_life_${start}_to_${end}.json"; fi; \
    md_dir="{{md_dir}}"; \
    if [[ -z "$md_dir" ]]; then md_dir="artefacts/lifelog/life-timeline/life_drilldowns_${start}_to_${end}"; fi; \
    mkdir -p artefacts/lifelog/life-timeline; \
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
      --start "$start" \
      --end "$end" \
      --output "$output" \
      --markdown-output-dir "$md_dir"; \
    ln -sfn "$(realpath "$output")" "artefacts/lifelog/life-timeline/monthly_life_latest.json"; \
    ln -sfn "$(realpath "$md_dir")" "artefacts/lifelog/life-timeline/life_drilldowns_latest"; \
    printf '%s\n' "${start}_to_${end}" > "artefacts/lifelog/life-timeline/life_timeline_latest_range.txt"

life-digest output="artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md":
    mkdir -p "$(dirname {{output}})"
    python pipelines/lifelog/life-timeline/render_monthly_digest.py --output {{output}}

life-refresh start="2013-10" end="" digest_output="artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md":
    just life-timeline-drilldowns {{start}} {{end}}
    just life-digest {{digest_output}}
    just life-auto-narrative

youtube-oembed start="2013-10" end="" life_json="" cache="artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl" qps="10" workers="32":
    start="{{start}}"; \
    end="{{end}}"; \
    if [[ -z "$end" ]]; then end="$(date -u +%Y-%m)"; fi; \
    life_json="{{life_json}}"; \
    if [[ -z "$life_json" ]]; then life_json="artefacts/lifelog/life-timeline/monthly_life_${start}_to_${end}.json"; fi; \
    mkdir -p "$(dirname {{cache}})"; \
    python pipelines/lifelog/life-timeline/enrich_youtube_oembed.py \
      --life-json "$life_json" \
      --cache {{cache}} \
      --start "$start" \
      --end "$end" \
      --qps {{qps}} \
      --workers {{workers}}

life-auto-narrative life_json="artefacts/lifelog/life-timeline/monthly_life_latest.json" output="artefacts/lifelog/life-timeline/narratives/life_auto_summary.md" quarter_limit="8" year_limit="10":
    mkdir -p "$(dirname {{output}})"
    python pipelines/lifelog/life-timeline/generate_auto_narrative.py \
      --life-json {{life_json}} \
      --output {{output}} \
      --quarter-limit {{quarter_limit}} \
      --year-limit {{year_limit}}

# --- Utilities ----------------------------------------------------------------------

clean-generated:
    rm -rf artefacts tmp scratch
    find pipelines -type d -name '__pycache__' -prune -exec rm -rf {} +
