#!/usr/bin/env python3
"""
Prepare structured session summaries from Markdown transcripts.

This is the “easy” scaffolding for the progressive summarisation workflow
(`docs/plans/progressive-summaries.md`).  It does not call any LLMs yet; instead
it extracts structural cues (title, headings, timestamps) and emits a JSON
template that an LLM or human editor can fill in later.  The template includes
slots for highlights, decisions, follow-ups, and raw path references, providing
a consistent schema for Level-1 summaries and Sinevec embeddings.

Example:
    python scripts/summarise_session.py \
        docs/reference/sessions/2025-10-24-codex.md \
        --output data/derived/session_summaries/2025-10-24-codex.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

import typer

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


@dataclass
class SummaryTemplate:
    source_path: str
    title: Optional[str]
    headings: List[str]
    word_count: int
    token_estimate: int
    highlights: List[str]
    decisions: List[str]
    follow_ups: List[str]
    raw_references: List[str]


def estimate_tokens(word_count: int, ratio: float = 0.75) -> int:
    """Approximate token count from words (heuristic tuned for English prose)."""
    return max(1, int(word_count / ratio))


def extract_headings(markdown: str) -> List[str]:
    headings: List[str] = []
    for line in markdown.splitlines():
        match = HEADING_RE.match(line.strip())
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            headings.append(f"{level}:{text}")
    return headings


def build_template(path: Path) -> SummaryTemplate:
    text = path.read_text(encoding="utf-8")
    word_count = len(text.split())
    headings = extract_headings(text)
    title = None
    if headings:
        first_heading = headings[0]
        if first_heading.startswith("1:"):
            title = first_heading.split(":", 1)[1]

    return SummaryTemplate(
        source_path=str(path),
        title=title,
        headings=headings,
        word_count=word_count,
        token_estimate=estimate_tokens(word_count),
        highlights=[],
        decisions=[],
        follow_ups=[],
        raw_references=[str(path)],
    )


def write_template(template: SummaryTemplate, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(template), handle, ensure_ascii=False, indent=2)


def summarise(
    input_path: Path = typer.Argument(..., help="Markdown transcript to summarise"),
    output_path: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination JSON template (defaults to alongside source)",
    ),
) -> None:
    if not input_path.exists():
        raise typer.BadParameter(f"Input file not found: {input_path}")

    template = build_template(input_path)
    destination = (
        output_path
        if output_path is not None
        else input_path.with_suffix(".summary.json")
    )
    write_template(template, destination)
    typer.echo(f"Summary template written to {destination}")


if __name__ == "__main__":
    typer.run(summarise)
