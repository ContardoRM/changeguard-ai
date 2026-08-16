#!/usr/bin/env python3
"""Kiro Crew DAG transport utility: run one Kiro CLI agent, save its JSON.

This script is the minimum glue needed between a Kiro Crew YAML DAG `shell`
node and an existing Kiro CLI custom agent (`security-reviewer`,
`reliability-reviewer`, or `remediator`). It exists because two confirmed
facts about the installed Kiro Crew 0.2.0 runtime make plain shell
redirection (`kiro-cli chat ... > artifacts/x.json`) insufficient on its
own:

    1. A completed DAG task's `Task.result` text is never automatically
       injected into a dependent task's prompt (`task_executor.py::
       build_task_prompt` only ever shows plan titles/descriptions/the
       dependency graph). The only working data-flow channel between DAG
       nodes is the shared `run.work_dir` filesystem, so every agent
       invocation's output must land at a fixed, explicitly-named JSON
       file path that downstream nodes are told to read by path.
    2. `kiro-cli chat --no-interactive` is an LLM chat session, not a pure
       JSON-emitting function call: its stdout is not contractually
       guaranteed to contain nothing but the agent's final JSON message.
       A live Control Room smoke test confirmed this concretely for the
       reviewer agents (see "Reviewer result transport correction"
       below): a reviewer's stdout legitimately contains the
       evidence-extraction tool's own JSON output, Kiro's
       progress/narration text, AND the reviewer's final ReviewResult
       JSON, all in one stream. No "first JSON" / "last JSON" / brace-span
       heuristic applied to that combined stdout can be relied upon as
       proof of the agent's actual verdict.

Reviewer result transport correction (analogous to the Remediator's own
`--result-file` mechanism, design.md "Phase 8B transport correction"):
for `--agent security-reviewer`/`--agent reliability-reviewer`, this
script no longer derives the reviewer's result by parsing chat stdout at
all. Instead:

    (i)   Generate a fresh, unique internal artifact path (confined to
          the same directory as the durable `--output` path, named with
          the fixed `.review-result-<id>.json` pattern
          `scripts/write_review_result.py` requires) and instruct the
          agent, via the prompt, to invoke
          `python3 scripts/write_review_result.py --agent <name> --output
          <internal path>` exactly once, passing its final ReviewResult
          JSON on stdin.
    (ii)  Run `kiro-cli chat --agent <name> --no-interactive "<prompt>"`
          as a subprocess (argv list, never a shell string). Its stdout
          is captured for diagnostics only.
    (iii) After the process returns, validate the internal artifact
          DIRECTLY: it must exist, parse as JSON, and pass
          `write_review_result.validate_review_result_schema` for that
          exact agent identity — independent of, and never inferred from,
          the chat stdout.
    (iv)  On success, atomically publish the validated payload to the
          durable `--output` path (e.g. `artifacts/security-review-result.json`)
          and remove the internal artifact. On any failure, write nothing
          to `--output`, remove the internal artifact if present, and
          exit non-zero.

For any other `--agent` value (kept for backward compatibility; no
current DAG node invokes this script with `--agent remediator` — the
Remediator has its own separate, already-corrected transport via
`scripts/run_remediation_stage.py` / `scripts/apply_remediation.py`'s
`--result-file`), this script falls back to the original stdout-parsing
behavior: validate that stdout parses as JSON (tolerating leading/
trailing non-JSON text around a single top-level `{...}` object) and
atomically write that JSON to `--output`.

This script contains NO SEC-001/SEC-002/REL-001/BR-001 rule logic, no
PASS/FAIL/INCOMPLETE logic, no severity computation, and no remediation
decision-making of any kind. It does not know what agent it is running for
beyond the literal `--agent` string it was given, and — for the reviewer
transport above — it validates only the STRUCTURE of the agent's own
ReviewResult (identity, status enum, findings shape, permitted rule IDs
for that agent), never the truth of any Terraform value. All policy
judgment happens entirely inside the invoked Kiro CLI agent
(`security-reviewer`, `reliability-reviewer`, or `remediator`), per their
own prompt-level policy documents; this script is transport only.

CLI contract:

    python3 scripts/run_agent_and_save.py \
        --agent <security-reviewer|reliability-reviewer|remediator> \
        --prompt "<literal prompt text>" \
        --output <path>

On success: writes the agent's validated JSON result to `--output` and
exits 0. On any failure, writes nothing to `--output` and exits non-zero
with a diagnostic on stderr, mirroring `scripts/run_tf_plan.py`'s
fail-fast contract.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import write_review_result  # noqa: E402

# Reviewer agents use the corrected artifact-based transport
# (write_review_result.py); every other agent value falls back to the
# original stdout-parsing behavior below.
REVIEWER_AGENTS = frozenset(write_review_result.ALLOWED_RULE_IDS_BY_AGENT)

# Defense-in-depth allow-list: every `shell:` command in the YAML DAG
# passes a fixed, non-interpolated `--agent` literal today, so this is a
# secondary guard (not the only thing preventing an arbitrary agent name),
# consistent with the same defense-in-depth pattern used by
# apply_remediation.py's rule-ID whitelist and safety_guard.py's pattern
# list elsewhere in this codebase. Rejecting here happens BEFORE any
# subprocess is started -- no `kiro-cli` invocation occurs for a
# disallowed name.
ALLOWED_AGENTS = frozenset({"security-reviewer", "reliability-reviewer", "remediator"})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_agent_and_save.py",
        description=(
            "Run a Kiro CLI custom agent non-interactively and atomically "
            "save its JSON response to a fixed artifact path. Transport "
            "only: no policy logic."
        ),
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Name of the Kiro CLI custom agent to invoke (e.g. security-reviewer).",
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Literal prompt text to pass to the agent.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to atomically write the agent's parsed JSON response to.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the kiro-cli chat subprocess before aborting (default: 300).",
    )
    return parser.parse_args(argv)


def _extract_json_object(stdout_text):
    """Return the parsed JSON object found in `stdout_text`, or raise ValueError.

    Tries a strict `json.loads` first. If that fails (e.g. the CLI emitted
    incidental banner/progress text around the agent's final message),
    falls back to locating the outermost `{ ... }` span (first `{` to
    last `}`) and parsing that span. Performs no interpretation of the
    JSON's contents beyond confirming it parses.
    """
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


def _atomic_write_json(payload, output_path):
    """Write `payload` as pretty-printed JSON to `output_path` atomically."""
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".run_agent_and_save_", suffix=".json.tmp", dir=output_dir
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


def _make_internal_review_result_path(output_path):
    """Generate a fresh, unique internal artifact path for the reviewer
    result-artifact transport, confined to the same directory as the
    durable `--output` path and named with the fixed
    `.review-result-<id>.json` pattern `write_review_result.py` requires.

    Mirrors `run_remediation_stage.py::_make_result_file_path`'s
    unique-path-per-invocation discipline: `tempfile.mkstemp` guarantees
    uniqueness (no stale artifact from a prior invocation can ever be
    mistaken for this one's result), and the file it creates is
    immediately removed so only `write_review_result.py`'s own atomic
    write ever populates it. This function is the sole generator of this
    path -- the reviewer agent only ever passes through the exact path it
    is given, via its prompt.
    """
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(output_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=output_dir, prefix=".review-result-", suffix=".json")
    os.close(fd)
    os.remove(path)
    return path


def _validate_internal_review_result(internal_path, agent):
    """Validate the internal review-result artifact directly.

    This is the authoritative signal for a reviewer's result (Reviewer
    result transport correction, module docstring) -- independent of, and
    never inferred from, the reviewer agent's chat stdout. Returns the
    parsed, schema-validated payload dict. Raises `ValueError` if the
    file is missing, unreadable, malformed, or fails
    `write_review_result.validate_review_result_schema` for `agent`.
    """
    if not os.path.isfile(internal_path):
        raise ValueError(
            f"write_review_result.py did not produce an internal review-result "
            f"artifact at {internal_path!r}; the reviewer's result cannot be confirmed"
        )

    try:
        with open(internal_path, "r") as internal_file:
            payload = json.load(internal_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"internal review-result artifact could not be read as JSON: {exc}") from exc

    try:
        return write_review_result.validate_review_result_schema(payload, agent)
    except write_review_result.ReviewResultValidationError as exc:
        raise ValueError(str(exc)) from exc


def _run_reviewer_with_artifact_transport(args):
    """Run a reviewer agent (`security-reviewer`/`reliability-reviewer`)
    using the corrected artifact-based result transport. Returns 0 on
    success (after atomically publishing the validated result to
    `args.output`), non-zero on any failure (nothing published).
    """
    internal_path = _make_internal_review_result_path(args.output)

    prompt = (
        f"{args.prompt}\n\n"
        "After you have determined your final ReviewResult JSON object, "
        "you MUST persist it by running exactly this command and passing "
        "your ReviewResult JSON object on its stdin (verbatim, nothing "
        "else on stdin):\n"
        f"python3 scripts/write_review_result.py --agent {args.agent} --output {internal_path}\n"
        "Do not print your ReviewResult as your final chat message instead "
        "of running that command -- running that command IS how your "
        "result is recorded."
    )
    argv_list = ["kiro-cli", "chat", "--agent", args.agent, "--no-interactive", prompt]

    try:
        result = subprocess.run(
            argv_list, capture_output=True, text=True, timeout=args.timeout
        )
    except subprocess.TimeoutExpired:
        print(
            f"run_agent_and_save.py: '{args.agent}' invocation timed out after {args.timeout}s",
            file=sys.stderr,
        )
        _remove_if_exists(internal_path)
        return 1
    except FileNotFoundError as exc:
        print(f"run_agent_and_save.py: could not run kiro-cli: {exc}", file=sys.stderr)
        _remove_if_exists(internal_path)
        return 1

    # Chat stdout/stderr and the process exit code are captured here only
    # for diagnostics -- never as the authoritative success/failure
    # signal for the reviewer's result (module docstring). Even a
    # non-zero kiro-cli exit does not short-circuit the artifact check:
    # the agent may have already successfully persisted its result before
    # a later, unrelated failure in the same chat turn.
    try:
        internal_payload = _validate_internal_review_result(internal_path, args.agent)
    except ValueError as exc:
        print(f"run_agent_and_save.py: {exc}", file=sys.stderr)
        if result.returncode != 0:
            print(
                f"run_agent_and_save.py: '{args.agent}' also exited with code {result.returncode}",
                file=sys.stderr,
            )
        print("--- diagnostic (non-authoritative) chat stdout ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        if result.stderr:
            print("--- diagnostic (non-authoritative) chat stderr ---", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
        _remove_if_exists(internal_path)
        return 1

    _atomic_write_json(internal_payload, args.output)
    _remove_if_exists(internal_path)
    print(json.dumps({"status": "saved", "agent": args.agent, "output": args.output}))
    return 0


def _remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main(argv=None):
    args = parse_args(argv)

    if args.agent not in ALLOWED_AGENTS:
        print(
            f"run_agent_and_save.py: unsupported --agent '{args.agent}'; "
            f"allowed agents are: {', '.join(sorted(ALLOWED_AGENTS))}",
            file=sys.stderr,
        )
        return 1

    if args.agent in REVIEWER_AGENTS:
        return _run_reviewer_with_artifact_transport(args)

    # Fallback path (non-reviewer agents, e.g. any future direct
    # `--agent remediator` invocation of this script): original
    # stdout-parsing behavior, unchanged.
    argv_list = ["kiro-cli", "chat", "--agent", args.agent, "--no-interactive", args.prompt]

    try:
        result = subprocess.run(
            argv_list, capture_output=True, text=True, timeout=args.timeout
        )
    except subprocess.TimeoutExpired:
        print(
            f"run_agent_and_save.py: '{args.agent}' invocation timed out after {args.timeout}s",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError as exc:
        print(f"run_agent_and_save.py: could not run kiro-cli: {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(
            f"run_agent_and_save.py: '{args.agent}' exited with code {result.returncode}",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
        return 1

    try:
        payload = _extract_json_object(result.stdout)
    except ValueError as exc:
        print(f"run_agent_and_save.py: {exc}", file=sys.stderr)
        print("--- raw stdout ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    _atomic_write_json(payload, args.output)
    print(json.dumps({"status": "saved", "agent": args.agent, "output": args.output}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
