# Developer Productivity Research Notes

> Extracted research on developer productivity metrics and AI-era dynamics. For methodology and context.

## Pre-AI Baselines (2015-2020)

| Source | Metric | Notes |
|--------|--------|-------|
| Mythical Man Month | ~10 LOC/day | Averaged across project lifecycle |
| Capers Jones | 325-750 LOC/month | 15-35 LOC/day |
| NDepend author (14yr sustained) | 80 LOC/day | High quality, long-term |
| Solo developer realistic | 50-150 LOC/day | Quality code, sustainable |
| Sprint bursts | 200-400 LOC/day | Not sustainable |

**Churn dynamics:**
- Pre-AI churn rate: 3-4% within 2 weeks
- Post-AI (2023): 5.5% churn rate
- Healthy refactoring: ~25% of changes
- Churn multiplier: 1.5x conservative → 4-6x high iteration

## AI-Era Impact Research

| Finding | Source |
|---------|--------|
| 20-55% speedup on boilerplate | IDE autocomplete studies |
| 88% code retention | Copilot acceptance rates |
| 19% SLOWER on complex tasks | METR 2025 (experienced devs) |
| 21% faster on enterprise tasks | Google internal study |
| 84% of developers use AI tools | Industry surveys |
| 41% of code now AI-generated | 2024 estimates |
| 4x increase in copy/pasted code | Code quality studies |

## The "Vibecoding Paradox"

Why AI didn't commoditize software development:

1. **Idea overhang smaller than assumed** — fewer people have clear technical ideas than expected
2. **Friction filter** — many tried AI coding, gave up at first obstacle
3. **Cost barrier** — $200/month is "nothing" but still stops most
4. **Intersection is small** — (ideas + persistence + AI skill + time) = very few people
5. **Prototypes stay prototypes** — most AI-generated code never ships
6. **Distribution still hard** — marketing/sales unaffected by code generation

## Solo Developer Velocity Examples

| Project | Output | Timeline |
|---------|--------|----------|
| Toby Fox (Undertale) | ~50-100K LOC | 2.5 years |
| Eric Barone (Stardew Valley) | ~300K LOC | 5 years |
| NDepend author | ~400K LOC | 14 years |
| P95-P97 AI-assisted | ~18K LOC/month | Sustained |

## The Velocity Delta

When comparing pre-AI and post-AI productivity, the multiplier isn't "became better":

- **Different era** — pre-AI vs AI-assisted tooling
- **Different stack** — legacy forms vs modern typed languages
- **Different constraints** — employed vs self-directed
- **Different codebase health** — existing spaghetti vs greenfield
- **Removed bottleneck** — "cursor moving" delegated, "thinking" preserved

## Correctness vs Feature Velocity

Developer profiles vary by work type:

| Profile | LOC/day | Refactor Ratio | Characteristic |
|---------|---------|----------------|----------------|
| Feature developer | Higher | Lower | New code velocity |
| Correctness engineer | Lower | Higher | Prevents outages, cleans messes |
| Integration engineer | Variable | Variable | Cross-system work |

The correctness engineer profile is systematically undervalued by LOC metrics — work that prevents outages doesn't get credit.

---

*These are research notes, not formal documentation. For Sinex architecture, see `../current/architecture/`.*
