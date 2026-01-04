set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# --- Core baselines & ledgers -------------------------------------------------------

baseline session_root="/realm/data/sinity-lynchpin/baseline-inputs/latest" health_root="/realm/data/exports/health/processed" output_dir="artefacts/core/baseline/latest" mode="auto" full="true" since="" until="" window_days="90" web_bucket="":
    mkdir -p "{{output_dir}}"
    python pipelines/core/baseline/build_baseline.py --session-root "{{session_root}}" --health-root "{{health_root}}" --output-dir "{{output_dir}}" --mode "{{mode}}" {{ if full == "true" { "--full" } else { "--window-days " + window_days } }}{{ if since != "" { " --since " + since } else { "" } }}{{ if until != "" { " --until " + until } else { "" } }}{{ if web_bucket != "" { " --include-web-sample --web-bucket " + web_bucket } else { "" } }}

session-index output="artefacts/knowledge/ledgers/session_index.csv":
    mkdir -p "$(dirname "{{output}}")"
    python -m lynchpin.views.ledgers session --sessions-dir "docs/reference/sessions" --output "{{output}}"

artefact-index output="artefacts/knowledge/ledgers/artefact_index.csv":
    mkdir -p "$(dirname "{{output}}")"
    python -m lynchpin.views.ledgers artefact --catalog "docs/reference/ledgers/artefact_catalog.json" --output "{{output}}"

refresh-ledgers:
    just session-index
    just artefact-index

ledgers target="session" output="":
    if [[ "{{target}}" == "session" ]]; then out="{{output}}"; if [[ -z "$out" ]]; then out="artefacts/knowledge/ledgers/session_index.csv"; fi; mkdir -p "$(dirname "$out")"; python -m lynchpin.views.ledgers session --sessions-dir "docs/reference/sessions" --output "$out"; elif [[ "{{target}}" == "artefact" ]]; then out="{{output}}"; if [[ -z "$out" ]]; then out="artefacts/knowledge/ledgers/artefact_index.csv"; fi; mkdir -p "$(dirname "$out")"; python -m lynchpin.views.ledgers artefact --catalog "docs/reference/ledgers/artefact_catalog.json" --output "$out"; else echo "Unknown ledger target: {{target}}" >&2; exit 1; fi

# --- Session summaries & context ----------------------------------------------------

summarise-session input_path output="" model="gpt-5-mini" api_base="https://api.openai.com/v1":
    python -m lynchpin.views.session_summaries summarise {{input_path}} \
    --model {{model}} \
    --api-base {{api_base}} \
    {{ if output != "" { "--output " + output } else { "" } }}

# --- Instrumentation metadata -------------------------------------------------------

asciinema-metadata root="/realm/data/captures/asciinema" output="artefacts/ingest/instrumentation/asciinema_metadata.jsonl":
    python -m lynchpin.ingest.instrumentation asciinema --root {{root}} --output {{output}}

audio-metadata root="/realm/data/captures/audio/raw" output="artefacts/ingest/instrumentation/audio_metadata.jsonl":
    python -m lynchpin.ingest.instrumentation audio --root {{root}} --output {{output}}

screen-metadata root="/realm/data/captures/screenshot" output="artefacts/ingest/instrumentation/screen_metadata.jsonl":
    python -m lynchpin.ingest.instrumentation screen --root {{root}} --output {{output}}

webhistory-full-history root="/realm/data/captures/webhistory/gestalt/data" output="/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson":
    python -m lynchpin.ingest.webhistory full-history --root {{root}} --output {{output}}

webhistory-dedup raw_root="/realm/data/captures/webhistory/gestalt/raw" output_dir="/realm/data/captures/webhistory/gestalt/data" tolerance="5":
    python -m lynchpin.ingest.webhistory dedup --raw-root {{raw_root}} --output-dir {{output_dir}} --tolerance-seconds {{tolerance}}

webhistory-compare canonical="/realm/data/captures/webhistory/gestalt/data" candidate="/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson" output="artefacts/webhistory/gestalt_compare.json":
    python -m lynchpin.ingest.webhistory compare --canonical {{canonical}} --candidate {{candidate}} --output {{output}}

# --- Lynchpin helpers -------------------------------------------------------------

