# HPI Modules

Vendored `my.*` modules live under `external/` and are configured via `config/my/config.py` (`MY_CONFIG=$PWD/config`). The repo does not treat every vendored module as part of the supported contract.

## Active Supported Set

These are the `my.*` modules that are configured for this environment, documented, and covered by the default `python -m lynchpin.system.validate hpi --quick` run:

- `my.coding.commits`
- `my.calendar.holidays`
- `my.fbmessenger`
- `my.smscalls`
- `my.sleep.manual`
- `my.money`
- `my.webhistory`
- `my.browser`
- `my.google.takeout.parser`
- `my.goodreads`
- `my.spotify.gdpr`
- `my.activitywatch`
- `my.activitywatch.active_window`
- `my.atuin`

Locally populated in that active set:

- `my.atuin`
- `my.browser`
- `my.fbmessenger`
- `my.money`
- `my.sleep.manual`
- `my.webhistory`

Notes:

- `my.fbmessenger` is active because the processed `fbmessengerexport.sqlite` now exists at the canonical Messenger export path.
- `my.browser` is active through two concrete local feeds: filtered Gestalt browserexport files under `/realm/data/captures/webhistory/gestalt/raw/*.json|*.csv` and the live Chrome history database at `~/.config/google-chrome/Default/History`.

## Dormant Vendored Modules

These remain vendored for possible future use, but they are not part of the default supported/validated surface:

- `my.body.weight`
- `my.body.exercise.all`
- `my.github.all`
- `my.github.gdpr`
- `my.github.ghexport`
- `my.lastfm`
- `my.linkedin.privacy_export`
- `my.location.google`
- `my.photos.main`
- `my.reddit`
- `my.steam.scraper`
- `my.taskwarrior`
- `my.twitter.all`
- `my.twitter.archive`
- `my.twitter.twint`
- `my.zsh`
- `my.bash`

Move a dormant module back into the active set only when there is both:

1. A stable canonical local input for it.
2. A concrete Lynchpin consumer or runbook that depends on it.

The next sensible dormant candidates are `my.github.*`, `my.twitter.*`, `my.linkedin.privacy_export`, and `my.steam.scraper`, but they still need exported inputs. See [hpi-service-bootstrap.md](/realm/project/sinity-lynchpin/docs/reference/hpi-service-bootstrap.md) for the exact bootstrap paths and the current Chrome-profile/auth limits.

## Validation

```bash
python -m lynchpin.system.validate hpi --quick
python -m lynchpin.system.validate hpi --profile all --quick
```

Use the default `active` profile for the real supported contract. Use `--profile all` only to audit the full vendored inventory.
