# Input activity reconstruction

`python -m lynchpin.analysis input-activity` reads owner-native keylog day files and raw ActivityWatch foreground records over explicit timezone-aware bounds. It does not refresh a substrate or repair capture services. The command writes a new private JSON file with mode `0600` and refuses to overwrite existing paths. Foreground titles and event references are private even when text reconstruction is disabled.

```sh
python -m lynchpin.analysis input-activity \
  --start 2026-04-02T10:00:00+00:00 --end 2026-04-02T11:00:00+00:00 \
  --out /private/derived/input-evidence.json
```

Use the declared `input_activity` AgentCTL operation for resource-heavy windows, passing the same arguments after `--`. `--logs-root` selects a directory of UTC-named JSONL day files; `--aw-db` and `--bucket` select foreground input. Multiple foreground buckets require an explicit choice. `--contexts` instead accepts a JSON list of `id`, `start`, `end`, `app`, `title`, `source_ref` and optional `label`. Contexts must be non-overlapping and within the requested bounds.

## What the durations mean

The source reader retains keyboard presses, pointer buttons and wheel records, including `pointer_rel` wheel codes. Pointer X/Y motion is counted in source audit metadata but excluded from input accounting. Wheel record counts are not gesture counts: low- and high-resolution events can describe the same gesture.

Input bouts run from the first to the last input with at most 30 seconds between adjacent records. The two-second burst measure sums only the gaps at or below two seconds. Neither measure is physical key-down time or attention. A single isolated input has zero bout duration.

Gaps between bouts up to five minutes are listed separately as possible reading, thinking, waiting or other activity. They require input on both sides in one uninterrupted foreground context, with no observed same-device capture restart. Longer silence and focus changes are barriers. Continuous capture cannot be established from event-only files; missing days, malformed lines, read-time changes and file hashes are recorded separately. Adjacent foreground heartbeats with unchanged app/title are coalesced; a focus-change record is a barrier even if its duration is zero.

One- and five-minute input-recency windows are sensitivity measures clipped to the same foreground context, not extra time added to bouts. Input without foreground coverage remains counted but unattributed. Unmeasured time is not inactivity.

`--reported-periods` accepts a JSON list with `start`, `end`, `source_ref` and optional descriptive fields. Its clipped interval union and overlap with input bouts remain separate from input totals. This permits a person's account of task dedication, including quiet thought or waiting, to coexist with the instrumented evidence without double counting.

## Optional text and message matching

`--include-text` adds lossy physical-key candidates with character timestamps and file/line references. The current reconstruction assumes a US layout and lowercase letters. Missing keyboard releases prevent reliable modifier reconstruction. Backspace assumes one character; navigation, shortcut, paste, modifier, click and unknown-key boundaries split fragments. A physical space with recorder `changed=false` remains a flagged candidate unless a shortcut modifier is known: this flag describes the recorder buffer, not application contents. These candidates are not final application text. Clipboard contents are never reconstructed.

`--anchors` accepts a JSON list of known human messages with `id`, `timestamp`, `source_ref` and `text`. The matcher looks back at most 20 minutes and requires a contiguous match of at least 24 normalized alphanumeric characters. Normalization ignores case, punctuation and common diacritics. Matches identify only the corresponding input substring, preserve repeated-match ambiguity, and do not assign a whole foreground span or agent runtime to that message. Anchor matching does not include reconstructed text in the output unless `--include-text` is also set.

The artifact schema is `lynchpin.input-activity.v1`. Raw file audits, policy thresholds, context intervals, bout endpoints and message references supply the provenance needed to reproduce or challenge an interpretation. No analytical inference is written back to source data.
