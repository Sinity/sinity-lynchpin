#!/usr/bin/env python3
"""
Generate a structured Level-1 summary for a chat/coding session using an LLM.

This script is the production entry point for the progressive summarisation
workflow (`docs/plans/progressive-summaries.md`).  It reads a Markdown transcript,
invokes an LLM (OpenAI-compatible API by default), and writes a JSON summary with
highlight bullets, decisions, follow-ups, and raw-path references.

Example:
    python scripts/summarise_session.py \
        docs/reference/sessions/2025-10-24-codex.md \
        --output data/derived/session_summaries/2025-10-24-codex.json
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Optional

import requests
import typer

app = typer.Typer(pretty_exceptions_show_locals=False)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_API_BASE = "https://api.openai.com/v1"
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "source_path": {"type": "string"},
        "title": {"type": "string"},
        "timeframe": {"type": "string", "description": "When the session occurred."},
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {"type": "object", "properties": {"owner": {"type": "string"}, "task": {"type": "string"}, "status": {"type": "string"}}, "required": ["task"]},
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "raw_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source_path", "summary", "highlights", "raw_references"],
}


def load_transcript(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text
    typer.secho(
        f"Transcript exceeds {max_chars} characters; truncating for prompt.",
        err=True,
        fg=typer.colors.YELLOW,
    )
    return text[:max_chars]


def build_prompt(transcript: str, source_path: Path) -> str:
    return textwrap.dedent(
        f"""
        You are summarising an assistant-assisted development session.

        Produce a JSON object with the following keys:
        - source_path (string): absolute or workspace-relative path to the transcript.
        - title (string): concise session title capturing the main objective.
        - timeframe (string): when the session took place (infer from transcript if possible, else null).
        - summary (string): 3–4 sentence synthesis of the whole session.
        - highlights (array of strings): key moments, breakthroughs, or blockers.
        - decisions (array of strings): explicit choices made.
        - follow_ups (array of strings): pending investigations or context questions.
        - action_items (array of objects): {{owner?, task, status?}} for concrete next steps.
        - risks (array of strings): notable risks, ambiguities, or missing data.
        - raw_references (array of strings): canonical paths or IDs mentioned.

        Keep tone factual, under 300 words total. Do not invent information.
        Focus on clarifying relationships to other projects when mentioned.

        Transcript:
        ```markdown
        {transcript}
        ```

        Remember to return strict JSON matching the schema.
        """
    ).strip()


def call_llm(
    *,
    api_base: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "session_summary", "schema": SUMMARY_SCHEMA},
        },
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"LLM API error {response.status_code}: {response.text}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected response format: {data}") from exc
    return content


@app.command()
def summarise(
    input_path: Path = typer.Argument(..., help="Markdown transcript to summarise"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="JSON output path (defaults to <input>.summary.json)",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    api_base: str = typer.Option(DEFAULT_API_BASE, "--api-base"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="API key (defaults to OPENAI_API_KEY env variable)"
    ),
    max_chars: int = typer.Option(
        20000, help="Maximum transcript characters to include in the prompt"
    ),
) -> None:
    if not input_path.exists():
        raise typer.BadParameter(f"Input file not found: {input_path}")

    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise typer.BadParameter(
            "No API key supplied. Set OPENAI_API_KEY or pass --api-key."
        )

    transcript = load_transcript(input_path, max_chars=max_chars)
    user_prompt = build_prompt(transcript, input_path)
    system_prompt = "You produce concise JSON summaries of assistant-mediated development sessions."

    typer.echo(f"Summarising {input_path} with model {model}...")
    raw_response = call_llm(
        api_base=api_base,
        api_key=key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    try:
        summary = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model returned invalid JSON:\n{raw_response}") from exc

    summary.setdefault("source_path", str(input_path))
    summary.setdefault("raw_references", []).append(str(input_path))

    destination = output or input_path.with_suffix(".summary.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Summary written to {destination}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
