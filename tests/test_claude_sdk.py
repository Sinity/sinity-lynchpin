"""Tests for the Claude Agent SDK backend (lynchpin.core.claude_sdk).

Unit tests run without the claude CLI. Integration tests (marked slow) require
a live claude CLI with subscription auth.
"""
from __future__ import annotations

import math
import shutil

import pytest

from lynchpin.core.claude_sdk import (
    CREDIT_RATES,
    ClaudeSDKResult,
    _build_subscription_env,
    _infer_model_family,
    estimate_credits,
)

# ---------------------------------------------------------------------------
# Unit tests (no CLI needed)
# ---------------------------------------------------------------------------


class TestBuildSubscriptionEnv:
    def test_suppresses_api_key(self):
        env = _build_subscription_env()
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_returns_dict(self):
        env = _build_subscription_env()
        assert isinstance(env, dict)


class TestEstimateCredits:
    def test_opus_output_only(self):
        # 1000 output tokens at 50/15 ≈ 3333.3 → ceil = 3334
        credits = estimate_credits("opus", 0, 1000)
        assert credits == math.ceil(1000 * 50 / 15)

    def test_opus_mixed(self):
        # 200 input at 10/15 + 1000 output at 50/15
        credits = estimate_credits("opus", 200, 1000)
        expected = math.ceil(200 * 10 / 15 + 1000 * 50 / 15)
        assert credits == expected

    def test_haiku_cheaper(self):
        haiku = estimate_credits("haiku", 1000, 1000)
        opus = estimate_credits("opus", 1000, 1000)
        assert haiku < opus

    def test_sonnet_middle(self):
        haiku = estimate_credits("haiku", 1000, 1000)
        sonnet = estimate_credits("sonnet", 1000, 1000)
        opus = estimate_credits("opus", 1000, 1000)
        assert haiku < sonnet < opus

    def test_unknown_model_defaults_to_opus(self):
        unknown = estimate_credits("gpt-5", 1000, 1000)
        opus = estimate_credits("opus", 1000, 1000)
        assert unknown == opus

    def test_zero_tokens(self):
        assert estimate_credits("opus", 0, 0) == 0


class TestInferModelFamily:
    def test_opus(self):
        assert _infer_model_family("claude-opus-4-6-20250609") == "opus"

    def test_sonnet(self):
        assert _infer_model_family("claude-sonnet-4-6") == "sonnet"

    def test_haiku(self):
        assert _infer_model_family("claude-haiku-4-5") == "haiku"

    def test_unknown_defaults_opus(self):
        assert _infer_model_family("some-unknown-model") == "opus"

    def test_case_insensitive(self):
        assert _infer_model_family("Claude-OPUS-4") == "opus"


class TestCreditRates:
    def test_output_is_5x_input(self):
        for family, (inp, out) in CREDIT_RATES.items():
            assert abs(out / inp - 5.0) < 1e-10, f"{family}: output should be 5× input"

    def test_opus_is_5x_haiku(self):
        haiku_in, _ = CREDIT_RATES["haiku"]
        opus_in, _ = CREDIT_RATES["opus"]
        assert abs(opus_in / haiku_in - 5.0) < 1e-10


# ---------------------------------------------------------------------------
# Integration tests (require claude CLI + subscription auth)
# ---------------------------------------------------------------------------

def _has_usable_claude_sdk_runtime() -> bool:
    if shutil.which("claude") is None:
        return False
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return True


HAS_CLAUDE = _has_usable_claude_sdk_runtime()


@pytest.mark.slow
@pytest.mark.skipif(
    not HAS_CLAUDE,
    reason="claude CLI or claude_agent_sdk runtime not available",
)
class TestClaudeSDKIntegration:
    @pytest.fixture(autouse=True)
    def _skip_in_ci(self):
        """Skip in CI environments where subscription auth won't be available."""
        import os
        if os.environ.get("CI"):
            pytest.skip("Skipping live SDK test in CI")

    def test_simple_query(self):
        import asyncio
        from lynchpin.core.claude_sdk import run_claude_sdk

        result = asyncio.run(
            run_claude_sdk(
                "Reply with exactly the word 'hello' and nothing else.",
                allowed_tools=[],
                max_turns=1,
            )
        )
        assert isinstance(result, ClaudeSDKResult)
        assert result.text.strip().lower() == "hello"
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert result.estimated_credits > 0
        assert result.cost_usd == 0.0  # subscription auth

    def test_tool_using_query(self):
        import asyncio
        from lynchpin.core.claude_sdk import run_claude_sdk

        result = asyncio.run(
            run_claude_sdk(
                "Use the Bash tool to run 'echo test123' and tell me what it printed.",
                allowed_tools=["Bash"],
                max_turns=5,
            )
        )
        assert isinstance(result, ClaudeSDKResult)
        assert "test123" in result.text
        assert result.num_turns >= 2  # at least: tool call + response

    def test_structured_output(self):
        import asyncio
        import json
        from lynchpin.core.claude_sdk import run_claude_sdk

        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
        result = asyncio.run(
            run_claude_sdk(
                "Return a JSON object with ok set to true.",
                output_schema=schema,
                allowed_tools=[],
                max_turns=1,
            )
        )
        parsed = json.loads(result.text)
        assert parsed["ok"] is True
