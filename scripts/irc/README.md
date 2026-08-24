# IRC ad-hoc analysis scripts (imported from the lake, 2026-08-24)

These came verbatim from `/realm/data/activity/irc/scripts/` — ad-hoc tooling
that lived inside the data lake, against the rule that data dirs hold data and
repos hold tools. `seal_logs.py` is NOT here: it is the live nightly sealer and
moved into sinnix (`modules/services/weechat-log-sealer/`).

Their outputs (`_processed/` concat/extract products) were superseded by
`lynchpin.sources.irc_raw` + `lynchpin.ingest.irc_materialize`, which read raw
sealed logs directly. The scripts are retained as reference until the owning
bead re-implements what is still wanted as real lynchpin functionality; then
this directory is deleted.
