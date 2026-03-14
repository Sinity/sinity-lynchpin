# Wykop Export

## Status

stable

## Purpose

Export Wykop account activity into canonical JSON and JSONL files under `/realm/data/exports/wykop/raw/<username>/` so downstream readers can consume them reproducibly.

## Canonical Command

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.ingest.wykop_export --backend api
```

Useful variants:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.ingest.wykop_export --backend api --max-pages 200 --delay-seconds 0.1

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.ingest.wykop_export --backend api --collection znaleziska_komentowane
```

## Outputs

Primary exports land under `/realm/data/exports/wykop/raw/<username>/`:

- public profile streams such as commented links, authored entries, entry comments, and voted links,
- authenticated extras such as profile metadata, notifications, observed feeds, and private-message dumps,
- `scrape_state.json` for resumable state,
- `scrape_manifest.json` for run metadata.

## Auth Discovery

For `--backend api`, the command tries:

1. `--refresh-token`
2. `scrape_state.json`
3. browser profile Local Storage discovery for `userKeep`

If discovery fails, pass `--refresh-token` explicitly.

## Downstream Consumers

- `lynchpin.system.life_timeline`
- `lynchpin.sources.exports.wykop`

## Notes

- `--backend html` is public-only and stops at the prerender cutoff.
- `--backend api` can fetch deeper history and authenticated extras.
- When scraping another user while authenticated, self-only endpoints are skipped automatically.
