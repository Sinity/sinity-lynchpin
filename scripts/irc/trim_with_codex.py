#!/usr/bin/env python3
"""Trim a sinity conversation using Codex for semantic filtering."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
CODEX_CMD = [
    "codex",
    "exec",
    "--dangerously-bypass-approvals-and-sandbox",
    "--skip-git-repo-check",
    "-C",
    str(ROOT),
]

PROMPT_TEMPLATE = """You are assisting with analysing an IRC conversation. Each line begins with an integer id followed by a single space and then the original log line. The conversation contains a participant named "sinity".\n\nYou are given two views:\n1. The full numbered conversation.\n2. Only the numbered lines that contain the literal string "sinity" (case-insensitive), provided for quick reference.\n\nTask: Decide which lines must be kept to capture sinity's participation and the immediate context required to understand it. Apply these rules:\n- Always keep the header (id {header_id}).\n- Keep any line where sinity speaks (nick equals sinity) or where "sinity" appears in the text.\n- Keep additional context lines when they are needed to interpret sinity's lines or the direct responses to them.\n- Remove lines that are clearly unrelated to sinity.\n- When unsure about relevance, keep the line.\n\nReturn a JSON object of the form {{"keep": [id1, id2, ...]}} listing sorted unique integers. Respond with JSON only.\n\nFULL_CONVERSATION:\n{conversation}\n\nLINES_WITH_SINITY:\n{sinity_subset}\n"""


def run_codex(prompt: str) -> str:
    cmd = CODEX_CMD + [prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Codex command failed with exit code {result.returncode}:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        )
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.stdout


def extract_json(text: str) -> Dict[str, List[int]]:
    candidate = None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if stripped.startswith("{") and '"keep"' in stripped:
            candidate = stripped
            break
    if candidate is None:
        raise ValueError(f"No JSON object found in Codex output:\n{text}")
    return json.loads(candidate)


def build_prompt(numbered: List[str], sinity_lines: List[str], header_id: int) -> str:
    conversation_block = "\n".join(numbered)
    sinity_block = "\n".join(sinity_lines) if sinity_lines else "(none)"
    return PROMPT_TEMPLATE.format(
        header_id=header_id,
        conversation=conversation_block,
        sinity_subset=sinity_block,
    )


def number_lines(lines: List[str]) -> List[str]:
    return [f"{idx + 1} {line}" for idx, line in enumerate(lines)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Trim a single conversation using Codex")
    parser.add_argument("conversation", type=Path, help="Path to conversation log")
    args = parser.parse_args()

    content = args.conversation.read_text(encoding="utf-8").splitlines()
    numbered = number_lines(content)

    sinity_lines = [line for line in numbered if "sinity" in line.lower()]
    if not content:
        print("", end="")
        return

    prompt = build_prompt(numbered, sinity_lines, header_id=1)
    raw_output = run_codex(prompt)
    data = extract_json(raw_output)
    keep_ids = sorted(set(data.get("keep", [])))

    line_map = {idx + 1: text for idx, text in enumerate(content)}
    trimmed = [line_map[i] for i in keep_ids if i in line_map]

    print("\n".join(trimmed))


if __name__ == "__main__":
    main()
