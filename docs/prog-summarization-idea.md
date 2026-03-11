---
created: "2026-01-25"
purpose: "Recursive hierarchical decomposition orchestrator for context compression and codebase understanding"
status: "active - design phase"
---

# Recursive Summarization / Decomposition Orchestrator

## Executive Summary

A generalized **divide-and-conquer composition system** that can:
- Decompose huge documents/codebases into hierarchical elements
- Spawn parallel subagents to summarize each element independently
- Recursively handle elements too large for single pass
- Synthesize results into navigable summary trees
- Generalize to codebase grokking, plan generation, longform synthesis

## Problem Landscape

### Original Request
Build a "progressive summarization specialist" subagent to help Claude Code agents navigate chatlog histories efficiently and handle overwhelming input.

### Refinement
Not sequential progressive summarization (Tiago Forte style) but **recursive hierarchical decomposition**:

```
INPUT (overwhelming)
  → ORCHESTRATOR: "This is too big. Break it into parts."
  → DISTRIBUTE: Spawn subagents for each element (parallel)
  → SUBAGENT: "Summarize your section. If too big, recurse."
  → COLLECT: Subagents return summaries
  → SYNTHESIZE: Combine summaries into meta-summary
  → OUTPUT: Tree of .md files
```

## Core Design

### Architecture Pattern

```
ORCHESTRATOR (entry point)
├─ Parse input structure (detect sections/files/goals)
├─ Build routing index (what goes where)
├─ PARALLEL: Spawn SUBAGENT for each element
│   └─ SUBAGENT
│       ├─ Receive: [element content] + [context from siblings]
│       ├─ Try: Summarize self
│       ├─ If too large:
│       │   └─ RECURSIVELY: Spawn sub-subagents for own children
│       ├─ Collect: [child summaries]
│       └─ Output: .md file with own summary + tree pointer
└─ Collect all summaries
└─ Synthesize: Meta-summary linking to child summaries
└─ Write: Tree of nested .md files
```

### Key Insight: The Missing Piece

**Indexing/Routing Layer** - subagents need to know *which piece they're handling*:

- **Linear documents**: Headings + byte offsets (already have structure)
- **Codebases**: File graph + dependency ordering (need to construct)
- **Plans**: Goal DAG (subgoals branch from goals)
- **Generic**: "Composition element" abstraction (position, path, type, content bounds)

For codebases specifically: files conceptually concatenated, but ordering matters for comprehension. Ordering should reflect:
- Dependency hierarchy (low-level modules first)
- Logical grouping (related files together)
- Abstraction layers (bottom-up traversal)

## Implementation Roadmap

### Phase 1: Foundation (Now)

**Skill: `/recursive-summarize`**

```
Input:
  - document (file path | text | stdin)
  - structure hint (optional: "markdown" | "codebase" | "goal-tree")
  - max-chunk-size (optional: how big before recursing)

Output:
  - Summary tree in .claude/decompositions/[timestamp]-[name]/
  - Index file pointing to leaf summaries
  - Orchestrator summary at root
```

**Components**:

1. **Orchestrator Logic**
   - Detects composition structure
   - Builds routing index
   - Spawns subagents in parallel
   - Collects and synthesizes results

2. **Subagent (Recursive Summarizer Worker)**
   - Specialized prompt for single-element summary
   - Token budget awareness (knows when to recurse)
   - Outputs: own summary + children metadata
   - Writes: self-contained .md file

3. **Index/Routing Layer**
   - Markdown: heading hierarchy
   - Codebase: file dependency graph (future)
   - Plans: goal relationships (future)

4. **Output Tree**
   - Nested directory structure
   - Each file self-contained (readable standalone)
   - Index files for navigation
   - Breadcrumb links up/down hierarchy

### Phase 2: Codebase Grokking (Experiment)

**Goal**: Apply to entire codebases (sinex, sinnix, etc.)

**Needs**:
- Dependency ordering (important: good ordering = better comprehension)
- File grouping by layer/module
- Cross-file reference tracking
- "Import map" as routing index

**Output**: Navigable summary of entire codebase
- Layer summaries (lowest-level modules first)
- Module summaries (cohesive units)
- Cross-file patterns and concerns
- Dependency tree visualization

### Phase 3: Generalization (Vision)

**Planning Assistance**:
- User: "Help me build X"
- System: Recursively decompose goal → subgoals → tasks
- LLM suggests decomposition at each level
- Outputs: hierarchical plan tree

