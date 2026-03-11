# Calendar Views & Daily Markdown Blueprint

## 1. Goals
- Produce a **single canonical calendar surface** that captures “what happened” per day across ActivityWatch, Atuin, git, chats, wearable metrics, life events, and instrumentation.
- Generate **Markdown dossiers per day** (and week, month, quarter, year) that assistants can diff, edit, or quote in narratives.
- Render **clean static webpages** (day/week/month dashboards) with minimal dependencies, optimized for local reading (mobile + desktop) and progressive drill-downs.
- Reuse existing data feeds (baseline rollups, life timeline JSON, focus metrics, instrumentation metadata) so no new manual inputs are required.

## 2. Source Inventory

| Domain | Source | Frequency | Notes |
| --- | --- | --- | --- |
| Focus windows | `artefacts/core/baseline/latest/activity_timeline.json`, ActivityWatch DB | per minute/day | Already AFK-adjusted; calendar dossiers reuse them directly |
| Shell history | `~/.local/share/atuin/history.db` (ingested into baseline) | per command/day | Provides counts, topics, durations |
| Git deltas | Repos listed in baseline (sinex, sinnix, polylogue, etc.) | per commit/day | Already aggregated in baseline JSON |
| Chat sessions | `docs/reference/sessions`, Polylogue Markdown exports, `artefacts/knowledge/sessions/summaries` | per conversation/day | Need per-day mapping via timestamps |
| Life timeline | `artefacts/lifelog/life-timeline/monthly_life_latest.json` | per month/day subset | Contains high-sensitivity events (web, finance, health) |
| Wearables | `/realm/data/exports/health/processed/sleep_merged.jsonl`, `sleep_summary.json` | per night/day | Expand to steps/hr/stress later |
| Instrumentation | `artefacts/ingest/instrumentation/*_metadata.jsonl` | per file/day | indicates recordings, audio capture, screen grabs |
| Narratives/logs | `docs/analysis-log.md`, `docs/analysis/*` | per entry | Provide qualitative anchors |

## 3. Target Outputs

### 3.1 Daily Markdown (`artefacts/calendar/days/YYYY-MM-DD.md`)
Suggested sections (auto-generated with placeholders for manual notes):
1. **Overview block**
   - Date, weekday, ISO week number, day-of-year.
   - Focus span (hrs active), AFK ratio, “top contexts” (apps/web domains) with minutes.
2. **Timeline Highlights**
   - Morning/Afternoon/Evening summary bullet derived from ActivityWatch clusters.
   - Git commits grouped by repo (counts + net LOC).
   - Shell hotspots (top commands, interactive sessions, long-running commands).
3. **Knowledge & Chats**
   - List of chat sessions (Codex, Claude, etc.) with short summary references (link to `docs/reference/sessions/...`).
   - Notebook/journal mentions (if matching sections from `docs/analysis-log.md` or new logs).
4. **Life & Health**
   - Sleep metrics (start, end, duration, quality score).
   - Steps / heart rate / stress (when available).
   - Life timeline events (finance totals, top web intents, social/media spikes).
5. **Instrumentation**
   - Captures recorded (asciinema sessions, audio, screen). Link to metadata file or viewer.
6. **Artefacts & TODOs**
   - Derived outcomes (new dashboards, digests).
   - “Follow-ups” placeholder for manual notes.

Markdown front-matter (YAML) should include canonical metadata (range, run ID, upstream manifest) to support programmatic parsing.

### 3.2 Weekly / Monthly / Quarterly / Annual Markdown
- Weekly: aggregate seven daily files into `artefacts/calendar/weeks/2025-W42.md`. Include:
  - Sparkline of focus hours per day.
  - Top projects, command categories, chat minutes, health trends.
- Monthly & above: pull from life timeline JSON + aggregated daily stats. Provide sections for:
  - Focus distribution (coding/research/comms/maintenance).
  - Output volume (commits, docs, artefacts).
  - Recovery metrics (sleep averages, stress).
  - Notable conversations (link to sessions).
  - Financial/consumption highlights (from life timeline).

### 3.3 Static Web Experience
- Directory: `artefacts/calendar/site/`.
- **Calendar landing page** with heatmap (month grid) showing focus hours + quick status icons (sleep ok, chats, instrumentation). Click opens the daily page.
- **Daily page** renders Markdown (via static pre-render to HTML). Provide horizontal timeline chart, stacked bar for focus categories, cards for git/chat/wearable stats.
- **Weekly/Monthly dashboards** with responsive layout:
  - Clean typography (Inter/Space Grotesk), light/dark theme toggle, minimal color palette (neutral background, accent color for focus).
  - Use vanilla JS (htmx or Alpine.js) to keep dependencies small; fallback to pure HTML for offline use.
- Provide navigation breadcrumbs (Year > Month > Day) and keyboard shortcuts (`←/→` to move days).

## 4. Pipeline Design

