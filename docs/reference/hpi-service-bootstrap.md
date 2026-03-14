# HPI Service Bootstrap

This runbook covers the next sensible vendored HPI activations after the current curated set: GitHub, Twitter/X, LinkedIn, and Steam.

## Current state

As of 2026-03-14:

- `python -m lynchpin.system.validate hpi --module my.github.all --module my.twitter.all --module my.linkedin.privacy_export --module my.steam.scraper --quick` returns `empty` for all four surfaces because no canonical exports are populated yet.
- The local Chrome profile at `~/.config/google-chrome/Default/Cookies` does already contain cookies for `github.com`, `x.com` / `twitter.com`, `linkedin.com`, and `steamcommunity.com`.

That means authenticated collection is plausible, but the vendored HPI modules do not consume Chrome cookies directly. They expect exported files or scraper outputs.

## Canonical target paths

| Service | HPI module(s) | Expected local input | Canonical target path | Config env |
| --- | --- | --- | --- | --- |
| GitHub | `my.github.gdpr`, `my.github.ghexport`, `my.github.all` | GitHub GDPR export and/or `ghexport` JSON dump | `/realm/data/exports/github/gdpr/` and `/realm/data/exports/github/ghexport/` | `HPI_GITHUB_GDPR`, `HPI_GITHUB_EXPORT` |
| Twitter/X | `my.twitter.archive`, `my.twitter.twint`, `my.twitter.all` | Official archive zip/unpacked export and/or Twint SQLite DB | `/realm/data/exports/twitter/archive/` and `/realm/data/exports/twitter/twint/` | `HPI_TWITTER_ARCHIVE`, `HPI_TWINT_EXPORT` |
| LinkedIn | `my.linkedin.privacy_export` | LinkedIn privacy export zip or unpacked directory | `/realm/data/exports/linkedin/privacy-export/` | `HPI_LINKEDIN_GDPR` |
| Steam | `my.steam.scraper` | `steamscraper` JSON export | `/realm/data/exports/steam/steamscraper/` | `HPI_STEAM_EXPORT` |

## What to do next

1. Export or scrape the service data into the canonical path above.
2. If you use a different path, set the matching `HPI_*` env var before validation.
3. Validate only the new surface first:

```bash
python -m lynchpin.system.validate hpi --module my.github.all --quick
python -m lynchpin.system.validate hpi --module my.twitter.all --quick
python -m lynchpin.system.validate hpi --module my.linkedin.privacy_export --quick
python -m lynchpin.system.validate hpi --module my.steam.scraper --quick
```

4. Promote a service into the active HPI contract only after it has stable inputs and a concrete Lynchpin consumer or operator workflow.

## Chrome profile note

The Chrome profile is useful for future custom connectors and for export tooling that can reuse logged-in sessions. It is not, by itself, enough to activate the vendored HPI modules above.

If you want a future agent pass to build direct authenticated connectors, the useful local starting points are:

- `~/.config/google-chrome/Default/Cookies`
- `~/.config/google-chrome/Default/Local Storage/`
- `~/.config/google-chrome/Default/IndexedDB/`

For Messenger, Lynchpin already has a concrete Chrome-cookie path via [`fbmessenger_export.py`](/realm/project/sinity-lynchpin/lynchpin/ingest/fbmessenger_export.py). The other service modules do not yet have an equivalent repo-local connector.