**Longform Synthesis**:
- Use summaries as building blocks for longer compositions
- Bottom-up assembly of understanding
- "Tell me everything about X" → walk summary tree → synthesize into narrative

**Cross-Document Indexing**:
- Build indices across entire summary trees
- "Where in codebase is error handling?" → search index → navigate to relevant subtree

## Technical Notes

### Subagent Spawning Strategy

```
Orchestrator:
  FOR EACH element:
    PARALLEL:
      Task(
        subagent_type="general-purpose",
        prompt="You are a recursive summarizer. Summarize this element...",
        input={
          content: element.text,
          path: element.path,
          siblings: [list of sibling paths],
          max_tokens: compute_budget(element.size),
          recurse_budget: remaining_parallel_slots
        }
      )
```

**Key**: Subagent receives full context of where it sits (path, siblings, parent) so its summary can be aware of role in larger composition.

### Token Budget Management

```
- Orchestrator allocates token budget
- Subagents know their maximum input size
- If input > max: RECURSIVELY spawn sub-subagents
- If already at recursion depth limit: TRUNCATE + SAMPLE (take key excerpts)
- Output summaries are compressed (summaries of summaries)
```

### Storage Structure

```
.claude/decompositions/
├── 2026-01-25-chatlog-session-001/
│   ├── _index.md (navigation guide)
│   ├── root_summary.md (meta-summary)
│   ├── section_001/
│   │   ├── _index.md
│   │   ├── summary.md
│   │   ├── section_001_001/
│   │   │   ├── summary.md
│   │   │   └── ...
│   │   └── ...
│   └── section_002/
│       └── ...
├── 2026-01-25-sinex-codebase/
│   ├── _index.md
│   ├── root_summary.md
│   ├── layer_core/
│   │   ├── summary.md
│   │   ├── module_event/
│   │   │   └── summary.md
│   │   └── ...
│   └── ...
```

Each .md file is **self-contained and readable** (not just a reference). Includes:
- Summary of this element
- Key findings/patterns
- Links to children (if summarized into subtree)
- Breadcrumb back to parent
- Cross-references to sibling patterns

## Potential Generalizations

### Codebase Analysis
```
Input: /realm/project/sinex
Output: Summary tree showing:
  - Architecture overview (layers)
  - Crate dependencies
  - Key algorithms per module
  - Data flow patterns
  - Error handling strategies
  - Test coverage per layer
```

### Planning System
```
Input: High-level goal (e.g., "Implement async error recovery")
Output: Hierarchical plan
  - Goal decomposition
  - Subgoal dependencies
  - Implementation sequence
  - Risk/unknowns per subgoal
  - Test strategy
```

### Document Corpus
```
Input: Entire knowledge base / project documentation
Output: Navigable index
  - Topic summaries
  - Cross-references
  - "Where should I read first?" paths
  - Glossary from all docs
```

## Questions / Open Design

1. **Ordering for codebases**: Should ordering be:
   - Dependency-first (bottom-up)?
   - Abstraction-level first?
   - Conceptual grouping first?
   - Some hybrid?

2. **Recursion termination**: When to stop decomposing?
   - Token-based (stop when summary < N tokens)?
   - Structure-based (stop at natural leaf units)?
   - Depth-based (max recursion depth)?
   - Adaptive (stop when summaries plateau in information gain)?

3. **Synthesis strategy**: How to combine child summaries?
   - Simple concatenation + deduplication?
   - LLM re-summarization?
   - Statistical aggregation of concepts?
   - Hierarchical importance weighting?

4. **Parallel vs Sequential subagent spawning**:
   - Parallel: Faster, but higher token cost (all subagents read full input)
   - Sequential: Slower, but more efficient (only parent reads full)
   - Hybrid: Parallel within depth, sequential across depths?

## Next Steps

1. **Create `/recursive-summarize` skill**
2. **Implement orchestrator + subagent worker prompt**
3. **Test on: real chatlog → summary tree**
4. **Document patterns discovered in test**
5. **Plan Phase 2: codebase decomposition** (decide on ordering strategy first)

## References

- Tiago Forte's Progressive Summarization (inspiration for multi-pass refinement)
- Recursive composition patterns (compilers, AST walkers)
- Divide-and-conquer for document indexing
- LLM ensemble/tree-of-thought methods

---

**Status**: Ready for Phase 1 implementation. All pieces understood. Just missing execution.