### 4.1 Calendar Aggregator (`lynchpin.views.calendar_views`)
Steps:
1. **Load baseline artefacts** (`activity_timeline.json`, `baseline_summary.json`) for focus/git data.
2. **Query Atuin, git, chat summaries** directly or via new DuckDB views (see pipeline-unification doc).
3. **Join life timeline & wearable datasets**: map events to days using local timezone.
4. **Ingest instrumentation metadata** to tag days with recordings.
5. **Write normalized per-day rows** to `artefacts/calendar/calendar.duckdb` (tables `days`, `weeks`, `months` etc.) plus JSON exports for static site.

### 4.2 Markdown Rendering
- Use Jinja2 templates stored alongside `lynchpin.views.calendar_views` (or a dedicated prompts/templates folder).
- For each timescale, render Markdown with front-matter:
  ```yaml
  ---
  date: 2025-10-24
  iso_week: 2025-W43
  run_id: 2025-10-25T03-41-00Z
  sources:
    baseline: artefacts/core/baseline/latest/activity_timeline.json
    life_timeline: artefacts/lifelog/life-timeline/monthly_life_latest.json
    instrumentation: artefacts/ingest/instrumentation/terminal_sessions.jsonl
  ---
  ```
- Body uses consistent headings (`## Overview`, `## Timeline`, `## Work`, `## Chats`, `## Life & Health`, `## Captures`, `## Notes`).

### 4.3 Static Site
- Build script generates:
  - `index.html` (calendar grid + filters).
  - `day/YYYY/MM/DD/index.html` (rendered from Markdown + charts).
  - `week/YYYY/W##/index.html`, etc.
- Data pipeline exports JSON for charts (e.g., `day_data/2025-10-24.json`).
- Charting: prefer lightweight libs (Charts.css or tiny D3 subset). Provide CSS modules to keep look clean.

### 4.4 Linking Markdown ↔ Web
- Each Markdown file includes a footer linking to the HTML version.
- HTML pages link back to Markdown (for editing) and to source data (baseline run, life timeline, etc.).

## 5. Implementation Phases

1. **Scaffold & Proof of Concept**
- Create/extend `lynchpin.views.calendar_views` with README, sample template, CLI (`python -m lynchpin.views.calendar_views`).
   - Hard-code a single day to ensure formatting + data blending works.
2. **Daily Batch**
   - Implement data aggregation for ActivityWatch + Atuin + git (baseline derivatives).
   - Produce per-day Markdown + HTML (without life timeline yet) for a short range (e.g., past 7 days).
3. **Weekly/Monthly Aggregates**
   - Add week/month generation, referencing existing daily files to avoid duplicate logic.
   - Introduce sparkline visualizations + summary cards.
4. **Life/Wearable Integration**
   - Pull life timeline JSON + sleep summaries; map to day structures.
   - Display health widgets (sleep gauge, step counts).
5. **Instrumentation & Chat Enhancements**
   - Include recording counts, direct links to metadata.
   - Link chat sessions via `docs/reference/sessions` or Polylogue Markdown.
6. **Polish & Theming**
   - Refine CSS, add dark mode, implement keyboard navigation + search.
   - Add “export to PDF” / print-friendly styles.
7. **Automation & Verification**
   - Introduce `just calendar-refresh start=... end=...` target (or integrate into `just focus`).
   - Add regression tests verifying Markdown output schema (front-matter fields, sections).

## 6. Design Considerations

- **Privacy boundary**: keep outputs under `artefacts/calendar/` (ignored by Git). Only summaries or sanitized snippets enter `docs/personal/`.
- **Accessibility**: ensure HTML pages are readable with screen readers; use semantic headings, ARIA labels for charts.
- **Performance**: pre-render charts server-side when possible (SVG). Avoid heavy JS so calendar loads fast even for large ranges.
- **Manual annotations**: leave dedicated “Notes” sections in Markdown so assistants can append observations without re-running the pipeline (perhaps load extra `.md` overlays on render).
- **Extensibility**: treat day/week/month as view layers; upstream data should live in normalized DuckDB tables so future features (e.g., search, anomaly detection) can plug in easily.

## 7. Open Questions
- Preferred timezone (local vs UTC) for day boundaries? Baseline currently mixes; need consistent policy (likely system local with override flag).
- How to integrate high-sensitivity finance/health data into web output? Options: obfuscate, show only relative measures, or keep those sections Markdown-only.
- Should HTML site mimic Polylogue aesthetic or adopt a purely functional style? For now, prioritize minimalist “journal dashboard” look; revisit after first prototype.

## 8. Immediate Next Steps
1. Draft schema for aggregated calendar tables (fields per day/week/month).
2. Build sample Markdown + HTML for a single day using existing baseline data to validate layout.
3. Update `docs/reference/repo-organization.md` once the calendar pipeline exists, so assistants know where to find per-day dossiers.

## 9. Narrativization & LLM Story Engine

To convert the historical calendar data into coherent self-stories:

1. **Data pack assembly**
   - For any requested range (day/week/month/year), bundle the relevant Markdown files + JSON extracts into a single prompt-ready package.
   - Include structured stats (focus minutes, AFK ratio, git deltas, chat summaries, sleep metrics), notable artefacts, and instrumentation notes.
   - Provide short extracts of key chats or diary entries to supply qualitative color.
