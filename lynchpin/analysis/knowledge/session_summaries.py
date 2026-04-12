"""Session transcript summarisation helpers (codex-exec and claude-agent-sdk backends)."""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ...core.config import get_config
from ...core.codex_exec import run_codex_exec

DEFAULT_MODEL = os.environ.get("LYNCHPIN_SESSION_MODEL", "")
DEFAULT_CODEX_COMMAND = os.environ.get("LYNCHPIN_CODEX_COMMAND", "codex")
DEFAULT_SESSION_BACKEND = os.environ.get("LYNCHPIN_SESSION_BACKEND", "codex-exec")
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "source_path": {"type": "string"},
        "title": {"type": ["string", "null"]},
        "timeframe": {
            "type": ["string", "null"],
            "description": "When the session occurred.",
        },
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": ["string", "null"]},
                    "task": {"type": "string"},
                    "status": {"type": ["string", "null"]},
                },
                "required": ["owner", "task", "status"],
                "additionalProperties": False,
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "raw_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "source_path",
        "title",
        "timeframe",
        "summary",
        "highlights",
        "decisions",
        "follow_ups",
        "action_items",
        "risks",
        "raw_references",
    ],
    "additionalProperties": False,
}

MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-5-mini": {"input": 0.0000015, "output": 0.0000020},
}
SYSTEM_PROMPT = "You produce concise JSON summaries of assistant-mediated development sessions."
LogFn = Callable[[str], None]


@dataclass(frozen=True)
class SessionSummaryResult:
    input_path: Path
    output_path: Path
    model: str
    backend: str
    wrote: bool
    skipped: bool
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    cost_usd: Optional[float]


def _noop(_message: str) -> None:
    pass


def _normalize_backend(backend: str | None) -> str:
    normalized = (backend or DEFAULT_SESSION_BACKEND).strip().lower()
    return normalized or DEFAULT_SESSION_BACKEND


def load_transcript(path: Path, max_chars: int) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def build_prompt(transcript: str, source_path: Path) -> str:
    return textwrap.dedent(
        f"""
        You are summarising an assistant-assisted development session.

        Produce a JSON object with the following keys:
        - source_path (string): absolute or workspace-relative path to the transcript.
        - title (string): concise session title capturing the main objective.
        - timeframe (string): when the session took place (infer from transcript if possible, else null).
        - summary (string): 3-4 sentence synthesis of the whole session.
        - highlights (array of strings): key moments, breakthroughs, or blockers.
        - decisions (array of strings): explicit choices made.
        - follow_ups (array of strings): pending investigations or context questions.
        - action_items (array of objects): {{owner?, task, status?}} for concrete next steps.
        - risks (array of strings): notable risks, ambiguities, or missing data.
        - raw_references (array of strings): canonical paths or IDs mentioned.

        Keep tone factual, under 300 words total. Do not invent information.
        Focus on clarifying relationships to other projects when mentioned.
        Return strict JSON matching this schema:
        {json.dumps(SUMMARY_SCHEMA, ensure_ascii=False)}

        Transcript:
        ```markdown
        {transcript}
        ```
        """
    ).strip()


def _estimate_cost(
    model: str,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> Optional[float]:
    pricing = MODEL_PRICING.get(model)
    if not pricing or prompt_tokens is None or completion_tokens is None:
        return None
    return prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]


def _log_call(entry: dict[str, Any]) -> None:
    log_path = get_config().session_summary_log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _run_codex_exec(prompt: str, model: str | None) -> tuple[str, str, int, int]:
    result = run_codex_exec(
        prompt,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        output_schema=SUMMARY_SCHEMA,
        codex_command=DEFAULT_CODEX_COMMAND,
    )
    return result.model, result.text, 0, 0


def _run_claude_sdk(prompt: str, model: str | None) -> tuple[str, str, int, int]:
    from ...core.claude_sdk import run_claude_sdk

    result = asyncio.run(
        run_claude_sdk(
            prompt,
            system_prompt=SYSTEM_PROMPT,
            model=model,
            output_schema=SUMMARY_SCHEMA,
            allowed_tools=["Read"],
            max_turns=5,
        )
    )
    return result.model, result.text, result.input_tokens, result.output_tokens


def summarise_session_transcript(
    input_path: Path,
    *,
    output: Optional[Path] = None,
    model: str = DEFAULT_MODEL,
    backend: str | None = DEFAULT_SESSION_BACKEND,
    max_chars: int = 20000,
    force: bool = False,
    log: LogFn | None = None,
) -> SessionSummaryResult:
    if log is None:
        log = _noop
    if not input_path.exists():
        raise ValueError(f"Input file not found: {input_path}")

    resolved_backend = _normalize_backend(backend)
    destination = output or (get_config().session_summary_dir / f"{input_path.stem}.json")
    if destination.exists() and not force:
        log(
            f"Summary already exists at {destination}; skipping. Use force=True to regenerate."
        )
        return SessionSummaryResult(
            input_path=input_path,
            output_path=destination,
            model=model or "config-default",
            backend=resolved_backend,
            wrote=False,
            skipped=True,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
        )

    transcript, truncated = load_transcript(input_path, max_chars=max_chars)
    if truncated:
        log(f"Transcript exceeds {max_chars} characters; truncating for prompt.")
    user_prompt = build_prompt(transcript, input_path)
    log(f"Summarising {input_path} with {resolved_backend}...")
    if resolved_backend == "claude-agent-sdk":
        resolved_model, raw_message, prompt_tokens, completion_tokens = _run_claude_sdk(
            user_prompt, model or None,
        )
    else:
        resolved_model, raw_message, prompt_tokens, completion_tokens = _run_codex_exec(
            user_prompt, model or None,
        )

    try:
        summary = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model returned invalid JSON:\n{raw_message}") from exc

    summary.setdefault("source_path", str(input_path))
    summary.setdefault("raw_references", []).append(str(input_path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total_tokens = prompt_tokens + completion_tokens
    cost = _estimate_cost(resolved_model, prompt_tokens, completion_tokens)
    _log_call(
        {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "input_path": str(input_path),
            "output_path": str(destination),
            "model": resolved_model,
            "backend": resolved_backend,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }
    )

    return SessionSummaryResult(
        input_path=input_path,
        output_path=destination,
        model=resolved_model,
        backend=resolved_backend,
        wrote=True,
        skipped=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost,
    )