lynchpin-warehouse mode="views" format="parquet" sources="" limit="" root="" output="":
    cmd=(python -m lynchpin.views.warehouse --mode "{{mode}}" --format "{{format}}")
    if [[ -n "{{sources}}" ]]; then cmd+=(--sources "{{sources}}"); fi
    if [[ -n "{{limit}}" ]]; then cmd+=(--limit "{{limit}}"); fi
    if [[ -n "{{root}}" ]]; then cmd+=(--root "{{root}}"); fi
    if [[ -n "{{output}}" ]]; then cmd+=(--output "{{output}}"); fi
    "${cmd[@]}"

lynchpin-datasette:
    if ! command -v datasette >/dev/null 2>&1; then echo "datasette CLI not found; install via 'pipx install datasette' or add it to the devshell." >&2; exit 1; fi
    datasette artefacts/lynchpin/warehouse.duckdb

validate-lynchpin quick="true" output="artefacts/lynchpin/validation/lynchpin.jsonl":
    python -m lynchpin.system.validate lynchpin --output "{{output}}" {{ if quick == "true" { "--quick" } else { "--no-quick" } }}

validate-hpi quick="true" output="artefacts/lynchpin/validation/hpi.jsonl":
    python -m lynchpin.system.validate hpi --output "{{output}}" {{ if quick == "true" { "--quick" } else { "--no-quick" } }}

materialize webhistory="true" ledgers="true" warehouse="true" velocity="false" baseline="false":
    python -m lynchpin.system.materialize run \
    {{ if webhistory == "true" { "--webhistory" } else { "--no-webhistory" } }} \
    {{ if ledgers == "true" { "--ledgers" } else { "--no-ledgers" } }} \
    {{ if warehouse == "true" { "--warehouse" } else { "--no-warehouse" } }} \
    {{ if velocity == "true" { "--velocity" } else { "--no-velocity" } }} \
    {{ if baseline == "true" { "--baseline" } else { "--no-baseline" } }}

# --- Calendar views & narratives ---------------------------------------------------

calendar-refresh start="" end="" output_dir="artefacts/calendar/views" write_files="true" json="false":
    end="{{end}}"
    if [[ -z "$end" ]]; then end="$(date -I)"; fi
    start="{{start}}"
    if [[ -z "$start" ]]; then start="$(date -I -d "$end - 6 days")"; fi
    cmd=(python -m lynchpin.views.calendar_views build "$start" "$end" --output "{{output_dir}}")
    if [[ "{{write_files}}" == "true" ]]; then
    cmd+=(--write-files)
    else
    cmd+=(--no-write-files)
    fi
    if [[ "{{json}}" == "true" ]]; then
    cmd+=(--json)
    fi
    "${cmd[@]}"

calendar-narrative start end mode="reflective" output="" prompt_only="false" model="":
    cmd=(python -m lynchpin.views.calendar_narratives narrative "{{start}}" "{{end}}" --mode "{{mode}}")
    if [[ -n "{{output}}" ]]; then
    cmd+=(--output "{{output}}")
    fi
    if [[ "{{prompt_only}}" == "true" ]]; then
    cmd+=(--prompt-only)
    fi
    if [[ -n "{{model}}" ]]; then
    cmd+=(--model "{{model}}")
    fi
    "${cmd[@]}"

# --- Context bundles & repo metrics -------------------------------------------------

project-bundles projects="":
    python -m lynchpin.views.project_bundles {{ if projects != "" { "--projects " + projects } else { "" } }}

velocity:
    python -m lynchpin.views.velocity

# --- Data exports & knowledge graph -------------------------------------------------

wykop-export username="Sinity" backend="auto" out_dir="/realm/data/exports/wykop/raw" extras="true":
    python -m lynchpin.ingest.wykop_export \
    --username {{username}} \
    --backend {{backend}} \
    --out-dir {{out_dir}} \
    {{ if extras == "true" { "--extras" } else { "--no-extras" } }}

