#!/usr/bin/env bash
# Full retrospective pipeline: comprehensive on all quarters, then deep passes.
# Run sequentially to avoid subscription rate limits.
# Reuses cached artifacts — safe to restart at any point.

set -euo pipefail
cd /realm/project/sinity-lynchpin

CMD="python -m lynchpin.system.life_timeline workflow"
BACKEND="--backend claude-agent-sdk"

echo "[$(date)] Starting full retrospective pipeline"

# Phase 1: Comprehensive workflow on each quarter (reuses cached days/weeks)
for quarter in 2025-Q1 2025-Q2 2025-Q3 2025-Q4 2026-Q1; do
    echo ""
    echo "[$(date)] === Phase 1: comprehensive on $quarter ==="
    $CMD "$quarter" --workflow comprehensive --scale quarter $BACKEND || {
        echo "[$(date)] WARNING: $quarter comprehensive failed, continuing..."
    }
done

# Phase 2: Deep passes on each quarter (adds anomaly, dashboard, questions)
for quarter in 2025-Q1 2025-Q2 2025-Q3 2025-Q4 2026-Q1; do
    echo ""
    echo "[$(date)] === Phase 2: deep on $quarter ==="
    $CMD "$quarter" --workflow deep --scale quarter $BACKEND || {
        echo "[$(date)] WARNING: $quarter deep failed, continuing..."
    }
done

echo ""
echo "[$(date)] Full retrospective pipeline complete"
echo "Artifacts in: artefacts/retrospective/narratives/logs/"
