#!/usr/bin/env python3
"""Reviewer ReviewResult artifact transport (Kiro CLI reviewer agent tool).

This script is the reviewer-side analogue of `apply_remediation.py`'s
`--result-file` mechanism. It exists because a live ChangeGuard Control
Room smoke test confirmed the same root cause already documented for the
Remediator (design.md "Phase 8B transport correction"): `kiro-cli chat`
stdout is a presentation stream, not an authoritative machine-readable
transport. A reviewer's stdout can legitimately contain more than one
JSON-shaped fragment in one transcript -- the evidence-extraction tool's
own JSON output (`print_security_evidence.py`/`print_reliability_evidence.py`),
Kiro's progress/narration text, and the reviewer's final `ReviewResult`
JSON -- so no "first JSON" / "last JSON" / brace-span heuristic applied to
that stdout can be relied upon as proof of a reviewer's actual verdict.

This script is the ONLY mechanism a reviewer agent uses to persist its
`ReviewResult`. The reviewer agent (`security-reviewer` or
`reliability-reviewer`) invokes it exactly once, per its own prompt
contract, passing its final `ReviewResult` JSON object verbatim on stdin
(never as a command-line argument, to avoid shell-quoting fragility for
arbitrary error text). This script then:

    1. Validates the target `--output` path is confined to the expected
       `--artifacts-dir` and matches the fixed internal filename pattern
       (`.review-result-<id>.json`) -- the same path-confinement discipline
       `apply_remediation.py::_validate_result_file_path` already applies
       to `--result-file`. `run_agent_and_save.py` is the sole generator
       of this path; the reviewer agent only ever passes through the
       exact path it is given.
    2. Parses stdin as JSON.
    3. Validates the `ReviewResult`'s STRUCTURE ONLY:
         - `agent` matches the `--agent` this script was invoked for
           (identity check -- a security-reviewer cannot masquerade as a
           reliability-reviewer's result, or vice versa);
         - `status` is exactly one of `PASS`, `FAIL`, `INCOMPLETE`;
         - `PASS` requires `findings == []`;
         - `INCOMPLETE` requires `findings == []` and a non-empty string
           `error`;
         - `FAIL` requires a non-empty `findings` list, and every
           finding's `rule_id` is one of the exact rule IDs that agent is
           permitted to report (`SEC-001`/`SEC-002` for security-reviewer,
           `REL-001`/`BR-001` for reliability-reviewer).
    4. Atomically writes the validated payload to `--output`.

This script NEVER evaluates a Terraform plan value, NEVER decides whether
a fact pattern satisfies SEC-001/SEC-002/REL-001/BR-001, and NEVER
computes PASS/FAIL/INCOMPLETE itself. That policy judgment already
happened entirely inside the reviewer agent before it ever invoked this
script -- this script only confirms the *shape* of the judgment the agent
already made is well-formed and within that agent's permitted scope.
Rejecting a structurally invalid or out-of-scope payload is a schema/
identity/whitelist check, not a policy re-evaluation, exactly analogous to
`apply_remediation.py`'s own rule-ID whitelist and
`run_remediation_stage.py`'s finding-field validation.

CLI contract:

    python3 scripts/write_review_result.py \
        --agent <security-reviewer|reliability-reviewer> \
        --output <path> \
        [--artifacts-dir artifacts]
        < review_result.json

On success: writes the validated JSON payload to `--output` and exits 0.
On any failure (bad path, unreadable/malformed stdin, schema violation):
writes nothing to `--output` and exits non-zero with a diagnostic on
stderr -- fail-closed, mirroring every other deterministic transport
script in this repository.
"""

import argparse
import json
import os
import re
import sys
import tempfile

# Fixed whitelist: which rule IDs each reviewer agent is permitted to
# report. This is an identity/scope check, not a policy re-evaluation --
# it never asks whether a given rule_id's condition is actually satisfied
# by any Terraform value, only whether this agent is the one allowed to
# report that rule ID at all.
ALLOWED_RULE_IDS_BY_AGENT = {
    "security-reviewer": frozenset({"SEC-001", "SEC-002"}),
    "reliability-reviewer": frozenset({"REL-001", "BR-001"}),
}

VALID_STATUSES = frozenset({"PASS", "FAIL", "INCOMPLETE"})

# Fixed internal artifact filename pattern -- only `run_agent_and_save.py`
# generates paths matching this (see its `_make_internal_review_result_path`);
# a reviewer agent only ever passes through the exact path it was given.
# Mirrors `apply_remediation.py`'s `_RESULT_FILE_NAME_PATTERN` convention.
_INTERNAL_ARTIFACT_NAME_PATTERN = re.compile(r"^\.review-result-[A-Za-z0-9_-]+\.json$")


class ReviewResultValidationError(Exception):
    """Raised for any path-confinement or schema validation failure.

    Every raise site in this module occurs strictly before the single
    atomic write at the end of `main()` -- a `ReviewResultValidationError`
    always means nothing was written to `--output`.
    """


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="write_review_result.py",
        description=(
            "Validate and atomically persist a reviewer agent's own "
            "ReviewResult JSON (read from stdin) to a path-confined "
            "internal artifact. Structure/identity/scope validation "
            "only -- never evaluates Terraform values or computes "
            "PASS/FAIL/INCOMPLETE itself."
        ),
    )
    parser.add_argument(
        "--agent",
        required=True,
        choices=sorted(ALLOWED_RULE_IDS_BY_AGENT),
        help="The reviewer agent this ReviewResult is being persisted for.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help=(
            "Path to atomically write the validated ReviewResult to. "
            "Must be generated by run_agent_and_save.py and confined to "
            "--artifacts-dir with the fixed '.review-result-<id>.json' "
            "filename pattern."
        ),
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Directory --output must resolve strictly inside (default: artifacts).",
    )
    return parser.parse_args(argv)


