#!/usr/bin/env python3
"""Post-remediation final verdict (Kiro Crew DAG `final-verdict` node).

Reads the two post-remediation re-review `ReviewResult` JSON files
(`security-remediated-review-result.json`,
`reliability-remediated-review-result.json`), the remediated-plan
generation's own success/failure status, AND (Phase 8B correction, below)
the remediation stage's own result artifact
(`artifacts/remediation-result.json`, written by
`scripts/run_remediation_stage.py`), and writes `artifacts/final-verdict.json`.

**Phase 8B fail-closed correction:** a real live run demonstrated that
`remediated-plan.json` existing and both re-review reviewers returning
`PASS` is NOT sufficient evidence that remediation actually succeeded --
Terraform state can already reflect a prior successful `apply_remediation.py`
call even when the *remediator agent's own chat response* failed to parse
as valid JSON (observed live: `run_remediation_stage.py` reported
`{"status": "failed", ...}` in `remediation-result.json` while
`terraform/main.tf` had, in that specific run, already been correctly
mutated). Treating "the reviewers happen to agree the current state is
safe" as proof of a successful, contract-compliant remediation is exactly
the fail-open gap this correction closes: `final_verdict.py` now REQUIRES
`remediation-result.json` to exist, parse as JSON, and report
`status == "remediated"` (Requirement: every approved finding was
successfully remediated, not just some) before it will ever consider
emitting `SAFE_TO_SHIP` on the post-remediation path. A missing, malformed,
or non-"remediated" remediation result independently and unconditionally
blocks `SAFE_TO_SHIP`, regardless of what the plan status or either
reviewer reports.

This script performs ONLY workflow-level aggregation, identical in kind to
`scripts/aggregate_review.py`'s pre-remediation aggregation -- no
SEC-001/SEC-002/REL-001/BR-001 rule re-evaluation happens here. It differs
from `aggregate_review.py` in exactly one respect: it is the DAG's last
node, so it is the one place responsible for producing the *authoritative,
user-facing* verdict, including the steering doc's SAFE_TO_SHIP
scope-limitation sentence (design.md Requirement 10.8; steering doc "Final
verdict"), rather than the early, minimal SAFE_TO_SHIP shortcut
`aggregate_review.py` writes when no remediation was ever needed.

Verdict rule (design.md Correctness Property 5; Requirement 10.3-10.6; Phase
8B fail-closed correction):

    SAFE_TO_SHIP only if ALL of:
        - `--remediation-result` exists, parses as JSON, and its
          `status` field is exactly `"remediated"` (every approved
          finding was successfully remediated -- checked FIRST, before
          plan status or either reviewer, so it can never be masked by
          an otherwise-clean plan/PASS/PASS), AND
        - `--plan-status` indicates the remediated Terraform plan
          generation succeeded (i.e. the `remediated-plan` DAG node's
          `run_tf_plan.py` invocation exited 0), AND
        - both the security and reliability re-review results have
          status == "PASS".
    Otherwise: a blocked verdict. If the remediation-result check fails,
    the blocked verdict is `REMEDIATION_FAILED` (a distinct status from
    plain `CHANGE_BLOCKED`, so the caller can tell "the candidate still
    has findings" apart from "remediation itself did not complete
    successfully"). If it passes but plan-status or a reviewer fails, the
    blocked verdict remains `CHANGE_BLOCKED`, carrying the union of both
    re-review results' findings (identical aggregation logic to
    `aggregate_review.py.aggregate`), so the caller can see exactly which
    rule(s) still fail even after remediation.

CLI contract:

    python3 scripts/final_verdict.py \
        --security <path> --reliability <path> \
        --plan-status <success|failure> \
        --remediation-result artifacts/remediation-result.json \
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
    parser.add_argument(
        "--remediation-result",
        required=True,
        help=(
            "Path to run_remediation_stage.py's own result artifact "
            "(artifacts/remediation-result.json). Must exist, parse as "
            "JSON, and report status == 'remediated' before SAFE_TO_SHIP "
            "can ever be considered -- checked before plan status or "
            "either reviewer (Phase 8B fail-closed correction)."
        ),
    )
    parser.add_argument("--output", required=True, help="Path to atomically write the final verdict JSON to.")
    parser.add_argument("--scope", default=",".join(DEFAULT_SCOPE), help="Comma-separated rule IDs this MVP supports.")
    return parser.parse_args(argv)


def _load_remediation_result(path):
    """Load run_remediation_stage.py's result artifact.

    Returns (status_or_none, error_message_or_none). `status_or_none` is
    the parsed `status` field on success, or None if the file is
    missing, unreadable, malformed, or lacks a usable `status` field --
    in every one of those cases `error_message_or_none` describes why,
    for inclusion in a REMEDIATION_FAILED finding's `reason`.

    Never raises: any failure here is workflow-level plumbing
    information, exactly like `aggregate_review.py::_load_review_result`'s
    treatment of an unreadable reviewer result.
    """
    if not os.path.isfile(path):
        return None, f"remediation result file not found: {path}"

    try:
        with open(path, "r") as result_file:
            payload = json.load(result_file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"remediation result file could not be read as JSON: {exc}"

    if not isinstance(payload, dict) or "status" not in payload:
        return None, "remediation result file did not contain a recognizable remediation-stage result"

    return payload["status"], None


def build_final_verdict(security_result, reliability_result, plan_succeeded, remediation_status, remediation_error, scope):
    """Return the final verdict dict for the given inputs.

    Check order (each an independent, unconditional block on
    SAFE_TO_SHIP -- none can be overridden by the others being clean):

        1. remediation_status == "remediated" -- Phase 8B fail-closed
           correction. Checked FIRST: a failed/missing/malformed
           remediation result must never be masked by an otherwise-clean
           plan status or two PASS reviewer results, because Terraform
           state can already reflect a prior successful mutation even
           when the remediator agent's own chat-response contract failed
           (observed live -- see module docstring). Failing this check
           produces a distinct `REMEDIATION_FAILED` status, not plain
           `CHANGE_BLOCKED`, so the caller can tell "remediation itself
           did not complete successfully" apart from "the candidate
           still has findings."
        2. plan_succeeded -- a Terraform plan execution failure
           independently blocks SAFE_TO_SHIP (Requirement 10.6).
        3. both reviewers PASS.
    """
    if remediation_status != "remediated":
        reason = (
            remediation_error
            or f"remediation-result.json reported status={remediation_status!r}, not 'remediated'"
        )
        return {
            "status": "REMEDIATION_FAILED",
            "findings": [
                {
                    "rule_id": None,
                    "reviewer": "run_remediation_stage",
                    "status": "ERROR",
                    "reason": (
                        "Remediation did not complete successfully; SAFE_TO_SHIP "
                        f"cannot be reported without a validated remediation result. {reason}"
                    ),
                }
            ],
        }

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
    remediation_status, remediation_error = _load_remediation_result(args.remediation_result)

    verdict = build_final_verdict(
        security_result, reliability_result, args.plan_status == "success",
        remediation_status, remediation_error, scope,
    )
    _atomic_write(verdict, args.output)
    print(json.dumps({"status": verdict["status"], "output": args.output}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
