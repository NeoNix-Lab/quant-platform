#!/usr/bin/env python3
"""Operator CLI: Wave 5 Golden E2E supervised ML proof (issue #174).

Executes two independent deterministic supervised training/evaluation runs over
bounded canonical Bybit BTCUSDT fixture evidence and reports the stable I04/I05
identities.  All proof composition lives in
``quant_platform.application.golden_supervised``; this script only parses CLI
input and prints evidence, per the application-seam rule.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    DEFAULT_GOLDEN_FIXTURE,
    run_wave5_golden_supervised_proof,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_GOLDEN_FIXTURE))
    parser.add_argument("--code-ref", default="wave5-golden-supervised-e2e-v1")
    parser.add_argument("--execution-id", default="wave5-golden-supervised-e2e")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete proof summary as canonical pretty JSON",
    )
    args = parser.parse_args(argv)

    proof = run_wave5_golden_supervised_proof(
        fixture_path=args.fixture,
        code_ref=args.code_ref,
        execution_id=args.execution_id,
    )
    summary = proof.stable_dict()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Source evidence:      {proof.source_evidence_identity}")
        print(f"Projection identity:  {proof.projection.identity}")
        print(f"Run spec identity:    {proof.first.run_identity.run_spec_identity.fingerprint}")
        print(f"Normalizer identity:  {proof.first.normalizer.identity}")
        print(f"Model identity:       {proof.first.model.identity}")
        print(f"Accuracy:             {proof.first.metrics.accuracy}")
        print(f"Deterministic:        {proof.deterministic}")
        for registration in proof.first.artifact_registrations:
            print(
                f"Artifact {registration.identity.artifact_role}: "
                f"{registration.identity.artifact_content_identity.content_identity}"
            )

    if not proof.deterministic:
        print("FAIL: supervised proof is not bitwise-deterministic across runs.", file=sys.stderr)
        return 1
    if summary["sample_counts"]["train"] == 0 or summary["sample_counts"]["test"] == 0:
        print("FAIL: supervised proof is vacuous; train and test samples are required.", file=sys.stderr)
        return 1
    print("PASS: supervised Golden E2E identities are deterministic across independent runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
