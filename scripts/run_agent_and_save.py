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
       guaranteed to contain nothing but the agent's final JSON message
       (banner/progress text, a non-zero exit on a transient failure, or
       a malformed/partial response are all real possibilities). Plain
       `>` redirection would silently commit whatever stdout produced,
       valid JSON or not, to the fixed artifact path other DAG nodes
       depend on, with no atomicity guarantee if the process is
       interrupted mid-write.

This script therefore performs exactly three things, and nothing else:

    (i)   Run the approved `kiro-cli chat --agent <name> --no-interactive
          "<prompt>"` command as a subprocess (argv list, never a shell
          string, so the prompt text is never re-interpreted by a shell).
    (ii)  Validate that its stdout parses as JSON (tolerating leading/
          trailing non-JSON text around a single top-level `{...}`
          object, in case the CLI ever emits incidental banner output).
    (iii) Atomically write that JSON, pretty-printed, to the fixed
          `--output` artifact path (temp file + `os.replace`, so no
          downstream node can ever observe a partially written file).

This script contains NO SEC-001/SEC-002/REL-001/BR-001 rule logic, no
PASS/FAIL/INCOMPLETE logic, no severity computation, and no remediation
decision-making of any kind. It does not know what agent it is running for
beyond the literal `--agent` string it was given, and it does not inspect
the JSON payload's contents beyond confirming it parses as JSON. All
policy judgment happens entirely inside the invoked Kiro CLI agent
(`security-reviewer`, `reliability-reviewer`, or `remediator`), per their
own prompt-level policy documents; this script is transport only.

CLI contract:

    python3 scripts/run_agent_and_save.py \
        --agent <security-reviewer|reliability-reviewer|remediator> \
        --prompt "<literal prompt text>" \
        --output <path>

On success: writes the agent's parsed JSON response to `--output` and
exits 0. On any failure (non-zero `kiro-cli` exit, unparseable stdout, no
JSON object found), writes nothing to `--output` and exits non-zero with a
diagnostic on stderr, mirroring `scripts/run_tf_plan.py`'s fail-fast
contract.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

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


def main(argv=None):
    args = parse_args(argv)

    if args.agent not in ALLOWED_AGENTS:
        print(
            f"run_agent_and_save.py: unsupported --agent '{args.agent}'; "
            f"allowed agents are: {', '.join(sorted(ALLOWED_AGENTS))}",
            file=sys.stderr,
        )
        return 1

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