fbmessenger-export db="/realm/data/exports/comms/facebook-messenger/processed/fbmessengerexport.sqlite" cookie_db="~/.config/google-chrome/Default/Cookies" dry_run="false" remote_debug_port="" launch_debug_chrome="false":
    python -m lynchpin.ingest.fbmessenger_export \
    --db {{db}} \
    --cookie-db {{cookie_db}} \
    {{ if remote_debug_port != "" { "--remote-debug-port " + remote_debug_port } else { "" } }} \
    {{ if launch_debug_chrome == "true" { "--launch-debug-chrome" } else { "" } }} \
    {{ if dry_run == "true" { "--dry-run" } else { "" } }}

knowledge-graph output="artefacts/knowledge/graph/knowledge_graph.duckdb" manifest="artefacts/knowledge/graph/manifest.json" parquet_dir="":
    mkdir -p "$(dirname {{output}})"
    python -m lynchpin.views.knowledge_graph build \
    --output {{output}} \
    --manifest {{manifest}} \
    {{ if parquet_dir != "" { "--parquet-dir " + parquet_dir } else { "" } }}

# --- Life timeline family -----------------------------------------------------------

life-timeline start="2020-04" end="2023-04" output="artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json" md_output="artefacts/lifelog/life-timeline/life_2020-04_to_2023-04.generated.md":
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
    --start "{{start}}" \
    --end "{{end}}" \
    --output "{{output}}" \
    --markdown-output "{{md_output}}"

life-timeline-range start end:
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
    --start "{{start}}" \
    --end "{{end}}" \
    --output "artefacts/lifelog/life-timeline/monthly_life_{{start}}_to_{{end}}.json" \
    --markdown-output "artefacts/lifelog/life-timeline/life_{{start}}_to_{{end}}.generated.md"

life-timeline-drilldowns start="2013-10" end="" output="" md_dir="":
    start="{{start}}"
    end="{{end}}"
    if [[ -z "$end" ]]; then end="$(date +%Y-%m)"; fi
    output="{{output}}"
    if [[ -z "$output" ]]; then output="artefacts/lifelog/life-timeline/monthly_life_${start}_to_${end}.json"; fi
    md_dir="{{md_dir}}"
    if [[ -z "$md_dir" ]]; then md_dir="artefacts/lifelog/life-timeline/life_drilldowns_${start}_to_${end}"; fi
    python pipelines/lifelog/life-timeline/build_life_timeline.py \
    --start "$start" \
    --end "$end" \
    --output "$output" \
    --markdown-output-dir "$md_dir"
    ln -sfn "$(realpath "$output")" "artefacts/lifelog/life-timeline/monthly_life_latest.json"
    ln -sfn "$(realpath "$md_dir")" "artefacts/lifelog/life-timeline/life_drilldowns_latest"
    echo "${start}_to_${end}" > "artefacts/lifelog/life-timeline/life_timeline_latest_range.txt"

life-digest output="artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md":
    python pipelines/lifelog/life-timeline/render_monthly_digest.py --output "{{output}}"

life-refresh start="2013-10" end="" digest_output="artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md":
    just life-timeline-drilldowns start={{start}} end={{end}}
    just life-digest output={{digest_output}}
    just life-auto-narrative

youtube-oembed start="2013-10" end="" life_json="" cache="artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl" qps="10" workers="32":
    end="{{end}}"
    if [[ -z "$end" ]]; then end="$(date +%Y-%m)"; fi
    life_json="{{life_json}}"
    if [[ -z "$life_json" ]]; then life_json="artefacts/lifelog/life-timeline/monthly_life_{{start}}_to_${end}.json"; fi
    python pipelines/lifelog/life-timeline/enrich_youtube_oembed.py \
    --life-json "$life_json" \
    --cache "{{cache}}" \
    --start "{{start}}" \
    --end "$end" \
    --qps "{{qps}}" \
    --workers "{{workers}}"

life-auto-narrative life_json="artefacts/lifelog/life-timeline/monthly_life_latest.json" output="artefacts/lifelog/life-timeline/narratives/life_auto_summary.md" quarter_limit="8" year_limit="10":
    python pipelines/lifelog/life-timeline/generate_auto_narrative.py \
    --life-json "{{life_json}}" \
    --output "{{output}}" \
    --quarter-limit "{{quarter_limit}}" \
    --year-limit "{{year_limit}}"

# --- Utilities ----------------------------------------------------------------------

clean-generated:
    rm -rf artefacts tmp scratch
    find . -type d -name '__pycache__' -prune -exec rm -rf {} +
