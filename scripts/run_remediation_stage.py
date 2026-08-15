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
       generates a fresh, unique per-invocation `--result-file` path (see
       "Deterministic execution artifact" below) and invokes
       `kiro-cli chat --agent remediator --no-interactive` with a prompt
       containing ONLY that Finding's JSON and that exact path (never raw
       plan JSON, never the full blocked-result payload). For any finding
       whose `rule_id` is not supported (including the synthetic
       INCOMPLETE/missing-result diagnostic entries `aggregate_review.py`
       can produce, which carry `rule_id: null`), record a `"refused"`
       entry without invoking any agent — this script performs that
       whitelist check itself, in addition to (not instead of) the
       Remediator agent's own refusal logic and `apply_remediation.py`'s
       own whitelist, as one more layer of the same defense-in-depth
       pattern already used elsewhere in this system.
    3. Atomically write the union of per-finding results to `--output`.

Deterministic execution artifact, not chat stdout, is the authoritative
per-finding result (Phase 8B transport correction): a direct investigation
confirmed `kiro-cli` chat stdout is not a reliable machine-readable
transport — the same stream can carry human-readable narration, the
underlying `apply_remediation.py` subprocess's own echoed stdout, Kiro's
progress/completion UI text, and the final assistant response, so more
than one JSON-shaped fragment can legitimately appear in one transcript
even when the remediation itself succeeded cleanly. This script now
generates a fresh, unique `--result-file` path per finding (via
`tempfile.mkstemp`, so no fixed/reused filename could ever let a stale
artifact from a different invocation satisfy this one), instructs the
Remediator to pass that exact path to `apply_remediation.py`, and — after
the `kiro-cli` process returns — validates that artifact directly and
independently of the chat stdout: it must exist, parse as JSON, and have
`status == "remediated"` with `rule_id`, `resource`, and `restored_value`
matching the approved Finding's `rule_id`, `resource`, and
`baseline_value` exactly. `_extract_json_object`'s strict single-JSON-value
parsing of chat stdout is retained for diagnostics/logging only — it is
no longer the authoritative signal for whether a finding was remediated.

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
    """Return the single, unambiguous JSON object found in `stdout_text`, or
    raise ValueError.

    Root-cause note (Phase 8B correction, discovered via a real remediated
    live run): a naive "first `{` to last `}`" span is unsafe when the
    agent's chat stdout contains more than one brace-delimited block (e.g.
    the intended result object followed by an incidental second
    JSON-shaped block, or a restated/echoed fragment). Concatenating
    everything between the first `{` and the last `}` in that case yields
    a byte span that is *not* valid JSON on its own -- observed live as
    `json.JSONDecodeError: Extra data: line 2 column 1`, at the exact
    offset immediately following a legitimately-valid first object. The
    old code treated that decode failure as "no JSON found" and let the
    caller wrap it into a `remediation_failed` result entry, but
    `run_remediation_stage.py`'s `main()` still returned exit 0
    unconditionally regardless of that failure, so a Crew DAG node
    treated this as success and the workflow proceeded (the actual
    fail-open bug this correction closes -- see `main()`'s new exit-code
    behavior below).

    This function is corrected to require the extracted candidate span to
    contain EXACTLY one top-level JSON value: it decodes greedily from the
    first `{` using `json.JSONDecoder.raw_decode`, then verifies nothing
    but whitespace follows that one decoded value. If any additional
    non-whitespace content follows (a second JSON object, echoed text,
    anything), this is treated as an ambiguous/malformed response and
    rejected -- never silently truncated to "whichever came first."
    """
    stripped = stdout_text.strip()
    first_brace = stripped.find("{")
    if first_brace == -1:
        raise ValueError("no JSON object found in agent stdout")

    decoder = json.JSONDecoder()
    try:
        value, end_index = decoder.raw_decode(stripped, first_brace)
    except json.JSONDecodeError as exc:
        raise ValueError(f"agent stdout did not contain valid JSON: {exc}") from exc

    trailing = stripped[end_index:].strip()
    if trailing:
        raise ValueError(
            "agent stdout contained more than one JSON value (ambiguous "
            f"result); rejecting rather than guessing. Trailing content "
            f"after the first valid JSON object: {trailing[:200]!r}"
        )

    return value


