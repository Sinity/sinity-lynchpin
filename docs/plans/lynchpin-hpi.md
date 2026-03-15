# Lynchpin HPI Integration

Goal: keep a narrow HPI surface that reflects only the modules with stable local inputs and active local use.

## Supported modules

These vendored modules are the full HPI contract for this repo:

| Module | Notes |
| --- | --- |
| `my.coding.commits` | Raw commit feed over local repos. |
| `my.calendar.holidays` | Holiday metadata overlays. |
| `my.fbmessenger` | Messenger chat surface over the processed `fbmessengerexport` SQLite export. |
| `my.smscalls` | SMS/call export tree. |
| `my.sleep.manual` | Adapter over the merged sleep JSONL already used by Lynchpin. |
| `my.money` | Adapter over the local ledger journal. |
| `my.webhistory` | Adapter over the canonical merged webhistory NDJSON. |
| `my.browser` | Browserexport-compatible view over filtered Gestalt raw exports plus the live Chrome profile DB. |
| `my.google.takeout.parser` | Secondary parser surface over the local Takeout archive set. |
| `my.goodreads` | Goodreads export support where the expected export exists. |
| `my.spotify.gdpr` | Secondary parser over Spotify GDPR exports. |
| `my.activitywatch`, `my.activitywatch.active_window` | ActivityWatch companion surface. |
| `my.atuin` | HPI-style view over the same Atuin DB Lynchpin reads directly. |

## Operating rule

- Vendored HPI snapshots stay under `external/`, but only the modules above are part of the repo’s operational contract.
- `config/my/config.py` should only carry paths for the supported set.
- `python -m lynchpin.system.validate hpi` validates only the supported set or a subset selected via `--module`.
- New HPI modules do not get added speculatively. They join the contract only when they already have stable local inputs and a concrete Lynchpin use.

## Current shape

- Browser and Messenger are now both first-class HPI surfaces, not side experiments.
- Browser support is intentionally dual-source: filtered Gestalt raw exports plus the live Chrome `History` DB.
- The rest of Lynchpin should keep treating `lynchpin.*` as the main API and the supported `my.*` modules as narrow companion sources.
