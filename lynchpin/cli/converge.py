"""Plan or execute bounded read convergence for scheduled maintenance."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from ..materialization import plan_read_convergence, run_read_convergence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or execute bounded Lynchpin read convergence")
    parser.add_argument("--product", default="evidence_graph_substrate")
    parser.add_argument("--start", required=True, help="inclusive window start, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="exclusive window end, YYYY-MM-DD")
    parser.add_argument("--execute", action="store_true", help="execute the plan and record a receipt")
    parser.add_argument("--caller", default="scheduled_convergence")
    parser.add_argument("--json", action="store_true", help="emit one compact JSON object")
    args = parser.parse_args(argv)

    window = (date.fromisoformat(args.start), date.fromisoformat(args.end))
    if window[1] <= window[0]:
        parser.error("--end must be after --start")
    plan = plan_read_convergence(product=args.product, window=window)
    payload: dict[str, object] = {"plan": plan.to_json()}
    if args.execute:
        payload = run_read_convergence(plan, caller=args.caller)
    if args.json:
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    else:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    result_payload = payload.get("result")
    result_status = result_payload.get("status") if isinstance(result_payload, dict) else plan.action
    return 0 if result_status not in {"failed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