def _make_result_file_path(terraform_dir):
    """Generate a fresh, unique per-invocation --result-file path under
    the `artifacts/` directory associated with `terraform_dir`.

    Phase 8C hardening: this path used to live under the system default
    `/tmp` directory. That was found to be an unnecessarily broad
    location -- combined with the Remediator agent's existing shell
    allow-list entry for `python3 scripts/apply_remediation.py *`, an
    unconfined `--result-file` path meant a maliciously crafted argument
    could in principle point `apply_remediation.py`'s delete-then-write
    logic at an arbitrary filesystem path. The path is now generated
    strictly inside `artifacts/` (the sibling directory of
    `terraform_dir`, i.e. `dirname(realpath(terraform_dir))/artifacts` --
    the same convention `apply_remediation.py`'s own
    `_resolve_allowed_artifacts_dir` independently re-derives and
    enforces from its side, and the same convention already used by
    `scripts/cleanup_run_artifacts.py`'s `--artifacts-dir` default and
    `scripts/changeguard_launch.py`'s defaults), using the fixed
    `.remediation-execution-<id>.json` filename prefix/suffix that
    `apply_remediation.py`'s `_RESULT_FILE_NAME_PATTERN` requires. This
    function is the sole generator of that path -- the Remediator agent
    never invents or chooses it, it only ever passes through the exact
    path given in its prompt (`.kiro/agents/remediator-prompt.md`).

    Uses `tempfile.mkstemp` (with `dir=artifacts_dir` and a matching
    prefix/suffix) so the path is guaranteed unique and cannot collide
    with any prior or concurrent invocation's artifact -- a stale result
    from a different finding/run can never be mistaken for this
    invocation's outcome. The file itself is immediately removed
    (mkstemp creates it) so `apply_remediation.py`'s own
    stale-artifact-clearing logic and atomic write are the only things
    that ever populate it; this function only reserves the unique path.
    """
    terraform_real = os.path.realpath(terraform_dir)
    artifacts_dir = os.path.realpath(os.path.join(os.path.dirname(terraform_real), "artifacts"))
    os.makedirs(artifacts_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(
        dir=artifacts_dir, prefix=".remediation-execution-", suffix=".json"
    )
    os.close(fd)
    os.remove(path)
    return path


def _validate_execution_artifact(result_file_path, finding):
    """Validate `apply_remediation.py`'s --result-file artifact directly.

    This is the authoritative success/failure signal for one finding
    (Phase 8B transport correction) -- independent of, and never inferred
    from, the Remediator agent's chat stdout or Terraform's resulting
    state. Returns the parsed, validated payload dict. Raises ValueError
    (turned into a `remediation_failed` entry by the caller) if the file
    is missing, unreadable, malformed, or any field does not exactly
    match the approved Finding's `rule_id`/`resource`/`baseline_value`.
    """
    if not os.path.isfile(result_file_path):
        raise ValueError(
            f"apply_remediation.py did not produce a --result-file artifact "
            f"at {result_file_path!r}; remediation cannot be confirmed successful"
        )

    try:
        with open(result_file_path, "r") as result_file:
            payload = json.load(result_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"--result-file artifact could not be read as JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("--result-file artifact did not contain a JSON object")

    if payload.get("status") != "remediated":
        raise ValueError(
            f"--result-file artifact reported status={payload.get('status')!r}, "
            f"not 'remediated'"
        )

    expected_rule_id = finding.get("rule_id")
    if payload.get("rule_id") != expected_rule_id:
        raise ValueError(
            f"--result-file artifact rule_id {payload.get('rule_id')!r} does not "
            f"match the approved Finding's rule_id {expected_rule_id!r}"
        )

    expected_resource = finding.get("resource")
    if payload.get("resource") != expected_resource:
        raise ValueError(
            f"--result-file artifact resource {payload.get('resource')!r} does not "
            f"match the approved Finding's resource {expected_resource!r}"
        )

    expected_restored_value = finding.get("baseline_value")
    if payload.get("restored_value") != expected_restored_value:
        raise ValueError(
            f"--result-file artifact restored_value {payload.get('restored_value')!r} "
            f"does not match the approved Finding's baseline_value {expected_restored_value!r}"
        )

    return payload


def _invoke_remediator(finding, terraform_dir, timeout, result_file_path):
    """Invoke the remediator agent for exactly one approved Finding.

    The agent's chat stdout is captured and parsed only for diagnostics
    (see `_extract_json_object`'s docstring) -- it is no longer the
    authoritative result. The authoritative result comes from validating
    `result_file_path` (`apply_remediation.py`'s own `--result-file`
    artifact) directly, via `_validate_execution_artifact`, after the
    `kiro-cli` process returns.

    Returns the validated execution-artifact payload dict. Raises
    ValueError or propagates a subprocess error on failure; callers turn
    that into a `"remediation_failed"` entry rather than crashing the
    whole stage.
    """
    prompt = (
        f"Terraform directory: {terraform_dir}\n\n"
        f"Result file: {result_file_path}\n\n"
        "Remediate exactly this already-approved Finding "
        "(JSON object below, nothing else is approved), passing the exact "
        "Result file path above as --result-file to apply_remediation.py:\n\n"
        f"{json.dumps(finding)}"
    )
    argv_list = ["kiro-cli", "chat", "--agent", "remediator", "--no-interactive", prompt]
    result = subprocess.run(argv_list, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ValueError(
            f"remediator invocation exited with code {result.returncode}: {result.stderr.strip()}"
        )

    # Chat stdout is parsed here only so a malformed/ambiguous response is
    # visible in diagnostics; its outcome does not gate success/failure.
    try:
        _extract_json_object(result.stdout)
    except ValueError:
        pass

    return _validate_execution_artifact(result_file_path, finding)


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

        result_file_path = _make_result_file_path(terraform_dir)
        try:
            agent_result = _invoke_remediator(finding, terraform_dir, timeout, result_file_path)
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
        finally:
            # The unique per-invocation artifact has already been read
            # and validated (or the finding already recorded as failed);
            # remove it so it can never be mistaken for a later
            # invocation's result, even though its unique filename would
            # already prevent that collision on its own.
            try:
                os.remove(result_file_path)
            except OSError:
                pass

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

    # Fail-closed exit code (Phase 8B correction): "skipped" (nothing
    # approved to remediate -- the SAFE_TO_SHIP-without-remediation path)
    # and "remediated" (every approved finding succeeded) are the only
    # two outcomes that may exit 0. "noop" (empty findings list on a
    # CHANGE_BLOCKED input -- should not normally occur, but is not a
    # success either), "partial", and "failed" all exit non-zero, so a
    # Crew DAG node treats this task itself as FAILED rather than PASSED
    # -- Crew's own dependency-failure propagation then blocks
    # `remediated-plan`/the re-review nodes from ever running on a truly
    # failed remediation, independent of (not instead of) the
    # final_verdict.py-level check below.
    return 0 if result["status"] in ("skipped", "remediated") else 1


if __name__ == "__main__":
    sys.exit(main())
