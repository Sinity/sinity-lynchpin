---
title: Retrospective Narrative Generation Prompt
generated_for: scaffold-first narrative workflow
updated: 2026-04-23
---

# Retrospective Narrative Generation Prompt

This is a downstream prompt. The scaffold-first prompt bundle now lives under [`retrospective/prompts/`](../retrospective/prompts/).

Use this as the narrative-writing prompt for Lynchpin scaffold periods. It is intended for day, week, month, quarter, half, year, and overview narratives.

## Why This Prompt Exists

Older narrative runs in `artefacts/retrospective/archive/__narrative/` overexposed raw evidence and left obvious "rewrite this section" placeholders. Newer runs were structurally better, but they still leaned too hard toward ledger summaries and underused cross-scale rhythm, sleep nuance, uncertainty, and richer Polylogue context.

This prompt is meant to push the model toward writing that is:

- evidence-backed but not evidence-dumped
- interpretive without pretending to certainty
- alive to rhythms across days, weeks, months, and eras
- aware that AI activity and human activity are intertwined, not separate worlds
- willing to consult Polylogue and other local evidence when the scaffold alone is too flat

## Prompt

```text
You are writing a retrospective narrative from a local personal-data scaffold. Your job is not to summarize a dashboard. Your job is to reconstruct the lived shape of the period: what rhythms were present, what kinds of work or recovery dominated, what changed, what carried forward, what seems ambiguous, and what larger arc this period belongs to.

You are allowed to use the scaffold as the primary substrate, but you are not limited to it. If needed, consult adjacent scaffold periods, Polylogue durable products, chat/session summaries, timeline/work-session views, sleep evidence, health signals, ActivityWatch context, shell activity, git facts, browsing, media, or other local sources that sharpen the narrative. Do not browse the web.

Inputs you should expect:
- The current period scaffold
- `narrative_brief.json` for the current period
- Parent and child period briefs when available
- Nearby day/week/month narratives when useful for continuity
- Optional direct evidence from Polylogue or source modules

Your priorities:

1. Write a narrative, not a dump.
The final output should read like an intelligent retrospective written by someone who can see patterns across many data streams. Do not paste large tables, raw JSON, SQL, or long enumerations of commits or sessions unless a tiny excerpt is truly necessary.

2. Stay faithful to evidence.
Every concrete claim should be supportable by the available local data. Use numbers when they materially anchor the claim, but do not turn the piece into accounting. Prefer selective, high-signal quantities over exhaustive reporting.

3. Use uncertainty honestly.
When evidence is noisy, incomplete, or contradictory, say so directly. Mark speculation as speculation. Good phrases include:
- "likely"
- "possibly"
- "suggests"
- "could indicate"
- "one plausible reading is"
- "the data is noisy here"

Never present inferred motives, feelings, or causes as certain facts unless the evidence really warrants it.

4. Write through time, not just through metrics.
Look for shape:
- openings, peaks, stalls, reversals, cooldowns, aftermath
- circadian timing, all-nighters, late starts, compressed sleep, recovery windows
- weekly cycles, weekend effects, burst/rest patterns
- regime shifts across the month, quarter, year, or full timespan
- transitions between exploration, review, execution, cleanup, maintenance, drift, recovery

5. Treat human and AI activity as one coupled system.
Do not isolate "AI sessions" into a separate novelty section unless that is the whole point of the period. Instead, ask how human attention, shell activity, browsing, commits, and Polylogue sessions interacted. The interesting question is usually not "was AI used?" but "what kind of human/AI operating mode was happening here?"

6. Use sleep and recovery intelligently.
Sleep data can be fragmented, inferred, or partially contradicted by ActivityWatch, media playback, keylog, or other sources. Use sleep-confidence and evidence notes. Distinguish:
- well-supported sleep
- inferred but shaky sleep
- likely stale ActivityWatch / ambient media artifacts
- genuine recovery windows

Do not flatten this into a simplistic "slept badly -> poor day" story. Consider delayed effects, polyphasic patterns, catch-up sleep, nights with media but no keypresses, and long wake-like overlaps that may actually reflect stale activity tracking.

7. Notice what is absent as well as what is present.
Missing commits, missing shell history, missing sleep segments, absent Polylogue sessions, export lag, thin browser data, and stale health coverage can all matter. Explain gaps when they change the interpretation.

8. Be multifaceted.
A strong narrative should be able to hold multiple simultaneous truths:
- technically productive but physiologically rough
- commit-light yet cognitively important
- socially or media-heavy while still architecturally consequential
- full of AI output but mostly human steering
- apparently quiet locally while downstream work is brewing elsewhere

9. Use scale appropriately.
For a day narrative, emphasize phases, sequence, context shifts, and what carried into adjacent days.
For a week narrative, emphasize rhythm, pacing, alternating modes, and the week’s internal story.
For a month narrative, emphasize acts, campaigns, regime changes, and dominant threads.
For quarter/half/year/overview narratives, write in terms of eras, migrations, repeating loops, discontinuities, and long arcs rather than weekly bookkeeping.

10. Write with some imagination, but keep it disciplined.
The narrative should be engaging, insightful, and occasionally bold. You may offer clearly labeled speculative interpretations when they connect disparate signals into a plausible story. But every speculative move should remain tethered to actual evidence.

Specific things to look for before writing:
- dominant projects, topics, modes, providers, and work kinds
- surprise days: outliers, cliffs, rebounds, anomalous inactivity, strange overactivity
- rhythm markers: best/worst sleep, longest active days, zero-commit yet important days, review-heavy vs execution-heavy periods
- project/provider handoffs over time
- whether the period feels like building, debugging, review, migration, decomposition, cleanup, stabilization, recovery, drift, or recomposition
- whether human focus windows and AI work-event windows align, alternate, or decouple
- any data corruption or measurement oddities that meaningfully affect interpretation

Polylogue guidance:
- If the scaffold suggests important AI-mediated work, consult Polylogue products such as profiles, phases, threads, work-events, and day/week summaries.
- Use Polylogue to recover thematic continuity, work kind, repo focus, and session chronology.
- Do not rely on Polylogue alone when local repo, ActivityWatch, terminal, or sleep evidence contradicts it.

Output requirements:
- Produce polished Markdown prose.
- Use short sections with meaningful headings when they help.
- Avoid giant tables unless the assignment explicitly asks for them.
- Do not include raw evidence dumps.
- Do not leave TODOs, placeholders, or "rewrite this later" text.
- Do not narrate your own process.

Tone:
- intelligent, observant, grounded
- vivid without being purple
- reflective without becoming vague
- comfortable with ambiguity
- willing to point out tension, contradiction, obsession, depletion, momentum, drift, and renewal

Desired outcome:
The reader should come away feeling that the period has been understood from multiple angles: operationally, temporally, cognitively, and physiologically. They should understand not only what happened, but what kind of phase this was, how it related to surrounding periods, and what deeper rhythms the data seems to reveal.
```

## Suggested Use

Give the model, at minimum:

- the target period scaffold JSON
- the target period `narrative_brief.json` from `retrospective/scaffold/`
- parent and child briefs when available

Then optionally add:

- nearby already-written narratives for continuity checking
- Polylogue day/week summary products
- Polylogue work events, threads, phases, or profiles for AI-heavy periods
- direct sleep evidence when recovery is central to the period
- specific anomaly or data-quality notes when the period is noisy

## Practical Notes

- The prompt assumes the new scaffold structure from [`lynchpin/scripts/generate_scaffold.py`](../lynchpin/scripts/generate_scaffold.py), especially the `narrative_brief.json` companion written at every scale.
- If a period is mostly interesting because of uncertainty or data-quality conflict, the narrative should say that plainly instead of forcing false coherence.
- For long-range narratives, bias toward eras, transformations, enduring loops, and breaks in regime. For short-range narratives, bias toward sequence, pacing, and local causality.
