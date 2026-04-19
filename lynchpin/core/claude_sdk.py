"""Claude Agent SDK backend — analytical agent runner for subscription-based usage.

This module wraps the Claude Agent SDK (``claude_agent_sdk``) as a proper agent
execution backend, designed for tool-using multi-turn analytical workflows rather
than one-shot text generation.

Subscription vs API Credit Economics
-------------------------------------
Subscriptions use an internal credit system:

    credits = ceil(input_tokens × input_rate + output_tokens × output_rate)

    Model    Input cr/tok    Output cr/tok
    Haiku    2/15 ≈ 0.133    10/15 ≈ 0.667
    Sonnet   6/15 = 0.4      30/15 = 2.0
    Opus     10/15 ≈ 0.667   50/15 ≈ 3.333

Key advantage: **cache reads cost 0 credits** on subscription (vs 10% of input
price on the API). In agentic tool-use loops, every tool call re-reads the full
context as a cache read — free on subscription, paid on API.

Example (50 tool calls, 100K context, Opus, Max 5×):
    Subscription: ~50K credits → 833 sessions/week from 41.67M budget
    API: ~$8.81/session → $7,339/week equivalent → **318× value**

Budgets (Max 5× = $100/mo):
    3.3M credits per 5h session, 41.67M per week, ~180.6M per month.

Limitations vs API:
    - Cache reads: free (vs 10% on API)
    - Cost tracking: always $0 (credits estimable via formulas above)
    - Rate limits: credit-based, shared with interactive Claude Code usage
    - Model selection: tier-constrained (Max → Opus+Sonnet+Haiku)
    - Batch API: not available
    - Retry control: CLI-internal, opaque
    - SLA: best-effort (vs 99.5%+ on API)
    - Model pinning: no dated snapshot IDs
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credit estimation
# ---------------------------------------------------------------------------

CREDIT_RATES: dict[str, tuple[float, float]] = {
    "haiku": (2 / 15, 10 / 15),
    "sonnet": (6 / 15, 30 / 15),
    "opus": (10 / 15, 50 / 15),
}


def estimate_credits(
    model_family: str,
    input_tokens: int,
    output_tokens: int,
) -> int:
    """Estimate subscription credits consumed (cache reads = 0, not tracked)."""
    inp_rate, out_rate = CREDIT_RATES.get(
        model_family.lower(), CREDIT_RATES["opus"]
    )
    return math.ceil(input_tokens * inp_rate + output_tokens * out_rate)


def _infer_model_family(model_str: str) -> str:
    """Best-effort extraction of model family from a model identifier."""
    lower = model_str.lower()
    for family in ("haiku", "sonnet", "opus"):
        if family in lower:
            return family
    return "opus"  # conservative default


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeSDKResult:
    """Result from a Claude Agent SDK execution."""

    model: str
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float  # always 0.0 under subscription
    estimated_credits: int
    session_id: str | None
    num_turns: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Subscription auth
# ---------------------------------------------------------------------------


def _build_subscription_env() -> dict[str, str]:
    """Return env overrides that suppress ``ANTHROPIC_API_KEY``.

    The SDK merges ``{**os.environ, **options.env}``, so setting the key to
    empty string shadows any real key present in the environment.  The Claude
    CLI treats ``""`` as absent and falls through to subscription OAuth.

    This is the only env-override path the SDK exposes without subclassing
    the transport or monkey-patching ``os.environ``.
    """
    return {"ANTHROPIC_API_KEY": ""}


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


async def run_claude_sdk(
    prompt: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    output_schema: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: Literal[
        "default", "acceptEdits", "plan", "bypassPermissions"
    ] = "bypassPermissions",
    max_turns: int | None = None,
    effort: Literal["low", "medium", "high", "max"] | None = None,
    cwd: str | Path | None = None,
) -> ClaudeSDKResult:
    """Run an analytical agent via the Claude Agent SDK.

    By default the agent has full tool access (Read, Bash, Glob, Grep, etc.)
    with ``bypassPermissions`` so it can execute tools without interactive
    approval.  Pass ``allowed_tools=[]`` for rare one-shot / no-tool cases.

    Parameters
    ----------
    prompt:
        The task / analysis prompt.
    system_prompt:
        System-level instructions (domain knowledge, heuristics, etc.).
    model:
        Model to request (tier-constrained under subscription).
    output_schema:
        JSON Schema for structured output.  Wrapped into the SDK's
        ``output_format={"type": "json_schema", "schema": ...}``.
    allowed_tools:
        Explicit tool allowlist.  ``None`` means default tool suite.
        ``[]`` disables all tools (one-shot mode).
    permission_mode:
        How tool permissions are handled.  ``"bypassPermissions"`` is the
        default for automated analytical workflows.
    max_turns:
        Maximum agentic turns (tool-call rounds).  ``None`` means SDK default.
    effort:
        Reasoning effort level.
    cwd:
        Working directory for the agent's file operations.
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )
    except Exception as exc:  # pragma: no cover - environment-specific import surface
        raise RuntimeError(
            "claude_agent_sdk runtime is unavailable; check the devshell SDK dependencies",
        ) from exc

    options_kwargs: dict[str, Any] = {
        "env": _build_subscription_env(),
        "permission_mode": permission_mode,
    }
    if system_prompt is not None:
        options_kwargs["system_prompt"] = system_prompt
    if model is not None:
        options_kwargs["model"] = model
    if allowed_tools is not None:
        options_kwargs["allowed_tools"] = allowed_tools
    if max_turns is not None:
        options_kwargs["max_turns"] = max_turns
    if effort is not None:
        options_kwargs["effort"] = effort
    if cwd is not None:
        options_kwargs["cwd"] = str(cwd)
    if output_schema is not None:
        options_kwargs["output_format"] = {
            "type": "json_schema",
            "schema": output_schema,
        }

    options = ClaudeAgentOptions(**options_kwargs)

    text = ""
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0
    session_id: str | None = None
    num_turns = 0
    duration_ms = 0
    resolved_model = model or "claude-agent-sdk"

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage) and resolved_model == (model or "claude-agent-sdk"):
            resolved_model = message.model
        if isinstance(message, ResultMessage):
            text = message.result or ""
            session_id = message.session_id
            num_turns = message.num_turns
            duration_ms = message.duration_ms
            if message.total_cost_usd is not None:
                cost_usd = float(message.total_cost_usd)
            if message.usage:
                fresh = message.usage.get("input_tokens", 0)
                cached_create = message.usage.get(
                    "cache_creation_input_tokens", 0
                )
                cached_read = message.usage.get(
                    "cache_read_input_tokens", 0
                )
                input_tokens = fresh + cached_create + cached_read
                output_tokens = message.usage.get("output_tokens", 0)

    family = _infer_model_family(resolved_model)
    credits = estimate_credits(family, input_tokens, output_tokens)

    result = ClaudeSDKResult(
        model=resolved_model,
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        estimated_credits=credits,
        session_id=session_id,
        num_turns=num_turns,
        duration_ms=duration_ms,
    )
    log.info(
        "claude-sdk: %d turns, %d in + %d out tokens, ~%d credits, %.1fs",
        num_turns,
        input_tokens,
        output_tokens,
        credits,
        duration_ms / 1000,
    )
    return result
