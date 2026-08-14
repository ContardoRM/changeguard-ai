#!/usr/bin/env python3
"""Workflow-level review aggregation (Kiro Crew DAG `aggregate-review` node).

This script performs ONLY workflow-level status aggregation over two
already-produced `ReviewResult` JSON files (one from the Security Reviewer,
one from the Reliability Reviewer). It never re-evaluates SEC-001,
SEC-002, REL-001, or BR-001 itself, and it never reads Terraform plan JSON
(`artifacts/*-plan.json`) — it only reads the two reviewer output files
named on the command line, exactly matching the Orchestrator's permission
boundary in design.md ("coordination-only... never reads plan JSON to
make a rule decision").

Aggregation rule (design.md "Human Approval Gate" / "Correctness
Properties" Property 5, requirements.md Requirement 4.3):

    - If both reviewers returned status "PASS": there is nothing to
      remediate. Write `--pass-output` (the final verdict) directly as
      exactly `{"status": "SAFE_TO_SHIP", "scope": [...]}`, remove any
      pre-existing `--blocked-output` file left over from a previous run
      (a stale `change-blocked-result.json` from an earlier blocked run
      must never be mistaken for this safe candidate's outcome by a
      downstream consumer), and exit 0. This early SAFE_TO_SHIP is
      intentionally minimal (no scope-note prose) -- the workflow's
      authoritative, user-facing SAFE_TO_SHIP message, including the
      steering doc's scope-limitation sentence, is produced by the DAG's
      later `final-verdict` node (`scripts/final_verdict.py`) on the
      post-remediation path. Stage B (remediation onward) is only ever
      planned by `scripts/changeguard_launch.py` when `--blocked-output`
      exists on disk after this stage runs -- a safe candidate never has
      a `remediation` task decomposed at all, so there is nothing for a
      stale artifact, or anything else, to trigger.
    - Otherwise (either reviewer returned "FAIL", "INCOMPLETE", or its
      result file was missing/unreadable/malformed): write
      `--blocked-output` as `{"status": "CHANGE_BLOCKED", "findings":
      [...]}`, the union of every FAIL finding from either reviewer, plus
      a synthetic diagnostic entry (never a fabricated rule_id) for any
      reviewer whose result could not be read or was INCOMPLETE.

This is a pure union/aggregation of already-produced verdicts — it adds no
severity, no new rule_id, and no judgment about whether a given finding
"should" have been raised. That judgment already happened inside the
Security Reviewer / Reliability Reviewer agents.

CLI contract:

    python3 scripts/aggregate_review.py \
        --security <path> --reliability <path> \
        --pass-output <path> --blocked-output <path> \
        [--scope SEC-001,SEC-002,REL-001,BR-001]
"""

import argparse
import json
import os
import sys
import tempfile

DEFAULT_SCOPE = ["SEC-001", "SEC-002", "REL-001", "BR-001"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="aggregate_review.py",
        description=(
            "Aggregate two ReviewResult JSON files into either a direct "
            "SAFE_TO_SHIP final verdict or a CHANGE_BLOCKED union of "
            "findings. Performs no SEC/REL rule evaluation of its own."
        ),
    )
    parser.add_argument("--security", required=True, help="Path to the Security Reviewer's ReviewResult JSON.")
    parser.add_argument("--reliability", required=True, help="Path to the Reliability Reviewer's ReviewResult JSON.")
    parser.add_argument("--pass-output", required=True, help="Path to write the final verdict to on SAFE_TO_SHIP.")
    parser.add_argument("--blocked-output", required=True, help="Path to write the CHANGE_BLOCKED aggregation to.")
    parser.add_argument(
        "--scope",
        default=",".join(DEFAULT_SCOPE),
        help="Comma-separated rule IDs this MVP supports (for the SAFE_TO_SHIP scope field).",
    )
    return parser.parse_args(argv)


def _load_review_result(path, reviewer_label):
    """Load a ReviewResult JSON file, or return a synthetic INCOMPLETE-shaped
    diagnostic dict if the file is missing, unreadable, or malformed.

    Never raises: any failure here is workflow-level plumbing information
    ("this reviewer's output could not be read"), not a rule judgment, so
    it is reported the same way a reviewer's own INCOMPLETE would be.
    """
    if not os.path.isfile(path):
        return {
            "status": "INCOMPLETE",
            "findings": [],
            "error": f"{reviewer_label} result file not found: {path}",
        }

    try:
        with open(path, "r") as result_file:
            payload = json.load(result_file)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "INCOMPLETE",
            "findings": [],
            "error": f"{reviewer_label} result file could not be read as JSON: {exc}",
        }

    if not isinstance(payload, dict) or "status" not in payload:
        return {
            "status": "INCOMPLETE",
            "findings": [],
            "error": f"{reviewer_label} result file did not contain a recognizable ReviewResult",
        }

    payload.setdefault("findings", [])
    return payload


def aggregate(security_result, reliability_result):
    """Return (is_safe, findings) for two loaded ReviewResult dicts.

    `is_safe` is True only when both statuses are exactly "PASS".
    `findings` is the union of both reviewers' `findings` lists, plus a
    synthetic diagnostic entry for any reviewer whose status was not
    "PASS" and not "FAIL" (i.e. "INCOMPLETE" or an unreadable result).
    """
    findings = []
    is_safe = True

    for label, result in (("security-reviewer", security_result), ("reliability-reviewer", reliability_result)):
        status = result.get("status")
        if status == "PASS":
            continue
        is_safe = False
        if status == "FAIL":
            findings.extend(result.get("findings", []))
        else:
            findings.append(
                {
                    "rule_id": None,
                    "reviewer": label,
                    "status": status or "UNKNOWN",
                    "reason": result.get("error", f"{label} did not return PASS or FAIL"),
                }
            )

    return is_safe, findings


def _remove_if_exists(path):
    """Delete `path` if it exists, using stdlib os.remove only.

    Absence of the file is not an error (it means there was nothing stale
    to clean up). No shell `rm`, no recursive deletion -- this removes
    exactly the one named file passed in, nothing else.
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _atomic_write(payload, output_path):
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".aggregate_review_", suffix=".json.tmp", dir=output_dir)
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

    is_safe, findings = aggregate(security_result, reliability_result)

    if is_safe:
        # Exact minimal shape per the DAG contract: {"status": "SAFE_TO_SHIP",
        # "scope": [...]}. See module docstring's note on final-verdict.py
        # owning the authoritative, scope-noted user-facing message.
        _atomic_write({"status": "SAFE_TO_SHIP", "scope": scope}, args.pass_output)
        # A safe candidate must never let a previous run's stale
        # CHANGE_BLOCKED artifact linger where a downstream consumer
        # (scripts/changeguard_launch.py deciding whether to plan Stage B)
        # could mistake it for this run's outcome.
        _remove_if_exists(args.blocked_output)
        print(json.dumps({"status": "SAFE_TO_SHIP", "output": args.pass_output}))
        return 0

    # Atomic replace (temp file + os.replace, same as _atomic_write always
    # does) -- a genuinely blocked result always overwrites whatever was
    # at --blocked-output before, never appends to or merges with it.
    _atomic_write(
        {"status": "CHANGE_BLOCKED", "findings": findings},
        args.blocked_output,
    )
    print(json.dumps({"status": "CHANGE_BLOCKED", "output": args.blocked_output, "finding_count": len(findings)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
