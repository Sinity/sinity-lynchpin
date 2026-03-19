"""Shared helpers for deterministic Codex CLI exec runs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import get_config

DEFAULT_CODEX_COMMAND = os.environ.get("LYNCHPIN_CODEX_COMMAND", "codex")
DEFAULT_REASONING_EFFORT = os.environ.get("LYNCHPIN_CODEX_REASONING_EFFORT", "medium")


@dataclass(frozen=True)
class CodexExecResult:
    model: str
    text: str


def run_codex_exec(
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    output_schema: dict[str, Any] | None = None,
    cwd: Path | None = None,
    codex_command: str = DEFAULT_CODEX_COMMAND,
) -> CodexExecResult:
    codex = shutil.which(codex_command)
    if codex is None:
        raise RuntimeError(f"Codex CLI command not found: {codex_command}")

    cfg = get_config()
    root = cwd or cfg.repo_root
    full_prompt = prompt.strip()
    if system_prompt:
        full_prompt = f"{system_prompt.strip()}\n\n{full_prompt}"

    with TemporaryDirectory(prefix="lynchpin-codex-exec-") as tmpdir:
        tmp_root = Path(tmpdir)
        output_path = tmp_root / "final-output.txt"
        command = [
            codex,
            "exec",
            "-C",
            str(root),
            "-c",
            f'model_reasoning_effort="{DEFAULT_REASONING_EFFORT}"',
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
        ]
        if output_schema is not None:
            schema_path = tmp_root / "output-schema.json"
            schema_path.write_text(
                json.dumps(output_schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            command.extend(["--output-schema", str(schema_path)])
        if model:
            command.extend(["--model", model])
        command.append("-")

        result = subprocess.run(
            command,
            input=full_prompt,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "codex exec failed:\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )

        text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        if not text:
            raise RuntimeError(
                "codex exec produced no final text.\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )
        return CodexExecResult(
            model=model or "codex-config-default",
            text=text,
        )
