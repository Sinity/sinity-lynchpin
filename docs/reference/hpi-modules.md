# HPI Modules to Track

This list captures the upstream `my.*` modules we want to keep available and/or port. It is curated from the Codex session log and should stay in sync with `docs/plans/lynchpin-hpi.md`. Vendored sources live under `external/` and are configured via `config/my/config.py` (`MY_CONFIG=$PWD/config`). See `docs/reference/lynchpin-module-map.md` for the detailed mapping between `lynchpin.*` and upstream HPI modules.

## Core HPI modules (priority set)
- `my.coding.commits`
- `my.calendar.holidays`
- `my.body.weight`
- `my.body.exercise.all`
- `my.fbmessenger`
- `my.github.all`
- `my.github.gdpr`
- `my.github.ghexport`
- `my.lastfm`
- `my.location.google`
- `my.photos.main`
- `my.reddit`
- `my.smscalls`
- `my.twitter.all`
- `my.twitter.archive`
- `my.twitter.twint`
- `my.sleep.manual`
- `my.money`
- `my.webhistory`
- `my.browser`
- `my.google.takeout.parser`
- `my.goodreads`
- `my.spotify.gdpr` (purarue fork)

## External forks / variants
- `my.activitywatch`
- `my.taskwarrior`
- `my.activitywatch.active_window`
- `my.linkedin.privacy_export`
- `my.steam.scraper`
- `my.zsh`
- `my.bash`
- `my.atuin`

## Local fills (sinity)
These are currently implemented in `external/hpi-sinity` because upstream HPI lacks them.
- `my.money`
- `my.webhistory`
- `my.sleep.manual`
- `my.atuin`
