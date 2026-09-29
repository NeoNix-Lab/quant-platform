#!/usr/bin/env python3
"""Operator CLI: Wave 6 Golden E2E bounded proof (issue #200).

Runs the application-owned Wave 6 proof composition over bounded fixture
evidence and temporary storage.  The CLI only parses arguments and prints
evidence; all orchestration lives in ``quant_platform.application.wave6_golden``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import run_wave6_golden_e2e_proof  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-ref", default="wave6-golden-e2e-v1")
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="skip the second deterministic run comparison",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete proof summary as canonical pretty JSON",
    )
    args = parser.parse_args(argv)

    proof = run_wave6_golden_e2e_proof(code_ref=args.code_ref, repeat=not args.single_run)
    summary = proof.stable_dict()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Proof identity:          {proof.proof_identity}")
        print(f"Candle equivalence:      {proof.candle_equivalence}")
        print(f"No disappearance:        {proof.no_consumer_visible_disappearance}")
        print(f"Deterministic:           {proof.deterministic}")
        print(f"Relocation phase:        {proof.relocation_record.phase.value}")
        print(f"Protected K09 decision:  {proof.protected_retention_decision.decision.value}")
        print(f"Permitted K09 decision:  {proof.permitted_retention_decision.decision.value}")
        print(f"Permitted bytes deleted: {proof.permitted_deletion_result.deleted}")

    if not proof.pass_:
        print("FAIL: Wave 6 Golden E2E bounded proof did not satisfy acceptance.", file=sys.stderr)
        return 1
    print("PASS: Wave 6 Golden E2E bounded proof satisfied B06/D04/K07/K09 acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
