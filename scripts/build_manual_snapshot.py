#!/usr/bin/env python3
"""Rebuild docs/reference/knowledge-graph/manual_snapshot.yaml from fragments."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAG_DIR = REPO_ROOT / "docs" / "reference" / "knowledge-graph" / "manual_snapshot_fragments"
OUTPUT = REPO_ROOT / "docs" / "reference" / "knowledge-graph" / "manual_snapshot.yaml"

def main() -> None:
    fragments = sorted(FRAG_DIR.glob("*.yaml"))
    if not fragments:
        raise SystemExit(f"No fragments found in {FRAG_DIR}")

    pieces = []
    for frag in fragments:
        text = frag.read_text()
        if not text.endswith("\n"):
            text += "\n"
        pieces.append(text)

    OUTPUT.write_text("".join(pieces))
    print(f"Wrote {OUTPUT} from {len(fragments)} fragments")

if __name__ == "__main__":
    main()
