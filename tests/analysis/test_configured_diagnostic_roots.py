"""Fresh process defaults and CLI overrides follow the configured data owner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_configured_machine_and_analysis_roots_reach_production_consumers(tmp_path):
    machine = tmp_path / "machine"
    analysis = tmp_path / "analysis"
    explicit = tmp_path / "retained-bundle"
    environment = dict(os.environ, LYNCHPIN_MACHINE_HOST_ROOT=str(machine),
                       LYNCHPIN_ANALYSIS_OUTPUT_DIR=str(analysis))
    code = r'''
import json
import runpy
import sys
from types import SimpleNamespace
from typer.testing import CliRunner
from lynchpin.analysis import cli
from lynchpin.analysis.machine import below
roots = []
def writer(out, *, root, top_n):
    roots.append(str(root))
    return SimpleNamespace(system=[])
cli.write_below_analysis = writer
for arguments in (["machine-below"], ["machine-below", "--root", sys.argv[1]]):
    result = CliRunner().invoke(cli.build_app(), arguments)
    assert result.exit_code == 0, result.output
cartography = runpy.run_path("scripts/cartography.py")
print(json.dumps({"cli": roots, "api": str(below.DEFAULT_STABILITY_ROOT),
                  "cartography": str(cartography["OUTPUT_ROOT"])}))
'''
    result = subprocess.run([sys.executable, "-c", code, str(explicit)],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["api"] == str(machine / "stability-lab")
    assert payload["cli"] == [str(machine / "stability-lab"), str(explicit)]
    assert payload["cartography"] == str(analysis / "cartography")
    assert not machine.exists() and not analysis.exists()
