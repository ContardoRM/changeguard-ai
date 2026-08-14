#!/usr/bin/env python3
"""Post-remediation final verdict (Kiro Crew DAG `final-verdict` node).

Reads ONLY the two post-remediation re-review `ReviewResult` JSON files
(`security-remediated-review-result.json`,
`reliability-remediated-review-result.json`) and the remediated-plan
generation's own success/failure status, and writes
`artifacts/final-verdict.json`.

This script performs ONLY workflow-level aggregation, identical in kind to
`scripts/aggregate_review.py`'s pre-remediation aggregation -- no
SEC-001/SEC-002/REL-001/BR-001 rule re-evaluation happens here. It differs
from `aggregate_review.py` in exactly one respect: it is the DAG's last
node, so it is the one place responsible for producing the *authoritative,
user-facing* verdict, including the steering doc's SAFE_TO_SHIP
scope-limitation sentence (design.md Requirement 10.8; steering doc "Final
verdict"), rather than the early, minimal SAFE_TO_SHIP shortcut
`aggregate_review.py` writes when no remediation was ever needed.

Verdict rule (design.md Correctness Property 5; Requirement 10.3-10.6):

    SAFE_TO_SHIP only if:
        - `--plan-status` indicates the remediated Terraform plan
          generation succeeded (i.e. the `remediated-plan` DAG node's
          `run_tf_plan.py` invocation exited 0), AND
        - both the security and reliability re-review results have
          status == "PASS".
    Otherwise: a blocked verdict, carrying the union of both re-review
    results' findings (identical aggregation logic to
    `aggregate_review.py.aggregate`), so the caller can see exactly which
    rule(s) still fail even after remediation.

CLI contract:

    python3 scripts/final_verdict.py \
        --security <path> --reliability <path> \
        --plan-status <success|failure> \
        --output artifacts/final-verdict.json \
        [--scope SEC-001,SEC-002,REL-001,BR-001]
"""

import argparse
import json
import os
import sys
import tempfile

# Re-implemented rather than imported from aggregate_review.py so this
# script has no import-time dependency on that module's CLI-argument
# parsing side effects; the aggregation logic itself is intentionally
# identical (see module docstring).
from aggregate_review import _load_review_result, aggregate, DEFAULT_SCOPE  # noqa: E402

# The exact scope-limitation sentence from
# .kiro/steering/changeguard-principles.md's "Final verdict" section,
# restated here verbatim rather than re-derived.
SAFE_TO_SHIP_SCOPE_NOTE = (
    "SAFE_TO_SHIP means only that the candidate passed the supported "
    "ChangeGuard MVP rules. It does not mean the infrastructure is "
    "universally safe or production-ready."
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="final_verdict.py",
        description=(
            "Produce the authoritative final ChangeGuard verdict from the "
            "post-remediation re-review results. No SEC/REL rule "
            "evaluation of its own."
        ),
    )
    parser.add_argument("--security", required=True, help="Path to the post-remediation Security Reviewer ReviewResult JSON.")
    parser.add_argument("--reliability", required=True, help="Path to the post-remediation Reliability Reviewer ReviewResult JSON.")
    parser.add_argument(
        "--plan-status",
        required=True,
        choices=["success", "failure"],
        help="Whether the remediated-plan DAG node's Terraform execution succeeded.",
    )
    parser.add_argument("--output", required=True, help="Path to atomically write the final verdict JSON to.")
    parser.add_argument("--scope", default=",".join(DEFAULT_SCOPE), help="Comma-separated rule IDs this MVP supports.")
    return parser.parse_args(argv)


def build_final_verdict(security_result, reliability_result, plan_succeeded, scope):
    """Return the final verdict dict for the given inputs.

    A Terraform plan execution failure independently blocks SAFE_TO_SHIP
    (Requirement 10.6), regardless of what either reviewer reported --
    checked first, before even looking at the reviewer results, so a plan
    failure can never be masked by two otherwise-PASS reviewer results.
    """
    if not plan_succeeded:
        return {
            "status": "CHANGE_BLOCKED",
            "findings": [
                {
                    "rule_id": None,
                    "reviewer": "terraform-plan-tool",
                    "status": "ERROR",
                    "reason": "remediated-plan Terraform execution did not succeed; SAFE_TO_SHIP cannot be reported without a genuine remediated plan.",
                }
            ],
        }

    is_safe, findings = aggregate(security_result, reliability_result)
    if is_safe:
        return {"status": "SAFE_TO_SHIP", "scope": scope, "scope_note": SAFE_TO_SHIP_SCOPE_NOTE, "findings": []}

    return {"status": "CHANGE_BLOCKED", "findings": findings}


def _atomic_write(payload, output_path):
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".final_verdict_", suffix=".json.tmp", dir=output_dir)
    try:
        with os.fdopen(fd, "w") as tmp_file:
            json.dump(payload, tmp_file, indent=2)
            tmp_file.write("\n")
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def main(argv=None):
    args = parse_args(argv)
    scope = [rule_id.strip() for rule_id in args.scope.split(",") if rule_id.strip()]

    security_result = _load_review_result(args.security, "security-reviewer")
    reliability_result = _load_review_result(args.reliability, "reliability-reviewer")

    verdict = build_final_verdict(
        security_result, reliability_result, args.plan_status == "success", scope
    )
    _atomic_write(verdict, args.output)
    print(json.dumps({"status": verdict["status"], "output": args.output}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
