"""Skill cartography Layer 1 — mine polylogue/atuin/git evidence, synthesize
a skill map via ``claude -p``.

Evidence-based skill map, NOT self-report or agent testimony. The rich
lanes are polylogue dialogues (agent-orchestration practice, architectural
choice-points, domain discussion) and especially correction patterns --
what the operator catches agents on. Atuin/git evidence is thin here: the
operator directs agents rather than typing code or committing directly.

Same state-dump -> claude -p -> versioned-output shape as the sinnix
enrich-dump family of tools; kept as a standalone script (like
scripts/asciinema_index.py) rather than wired into the materialize DAG,
since this is a periodic evidence-mining job, not a daily substrate input.

Output: /realm/data/derived/reports/cartography/<timestamp>/{evidence-bundle.json,skill-map.json}
Re-derivable at any time; never writes to polylogue's or sinex's stores.

Run: python scripts/cartography.py
Env: SINNIX_CARTOGRAPHY_SINCE_DAYS (default 90)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lynchpin.core.projects import PROJECT_ROOT

OUTPUT_ROOT = Path("/realm/data/derived/reports/cartography")
SINCE_DAYS = int(os.environ.get("SINNIX_CARTOGRAPHY_SINCE_DAYS", "90"))
OPERATOR_AUTHOR = "Sinity"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def gather_corrections(run_dir: Path) -> dict:
    """Operator messages that catch/correct an agent (high-signal for real knowledge)."""
    query = (
        'role:user AND (text:"no wait" OR text:"actually" OR text:"that.s wrong" '
        'OR text:"you are wrong" OR text:"incorrect")'
    )
    proc = _run([
        "polylogue", "--since", f"{SINCE_DAYS} days ago",
        "find", query,
        "then", "read", "--view", "messages", "--format", "json", "--all",
    ])
    (run_dir / "corrections.stderr").write_text(proc.stderr)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"status": "unavailable", "note": "polylogue query failed, see corrections.stderr"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "note": "polylogue query returned non-JSON, see corrections.stderr"}


def gather_facets(run_dir: Path) -> dict:
    """Domain/topic facets discussed, bounded window."""
    try:
        proc = _run(
            ["polylogue", "--since", f"{SINCE_DAYS} days ago", "facets", "--format", "json"],
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        (run_dir / "facets.stderr").write_text("timed out after 60s")
        return {"status": "unavailable", "note": "facets query timed out or failed, see facets.stderr"}
    (run_dir / "facets.stderr").write_text(proc.stderr)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"status": "unavailable", "note": "facets query timed out or failed, see facets.stderr"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "note": "facets query returned non-JSON, see facets.stderr"}


def gather_atuin(run_dir: Path) -> dict:
    """Tool vocabulary over time. Thin evidence, gathered anyway; atuin stats has no --format json."""
    proc = _run(["atuin", "stats", f"{SINCE_DAYS} days"])
    (run_dir / "atuin.stderr").write_text(proc.stderr)
    (run_dir / "atuin-stats.txt").write_text(proc.stdout)
    return {"status": "text-only", "note": "atuin stats has no --format json; raw text captured"}


def gather_git_activity(run_dir: Path) -> list[dict]:
    """Languages/repos touched across the project constellation."""
    since_date = (datetime.now(timezone.utc) - timedelta(days=SINCE_DAYS)).strftime("%Y-%m-%d")
    activity = []
    errors = []
    for repo in sorted(PROJECT_ROOT.glob("*/")):
        if not (repo / ".git").is_dir():
            continue
        proc = _run([
            "git", "-C", str(repo), "log",
            f"--author={OPERATOR_AUTHOR}", f"--since={since_date}", "--oneline",
        ])
        if proc.returncode != 0:
            errors.append(f"{repo.name}: {proc.stderr.strip()}")
            continue
        authored = len([line for line in proc.stdout.splitlines() if line.strip()])
        if authored == 0:
            continue
        activity.append({"repo": repo.name, "operator_authored_commits": authored})
    if errors:
        (run_dir / "git.stderr").write_text("\n".join(errors) + "\n")
    return activity


def synthesize_skill_map(run_dir: Path, bundle_path: Path) -> None:
    prompt = (
        "You are building Layer 1 of sinnix-d0t (skill cartography): a living, "
        "evidence-based model of the operator's actual knowledge/skills, NOT self-report "
        "or agent testimony/praise -- receiver-behavior and ground-truth evidence only. "
        f"Read the evidence bundle at {bundle_path} (correction patterns from polylogue -- what "
        "the operator catches agents on is HIGH signal for real knowledge; discussed "
        "facets/topics; atuin/git activity, expected to be THIN since the operator "
        "directs agents rather than typing code directly -- treat that thinness itself "
        "as evidence of an agent-orchestration-heavy practice, not a gap). "
        'Output a JSON array of skill nodes, each: {"skill": name, "taxonomy": '
        "one of [languages_runtimes, systems, ml_ai_practice, math, domain_knowledge, "
        'meta_skills], "confidence": 0-1, "evidence": [short anchors citing what in '
        'the bundle supports this], "note": one sentence}. Per the bead\'s own '
        "rescope, expect meta_skills (agent orchestration, architectural judgment) to "
        "dominate with high confidence, and object-level coding fluency to show thin "
        "evidence -- say so explicitly rather than inflating it. Write ONLY the JSON "
        "array to stdout, no prose."
    )
    proc = _run(["claude", "-p", "--output-format", "json", prompt])
    (run_dir / "claude-p-response.json").write_text(proc.stdout)
    (run_dir / "claude-p.stderr").write_text(proc.stderr)
    if proc.returncode != 0:
        print("cartography: claude -p synthesis failed, see claude-p.stderr", file=sys.stderr)
        raise SystemExit(1)

    skill_map_path = run_dir / "skill-map.json"
    try:
        response = json.loads(proc.stdout)
        result = response.get("result", "")
    except json.JSONDecodeError:
        result = ""
    # Strip markdown code fences if the model wrapped the JSON in one.
    lines = [line for line in result.splitlines() if not line.strip().startswith("```")]
    text = "\n".join(lines).strip()
    skill_map_path.write_text(text)

    if text:
        try:
            json.loads(text)
        except json.JSONDecodeError:
            print(
                "cartography: WARNING -- skill-map.json is not valid JSON after fence-stripping, "
                "inspect claude-p-response.json",
                file=sys.stderr,
            )
    else:
        print(
            "cartography: WARNING -- could not extract skill-map.json from claude -p response, "
            "raw response preserved",
            file=sys.stderr,
        )


def main() -> None:
    now = datetime.now(timezone.utc)
    run_dir = OUTPUT_ROOT / now.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"cartography: gathering evidence (last {SINCE_DAYS}d) into {run_dir}", file=sys.stderr)

    corrections = gather_corrections(run_dir)
    facets = gather_facets(run_dir)
    atuin = gather_atuin(run_dir)
    git_activity = gather_git_activity(run_dir)

    bundle = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": SINCE_DAYS,
        "corrections": corrections,
        "facets": facets,
        "atuin": atuin,
        "git_activity": git_activity,
    }
    bundle_path = run_dir / "evidence-bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2))

    print("cartography: evidence bundle written, invoking claude -p for synthesis", file=sys.stderr)
    synthesize_skill_map(run_dir, bundle_path)

    latest = OUTPUT_ROOT / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(run_dir)

    print(f"cartography: done -> {run_dir}", file=sys.stderr)
    print(str(run_dir))


if __name__ == "__main__":
    main()
