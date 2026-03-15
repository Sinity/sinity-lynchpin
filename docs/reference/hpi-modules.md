# HPI Modules

Vendored `my.*` modules live under `external/` and are configured via [config.py](/realm/project/sinity-lynchpin/config/my/config.py) (`MY_CONFIG=$PWD/config`). This repo supports only the modules below.

## Supported Set

These modules are configured, documented, and covered by the default `python -m lynchpin.system.validate hpi --quick` run:

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

## Local Notes

- `my.fbmessenger` reads the processed `fbmessengerexport.sqlite` at `/realm/data/exports/comms/facebook-messenger/processed/fbmessengerexport.sqlite`.
- `my.browser` combines filtered Gestalt raw exports under `/realm/data/captures/webhistory/gestalt/raw/*.json|*.csv` with the live Chrome history DB at `~/.config/google-chrome/Default/History`.
- `my.webhistory` remains the canonical HPI view over the merged NDJSON history at `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson`.

## Validation

```bash
python -m lynchpin.system.validate hpi --quick
python -m lynchpin.system.validate hpi --module my.browser --no-quick
```

Use `--module` only for subsets of the supported set above.
