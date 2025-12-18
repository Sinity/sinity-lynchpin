# Wykop Export Pipeline

## Purpose
Export all Wykop activity relevant to the `Sinity` account into canonical JSONL files under `/realm/data/`, so other pipelines (notably the life timeline) can consume them reproducibly.

This is intentionally **high-sensitivity** (it captures your writing and interactions) and should be treated as local-only.

## Output (canonical location)
`/realm/data/personal-data/my_external_exports/wykop/Sinity/`

Files (JSONL):
- `wykop_links_commented.jsonl` – comments you wrote under links (includes link metadata + tags).
- `wykop_entries_added.jsonl` – authored microblog entries (wpisy).
- `wykop_entry_comments.jsonl` – comments you wrote under microblog entries.
- `wykop_links_wykopane.jsonl` – links you upvoted (“wykopane”).
- `wykop_entries_plusowane.jsonl` – microblog entries you upvoted (“plusowane”).
- `wykop_links_added.jsonl` / `wykop_links_opublikowane.jsonl` / `wykop_links_powiazane.jsonl` – smaller profile streams.

Additional authenticated exports (API “extras”, high-sensitivity):
- Account/profile: `wykop_profile.json`, `wykop_profile_self.json`, `wykop_profile_short.json`, `wykop_badges.json`, `wykop_actions.jsonl`
- Social graph: `wykop_followers.json`, `wykop_following.json`
- Settings: `wykop_settings_*.json` (+ blacklist JSONLs)
- Notifications: `wykop_notifications_*.jsonl`, `wykop_notification_groups.jsonl`, `wykop_notifications_status.json`
- Private messages: `wykop_pm_conversations.json`, plus per-thread dumps under `pm/`
- Observed feeds (can be large): `wykop_observed_*.jsonl`

State/manifest:
- `scrape_state.json` – resumable checkpoint + auth refresh token cache (local-only).
- `scrape_manifest.json` – last run metadata (pages, items written, etc.).

## Usage
Run inside the sinity-analysis devshell:
```bash
direnv exec /realm/project/sinity-analysis \
  bash -lc 'cd /realm/project/sinity-analysis && python scripts/scrape_wykop.py --backend api'
```

If you’re exploring the heavier endpoints (notifications/observed feeds) and want a bounded run, cap pages:
```bash
direnv exec /realm/project/sinity-analysis \
  bash -lc "cd /realm/project/sinity-analysis && python scripts/scrape_wykop.py --backend api --max-pages 200 --delay-seconds 0.1"
```

To scrape only one stream:
```bash
direnv exec /realm/project/sinity-analysis \
  bash -lc 'cd /realm/project/sinity-analysis && python scripts/scrape_wykop.py --backend api --collection znaleziska_komentowane'
```

## Backends (HTML vs API)
- `--backend html`: uses public HTML prerender pages. This **stops around page ~49** because deeper pages render as a JS app shell (no content in HTML).
- `--backend api`: uses Wykop’s authenticated API and can fetch the full history (hundreds of pages).
- `--backend auto` (default): uses API if auth is available, otherwise falls back to HTML.
- API extras can be disabled with `--no-extras` (useful if you only want the public profile streams).

### Auth discovery
For `--backend api`, the script tries, in order:
1. `--refresh-token` (a `localStorage.userKeep` value)
2. `scrape_state.json` (`auth.refresh_token`)
3. Chrome/Chromium Local Storage LevelDB (auto-extracts `userKeep` when present)

If auto-discovery fails, pass `--refresh-token` explicitly.

## Downstream consumers
- `scripts/build_life_timeline.py` reads:
  - `/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_links_commented.jsonl`
  - `/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_entries_added.jsonl`
  - `/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_entry_comments.jsonl`

Rebuild the 2020-04 → 2023-04 life timeline artefacts with:
```bash
direnv exec /realm/project/sinity-analysis just life-timeline
```