2. **LLM prompt template**
   - Define templates in `lynchpin.views.calendar_narratives.py` (or extract them into a dedicated doc) with slots for metrics and highlights.
   - Emphasize chronology (morning/afternoon/evening) and cross-domain insights (e.g., “coding spike followed by poor sleep”).
   - Offer stylistic modes (concise recap, reflective essay, actionable retro).
3. **Generation workflow**
   - Add a CLI (`just calendar-narrative 2025-10-01 2025-10-07 mode=reflective`) that:
     1. Ensures calendar Markdown/Web data is up to date for the range.
     2. Builds the prompt payload (Markdown → structured sections).
     3. Calls the configured LLM (OpenAI/Claude, etc.) to produce narrative Markdown, saved under `artefacts/calendar/narratives/`.
   - Store metadata (model, temperature, source files) alongside outputs for reproducibility.
4. **Human-in-the-loop editing**
   - Optionally open the generated Markdown in an editor or append it to `docs/analysis/` with TODO markers for manual refinement.
   - Encourage annotations back into daily files so future runs inherit curated context.

This pipeline keeps the historical view as the primary artifact while giving the LLM a curated, structured dataset to produce high-quality personal narratives.

## 10. Data Utilization & Derivative Processing Roadmap

1. **Full-scope ingestion parity**
   - Ensure the calendar aggregator consumes every baseline artefact: ActivityWatch windows, AFK spans, Atuin categories, git numstat, Codex daily counts, sleep summary, instrumentation metadata, and future wearable exports (steps, HR, stress). Done: sleep merge JSONL now flows into daily HTML/Markdown with Health & Recovery blocks plus raw `health/*.jsonl` in each bundle.
   - Pull life timeline overlays (monthly JSON) to surface intake/work/finance highlights at the month level.
   - Track chat sessions via the session ledger (`docs/reference/sessions`, `artefacts/knowledge/sessions/summaries`) so daily dossiers can cite major conversations. Done: raw bundles now hoover Codex/Claude/Polylogue exports per day and JSON payloads list the sources for prompt assembly.
2. **Derived data layers**
   - Compute proactive aggregates: focus buckets (coding/research/comms/maintenance), AFK-adjusted productivity scores, command density metrics, git churn variance, instrumentation coverage. (Daily/weekly/monthly dossiers now include heuristic ActivityWatch categories + AFK totals and repo churn tables.)
   - Pre-generate derivative artefacts (e.g., weekly heatmaps, repo rank deltas, chat cadence plots) so downstream prompts and dashboards can simply read JSON instead of recomputing.
   - Store normalized tables in a lightweight DuckDB file (`artefacts/calendar/calendar.duckdb`) with views for day/week/month to support future analytics and anomaly detection.
3. **Prompt-ready packs**
   - For any range, export a single JSON/Markdown bundle containing:
     * Key metrics.
     * Highlight sentences (auto-generated).
     * Links to artefacts (dashboards, narratives, instrumentation).
     * Manifest of raw bundles (`artefacts/calendar/raw/YYYY-MM-DD/`) pointing to ActivityWatch events, Atuin commands, git diffs, webhistory entries, session transcripts, and instrumentation metadata so orchestration layers can stream the full day when model context allows.
   - Include embeddings or tags referencing life events so LLMs can connect personal context with technical activity.
4. **Future data hooks**
   - Wearables: integrate steps, HR, stress once available; store per-day rollups with qualitative tags (“rested”, “overstressed”).
   - Instrumentation: parse asciinema/audio metadata for program names or durations to indicate meeting/coding sessions.
   - Chat transcripts: attach a short summary + link per major discussion to the daily/weekly dossier and prompt pack.

## 11. Narrative Modes & Outcome Targets

Define multiple narrative templates so different consumers get tailored outputs:

| Mode | Purpose | Style cues |
| --- | --- | --- |
| `reflective` | Personal retrospective | Emotive, causal, highlights lessons/tools |
| `executive` | Stakeholder update | Bullet-heavy, outcomes, blockers, metrics |
| `playful` | Light journaling | Conversational tone, metaphors, highlight contrasts |
| `retro` | Retrospective for process | “What worked / what didn't / experiments” framing |
| `tactical` | Action planning | Focus on follow-ups, TODOs, and risk flags |

Implementation path:
1. Extend `lynchpin.views.calendar_narratives` to load template snippets (prompt fragments and expected structure) from a dedicated prompts directory.
2. For each mode, define required sections (e.g., `## Context`, `## Output`, `## Recovery`, `## Next steps`) and specify how data fields feed the LLM (e.g., highlight top repos for executive, sleep stats for reflective).
3. Add CLI flags to request multiple outputs per range, e.g., `just calendar-narrative 2025-12-01 2025-12-31 mode=executive` and `... mode=playful`.
4. Store generated narratives separately (`artefacts/calendar/narratives/<mode>/...`) and expose them via the static site so assistants can quickly browse different perspectives.

Proactive goal: every range refresh automatically triggers generation of all relevant narrative modes plus derivative metrics, so future agents always start with a rich context pack rather than re-running pipelines manually.
