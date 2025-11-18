# ActivityWatch Minute Timeline Export

## Purpose
Produce a high-granularity (per-minute) timeline spanning the full ActivityWatch history, classify minutes via TF‑IDF + MiniBatchKMeans, and surface smoothed focus blocks in an interactive HTML viewer. This complements the daily baseline pipeline by exposing mixed-activity periods and short context switches that get averaged away in day summaries.

## Location
Implementation lives in `~/activitywatch-timeline/` (outside the repo to keep artefacts large). The directory currently contains:

- `build_timeline.py` – exporter script.
- `.venv/` – Python 3.13 virtualenv (`numpy`, `pandas`, `scikit-learn`, etc.).
- `timeline_data.{json,js}` – generated dataset (~42 MiB each).
- `index.html` – interactive client for local exploration.
- `analysis.html` – focus analytics dashboard (category rollups, daily summaries, CSV export).

Keep the script mirrored here conceptually; if you relocate or vendor it into this repo, update this doc and any references.

## Prerequisites
- ActivityWatch server data at `~/.local/share/activitywatch/aw-server-rust/sqlite.db`.
- NixOS environment with `nix-ld` available. The exporter requires `libstdc++.so.6`; use the prebuilt path below or update as needed.
- Python virtualenv prepared once via:
  ```bash
  cd ~/activitywatch-timeline
  python3 -m venv .venv
  source .venv/bin/activate
  pip install numpy pandas scikit-learn
  ```

## Usage
Run the exporter from `~/activitywatch-timeline`:

```bash
cd ~/activitywatch-timeline
source .venv/bin/activate
LD_LIBRARY_PATH=/nix/store/dphi8clmgplp05yg1g19irbz92y1w6lp-ld-library-path/share/nix-ld/lib:$LD_LIBRARY_PATH \
python build_timeline.py \
  --output-json timeline_data.json \
  --output-js timeline_data.js
```

Key flags:
- `--since YYYY-MM-DD` / `--until YYYY-MM-DD` to restrict the range.
- `--db /path/to/sqlite.db` if ActivityWatch lives elsewhere.
- To skip the JS wrapper, pass `--output-js ""` (creates JSON only).

The script emits:
- `timeline_data.json` – compact dataset with lookup indices, flattened embeddings, and metadata (`generated_at`, cluster terms, day index map).
- `timeline_data.js` – wraps the JSON as `window.TIMELINE_DATA` for direct browser loading.

## Interactive Viewer
Open `index.html` directly in a browser (no server required). The page auto-loads `timeline_data.js` when both files reside in the same directory. Controls include:

- **Range selectors** – limit timeline via `Range Start/End` (per ISO date).
- **Minimum Block Length** – reassigns short cluster runs to neighbours (default 6 min).
- **Shared Fragment Cap** – maximum length of a fragment eligible for “shared” blending (default 4 min).
- **Shared Detection Window** – sliding window size for merging interleaved micro-tasks (default 15 min).
- **Shared Dominance Threshold** – converts to shared block when top activity ≤ threshold (default 65 %).
- **Shared Activity Limit** – maximum distinct clusters shown in a blended block (default 3).
- **Host chips** – toggle hosts on/off to grey out blocks that do not involve the selected machines.

Each day renders as a horizontal lane with gradient blocks (shared) or solid colours (single cluster, AFK shown with muted styling). Hover for tooltips listing duration, dominant apps/domains/titles, and shared ratios.

## Focus Dashboard (`analysis.html`)
- Loads the same `timeline_data.js` dataset and classifies each minute into activity buckets (Sinex/Sinnix development, LLM interaction, research, media, social, adult content, etc.) via heuristic domain/app/title rules (see inline `CATEGORY_RULES`).
- Provides range selectors, summary cards (focused hours, active days, top category share, idle time), category table with top contexts, and a 30-day recent breakdown with stacked bars.
- Offers one-click CSV export (`Download CSV`) containing per-day hours for every category plus focused/idle totals, suitable for further analysis in DuckDB or spreadsheets.
- Extend mappings by editing `CATEGORY_META`, keyword/domain lists, and `CATEGORY_RULES` inside `analysis.html`.

## Artefact Notes
- The dataset currently spans 445 725 minutes (2024-10-14 → 2025-10-28) across 24 clusters.
- Embeddings use 8 dimensions (TruncatedSVD) scaled by `embedding_scale=1000`; values are stored as integers for space efficiency.
- Lookups (`hosts`, `apps`, `titles`, `domains`, `projects`, `files`, `sources`) keep repeating strings deduplicated.
- Shared blocks store per-cluster `share` ratios; the viewer re-normalises to ensure gradients fill 100 % even after slicing to `maxActivities`.

## Next Improvements
- Re-run after ActivityWatch schema changes or when new buckets are added (update `determine_category` in `build_timeline.py` if watcher names change).
- Parameterise cluster count in the CLI (currently adaptive based on sample size).
- Integrate optional embeddings export (e.g., Parquet) for downstream Sinevec ingestion.
- Hook results into the baseline pipeline once minute-level views are required in dashboards.
