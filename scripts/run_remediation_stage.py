#!/usr/bin/env python3
"""Remediation stage orchestration (Kiro Crew DAG `remediation` node).

This script is the transport/looping glue for the DAG's `remediation`
node. The YAML schema (`agents:`/`{agent, timeout, depends_on,
description, prompt, shell}`) has no conditional-execution key, so a
`remediation` node that `depends_on: [aggregate-review]` always runs, even
along the SAFE_TO_SHIP (no findings) path. This script is what makes that
safe: it is a no-op, by design, whenever there is nothing to remediate.

Responsibilities (transport only — no SEC/REL policy, no PASS/FAIL logic,
no severity computation, no remediation decision-making of its own):

    1. Look for `--blocked-input` (the CHANGE_BLOCKED aggregation written
       by `scripts/aggregate_review.py`). If it does not exist, the prior
       aggregation stage already produced SAFE_TO_SHIP directly and wrote
       it to the final verdict path — there is nothing approved to
       remediate, so this script writes a `"skipped"` result and exits 0
       without invoking any agent.
    2. If it exists, read its `findings` list. Every finding on this list
       has already been judged and its remediation implicitly approved by
       reaching this node — approval enforcement itself is out of scope
       for this script (it happens via the DAG task's `force_approval`
       gate, set post-decomposition per design.md's Kiro Crew mapping
       section, before this node is ever allowed to run). For each
       finding whose `rule_id` is one of the four ChangeGuard supports,
       invoke `kiro-cli chat --agent remediator --no-interactive` with a
       prompt containing ONLY that Finding's JSON (never raw plan JSON,
       never the full blocked-result payload). For any finding whose
       `rule_id` is not supported (including the synthetic
       INCOMPLETE/missing-result diagnostic entries `aggregate_review.py`
       can produce, which carry `rule_id: null`), record a `"refused"`
       entry without invoking any agent — this script performs that
       whitelist check itself, in addition to (not instead of) the
       Remediator agent's own refusal logic and `apply_remediation.py`'s
       own whitelist, as one more layer of the same defense-in-depth
       pattern already used elsewhere in this system.
    3. Atomically write the union of per-finding results to `--output`.

CLI contract:

    python3 scripts/run_remediation_stage.py \
        --blocked-input <path> --output <path> \
        [--terraform-dir <path>] [--timeout <seconds>]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

SUPPORTED_RULE_IDS = frozenset({"SEC-001", "SEC-002", "REL-001", "BR-001"})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_remediation_stage.py",
        description=(
            "No-op-safe remediation stage: invokes the remediator agent "
            "once per approved, supported finding. Transport only."
        ),
    )
    parser.add_argument("--blocked-input", required=True, help="Path to the CHANGE_BLOCKED aggregation JSON.")
    parser.add_argument("--output", required=True, help="Path to atomically write the remediation stage result to.")
    parser.add_argument("--terraform-dir", default="terraform", help="Terraform directory passed through to the Remediator's prompt context.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait per kiro-cli chat invocation.")
    return parser.parse_args(argv)


def _extract_json_object(stdout_text):
    """Return the parsed JSON object found in `stdout_text`, or raise ValueError."""
    stripped = stdout_text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace < first_brace:
        raise ValueError("no JSON object found in agent stdout")

    candidate = stripped[first_brace : last_brace + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"agent stdout did not contain valid JSON: {exc}") from exc


def _invoke_remediator(finding, terraform_dir, timeout):
    """Invoke the remediator agent for exactly one approved Finding.

    Returns the agent's parsed JSON response dict. Raises ValueError or
    propagates a subprocess error on failure; callers turn that into a
    `"remediation_failed"` entry rather than crashing the whole stage.
    """
    prompt = (
        f"Terraform directory: {terraform_dir}\n\n"
        "Remediate exactly this already-approved Finding "
        "(JSON object below, nothing else is approved):\n\n"
        f"{json.dumps(finding)}"
    )
    argv_list = ["kiro-cli", "chat", "--agent", "remediator", "--no-interactive", prompt]
    result = subprocess.run(argv_list, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ValueError(
            f"remediator invocation exited with code {result.returncode}: {result.stderr.strip()}"
        )
    return _extract_json_object(result.stdout)


def run_remediation_stage(blocked_input_path, terraform_dir, timeout):
    """Return the remediation stage's result dict for the given inputs."""
    if not os.path.isfile(blocked_input_path):
        return {
            "status": "skipped",
            "reason": (
                f"no CHANGE_BLOCKED input found at {blocked_input_path}; "
                "aggregate-review already reported SAFE_TO_SHIP directly, "
                "nothing to remediate"
            ),
            "results": [],
        }

    with open(blocked_input_path, "r") as blocked_file:
        blocked_payload = json.load(blocked_file)

    findings = blocked_payload.get("findings", [])
    results = []

    for finding in findings:
        rule_id = finding.get("rule_id")
        if rule_id not in SUPPORTED_RULE_IDS:
            results.append(
                {
                    "status": "refused",
                    "rule_id": rule_id,
                    "resource": finding.get("resource"),
                    "error": (
                        "Unsupported rule ID; ChangeGuard supports only "
                        "SEC-001, SEC-002, REL-001, and BR-001."
                    ),
                }
            )
            continue

        try:
            agent_result = _invoke_remediator(finding, terraform_dir, timeout)
        except (ValueError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            results.append(
                {
                    "status": "remediation_failed",
                    "rule_id": rule_id,
                    "resource": finding.get("resource"),
                    "error": str(exc),
                }
            )
            continue

        results.append(agent_result)

    overall_status = _summarize_status(results)

    return {"status": overall_status, "results": results}


def _summarize_status(results):
    """Roll up per-finding results into one observability-only status.

    Distinguishes "every finding failed" from "some findings failed" so
    an operator/judge does not have to open --output to tell them apart.
    This is purely descriptive: it has no bearing on the authoritative
    verdict, which always comes from the post-remediation reviewer
    re-review (final_verdict.py), never from this rollup.
    """
    if not results:
        return "noop"

    succeeded = sum(1 for entry in results if entry.get("status") == "remediated")
    if succeeded == len(results):
        return "remediated"
    if succeeded == 0:
        return "failed"
    return "partial"


def _atomic_write(payload, output_path):
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".run_remediation_stage_", suffix=".json.tmp", dir=output_dir)
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
    result = run_remediation_stage(args.blocked_input, args.terraform_dir, args.timeout)
    _atomic_write(result, args.output)
    print(json.dumps({"status": result["status"], "output": args.output}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
