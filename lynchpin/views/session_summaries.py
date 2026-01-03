"""Session-level LLM summarisation helpers.

This replaces the legacy pipeline summariser so callers can run
`python -m lynchpin.views.session_summaries summarise <conversation.md>`.
See `docs/reference/sessions/README.md` for workflow details.
"""

from __future__ import annotations

import json
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import typer

app = typer.Typer(pretty_exceptions_show_locals=False)

DEFAULT_MODEL = os.environ.get("LYNCHPIN_SESSION_MODEL", "gpt-5-mini")
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
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "task": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["task"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "raw_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source_path", "summary", "highlights", "raw_references"],
}

DEFAULT_OUTPUT_DIR = Path("artefacts/knowledge/sessions/summaries")
LOG_PATH = Path("artefacts/knowledge/sessions/logs/session_summaries.jsonl")
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Costs in USD per 1 token (input/output). Update when vendors change pricing.
    "gpt-5-mini": {"input": 0.0000015, "output": 0.0000020},
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
) -> Dict[str, Any]:
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
    return response.json()


def _estimate_cost(model: str, prompt_tokens: Optional[int], completion_tokens: Optional[int]) -> Optional[float]:
    pricing = MODEL_PRICING.get(model)
    if not pricing or prompt_tokens is None or completion_tokens is None:
        return None
    return (
        prompt_tokens * pricing["input"] +
        completion_tokens * pricing["output"]
    )


def _log_call(entry: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


@app.command()
def summarise(
    input_path: Path = typer.Argument(..., help="Markdown transcript to summarise"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="JSON output path (defaults to artefacts/knowledge/sessions/summaries/<input_stem>.json)",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    api_base: str = typer.Option(DEFAULT_API_BASE, "--api-base"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="API key (defaults to OPENAI_API_KEY env variable)"
    ),
    max_chars: int = typer.Option(
        20000, help="Maximum transcript characters to include in the prompt"
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run even if the output already exists"
    ),
) -> None:
    if not input_path.exists():
        raise typer.BadParameter(f"Input file not found: {input_path}")

    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise typer.BadParameter(
            "No API key supplied. Set OPENAI_API_KEY or pass --api-key."
        )

    destination = output or (DEFAULT_OUTPUT_DIR / f"{input_path.stem}.json")
    if destination.exists() and not force:
        typer.secho(
            f"Summary already exists at {destination}; skipping. Use --force to regenerate.",
            fg=typer.colors.YELLOW,
        )
        return

    transcript = load_transcript(input_path, max_chars=max_chars)
    user_prompt = build_prompt(transcript, input_path)
    system_prompt = "You produce concise JSON summaries of assistant-mediated development sessions."

    typer.echo(f"Summarising {input_path} with model {model}...")
    response_data = call_llm(
        api_base=api_base,
        api_key=key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    try:
        raw_message = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected response format: {response_data}") from exc

    try:
        summary = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model returned invalid JSON:\n{raw_message}") from exc

    summary.setdefault("source_path", str(input_path))
    summary.setdefault("raw_references", []).append(str(input_path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Summary written to {destination}", fg=typer.colors.GREEN)

    usage = response_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    cost = _estimate_cost(model, prompt_tokens, completion_tokens)
    log_entry = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_path": str(destination),
        "model": model,
        "api_base": api_base,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "raw_usage": usage,
    }
    _log_call(log_entry)


if __name__ == "__main__":
    app()
