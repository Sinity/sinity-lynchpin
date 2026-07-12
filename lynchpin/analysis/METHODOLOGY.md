# Analysis methodology

Lynchpin's canonical analysis products are deterministic, provenance-carrying
artifacts. Interpretation may build on them, but it may not silently redefine
their measurements.

## Evidence classes

- **Measured fact:** parsed from a named source or computed by a traceable
  formula.
- **Association:** a relationship over an explicit time window and coverage
  set, without causal language.
- **Qualified inference:** a rule/model result with method, confidence, and
  fallback state.
- **Causal claim:** supported by an experiment or identification strategy that
  states treatment, outcome, assumptions, and falsification checks.
- **Narrative:** bounded synthesis that cites the evidence products it uses.

## Canonical contract

Every top-line metric must identify:

1. the artifact or substrate refresh that produced it;
2. the timeframe and coverage boundary;
3. the denominator and unit;
4. the method or formula;
5. any missing-source or fallback condition that changes interpretation.

Ambiguous metrics should be corrected or removed rather than defended through
prose.

## Regeneration

The normal command spine is:

```bash
python -m lynchpin.analysis materialize
python -m lynchpin.analysis analysis-validate
```

The analysis DAG owns dependency ordering. Individual steps remain available
for focused debugging, including project maps, dependency maps, and change
surface maps.

Generated map summaries and personal results belong under the ignored local
analysis root, not tracked documentation.

## Canonical code-analysis artifacts

`lynchpin/analysis/analysis_spec.json` defines the required code-analysis
artifact set. The maintained products include structure, temporal,
cross-project, ecosystem, and coherent snapshot metrics under
`.lynchpin/generated/analysis/`.

## Forbidden shortcuts

Canonical claims must not rely on:

- commit-message-only intent inference;
- LLM adjudication of ticket snippets as a metric denominator;
- external narrative packets promoted to fact without a contract change;
- treating missing coverage as zero;
- causal wording for an uncontrolled before/after comparison;
- mixing rows from incompatible substrate refreshes.

LLMs are useful for bounded synthesis, hypothesis generation, and explaining
measured artifacts. They are not an authority that can override the metric
layer.