def _resolve_allowed_artifacts_dir(artifacts_dir):
    return os.path.realpath(artifacts_dir)


def _validate_output_path(output_path, artifacts_dir):
    """Validate `output_path` is confined to `artifacts_dir` with the
    required filename pattern, or raise `ReviewResultValidationError`.

    Resolves both sides with `os.path.realpath` first (defeating `../`
    traversal and symlink escape alike), exactly mirroring
    `apply_remediation.py::_validate_result_file_path`. Returns the
    resolved, canonical path on success.
    """
    allowed_dir = _resolve_allowed_artifacts_dir(artifacts_dir)
    resolved_path = os.path.realpath(output_path)

    basename = os.path.basename(resolved_path)
    if not _INTERNAL_ARTIFACT_NAME_PATTERN.fullmatch(basename):
        raise ReviewResultValidationError(
            f"--output {output_path!r} does not match the required "
            f"'.review-result-<id>.json' filename pattern; refusing to touch it."
        )

    try:
        common = os.path.commonpath([allowed_dir, resolved_path])
    except ValueError:
        common = None
    if common != allowed_dir or resolved_path == allowed_dir:
        raise ReviewResultValidationError(
            f"--output {output_path!r} resolves to {resolved_path!r}, which "
            f"is not strictly inside the expected artifacts directory "
            f"{allowed_dir!r}; refusing to touch it."
        )

    return resolved_path


def validate_review_result_schema(payload, agent):
    """Validate `payload`'s STRUCTURE ONLY for the given reviewer `agent`.

    Returns `payload` unchanged on success. Raises
    `ReviewResultValidationError` on any structural, identity, or scope
    violation. This function never inspects a Terraform plan, never
    computes a PASS/FAIL/INCOMPLETE verdict, and never decides whether a
    finding's underlying condition is real -- it only confirms the
    already-produced verdict's shape is well-formed and within the given
    agent's permitted rule-ID scope.
    """
    if not isinstance(payload, dict):
        raise ReviewResultValidationError("ReviewResult must be a JSON object")

    if payload.get("agent") != agent:
        raise ReviewResultValidationError(
            f"ReviewResult 'agent' field {payload.get('agent')!r} does not "
            f"match the expected agent identity {agent!r}"
        )

    status = payload.get("status")
    if status not in VALID_STATUSES:
        raise ReviewResultValidationError(
            f"ReviewResult 'status' {status!r} is not one of {sorted(VALID_STATUSES)}"
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ReviewResultValidationError("ReviewResult 'findings' must be a list")

    allowed_rule_ids = ALLOWED_RULE_IDS_BY_AGENT[agent]

    if status == "PASS":
        if findings != []:
            raise ReviewResultValidationError(
                "ReviewResult status is 'PASS' but 'findings' is not empty; "
                "PASS requires an empty findings list"
            )
    elif status == "INCOMPLETE":
        if findings != []:
            raise ReviewResultValidationError(
                "ReviewResult status is 'INCOMPLETE' but 'findings' is not "
                "empty; INCOMPLETE requires an empty findings list"
            )
        error = payload.get("error")
        if not isinstance(error, str) or not error.strip():
            raise ReviewResultValidationError(
                "ReviewResult status is 'INCOMPLETE' but 'error' is missing "
                "or empty; INCOMPLETE requires a non-empty 'error' string"
            )
    else:  # FAIL
        if not findings:
            raise ReviewResultValidationError(
                "ReviewResult status is 'FAIL' but 'findings' is empty; "
                "FAIL requires a non-empty findings list"
            )
        for finding in findings:
            if not isinstance(finding, dict):
                raise ReviewResultValidationError(
                    f"every finding must be a JSON object, got {finding!r}"
                )
            rule_id = finding.get("rule_id")
            if rule_id not in allowed_rule_ids:
                raise ReviewResultValidationError(
                    f"finding rule_id {rule_id!r} is not one of the rule IDs "
                    f"{agent} is permitted to report: {sorted(allowed_rule_ids)}"
                )

    return payload


def _atomic_write_json(payload, output_path):
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".write_review_result_", suffix=".json.tmp", dir=output_dir
    )
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

    try:
        resolved_output = _validate_output_path(args.output, args.artifacts_dir)
    except ReviewResultValidationError as exc:
        print(f"write_review_result.py: {exc}", file=sys.stderr)
        return 1

    try:
        raw_stdin = sys.stdin.read()
    except Exception as exc:  # noqa: BLE001 - fail closed on any read error
        print(f"write_review_result.py: could not read stdin: {exc}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw_stdin)
    except json.JSONDecodeError as exc:
        print(f"write_review_result.py: stdin did not parse as JSON: {exc}", file=sys.stderr)
        return 1

    try:
        validate_review_result_schema(payload, args.agent)
    except ReviewResultValidationError as exc:
        print(f"write_review_result.py: {exc}", file=sys.stderr)
        return 1

    _atomic_write_json(payload, resolved_output)
    print(json.dumps({"status": "saved", "agent": args.agent, "output": resolved_output}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
