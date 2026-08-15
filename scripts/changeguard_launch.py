#!/usr/bin/env python3
"""ChangeGuard Kiro Crew gateway launcher: two-stage plan/gate/execute.

WHAT THIS SCRIPT DOES, AND WHY IT EXISTS
-----------------------------------------
Three confirmed facts about the installed Kiro Crew 0.2.0 runtime shape
this script:

    0. A live semantics probe (see design.md's "Kiro Crew 0.2.0
       Orchestration Mapping") confirmed `decompose_yaml()` does not treat
       a node's `prompt:`/`shell:` text as a literal, deterministic
       subprocess command -- it is folded into `Task.description` and
       executed as one LLM/ACP chat turn against the run's single
       per-run agent. This script therefore ALWAYS supplies an explicit
       `agent` field (`crew-runner` by default) on both the plan and
       execute calls below, so every ChangeGuard DAG task runs inside the
       narrow, permission-restricted `crew-runner` Kiro CLI agent
       (`.kiro/agents/crew-runner.json`) rather than Crew's default
       `kirocrew-lite` persona. ChangeGuard's safety and reproducibility
       come from that agent's narrow command allow-list, the deterministic
       Python transport/tool scripts it is permitted to invoke, and
       fail-closed artifact validation -- not from any claim that Crew
       itself executes shell text deterministically.

    1. `decompose_yaml()` (`kiro_crew/task_planner.py`) has no
       conditional-skip/branching primitive, and does not accept
       `force_approval` as a YAML key at all (not in its allowed key set
       `{agent, timeout, depends_on, description, prompt, shell}`). A
       fully safe (PASS+PASS) candidate must never reach a human-approval
       gate, so ChangeGuard is split into two separate YAML DAG files:
       `.kiro/crew/changeguard-workflow.yaml` (Stage A: review only) and
       `.kiro/crew/changeguard-workflow-remediation.yaml` (Stage B:
       remediation onward, gated). Stage B is planned and executed only
       when Stage A's own filesystem output
       (`artifacts/change-blocked-result.json`) indicates the candidate
       was NOT safe -- a decision this script makes by checking for that
       one file's existence, never by calling any Crew API or evaluating
       SEC-001/SEC-002/REL-001/BR-001 itself.
    2. `force_approval` can only be set on an already-decomposed `Task`
       via `TaskRunner.update_task` (`kiro_crew/taskrunner.py`), exposed
       as `PATCH /api/taskrunner/{task_id}/tasks/{index}`
       (`dashboard/handlers/taskrunner.py::api_taskrunner_update_task`).
       Confirmed Crew 0.2.0 REST surface used by this script:

           POST  /api/taskrunner/plan            -- decompose WITHOUT executing
           PATCH /api/taskrunner/{task_id}/tasks/{index}  -- set force_approval
           POST  /api/taskrunner/{task_id}/execute        -- start execution

       `POST /api/taskrunner/plan` (source="yaml") calls `TaskRunner.plan()`,
       which decomposes into `Project.status == "planned"` WITHOUT
       starting execution -- unlike the combined `POST /api/taskrunner`
       (`api_taskrunner_start` -> `TaskRunner.start_background`), which
       begins running the DAG immediately on submission. Using the
       combined endpoint for the remediation stage would reopen the exact
       force_approval race this script exists to close (the DAG could
       already be executing before the force_approval PATCH lands), so
       this script NEVER calls plain `POST /api/taskrunner` for Stage B.

REQUIRED SAFE LIFECYCLE FOR STAGE B (implemented exactly, in this order):

    POST /api/taskrunner/plan
        -> obtain task_id + decomposed task list (steps[])
    -> locate exactly one task matching --remediation-node
    -> PATCH /api/taskrunner/{task_id}/tasks/{index}   {"force_approval": true}
    -> verify the response's "force_approval" field is exactly true
    -> POST /api/taskrunner/{task_id}/execute

Execution is NEVER started before the force_approval update is confirmed.
Every failure mode below fails closed (non-zero exit, no execute call):
planning fails; the response has no usable task_id; zero or more than one
task matches --remediation-node; the update call fails; the update
response does not show force_approval == true; the execute call fails.

This script never calls `POST /api/approvals/*` -- it never resolves,
simulates, or fakes the human approval decision itself. That decision is
made entirely inside the running gateway process (dashboard/websocket),
exactly as discovered in Phase 8A. This script also never modifies the
`kirocrew` package's source and never monkey-patches anything at runtime.

STANDARD LIBRARY ONLY (`urllib.request`/`json`/`os`), matching Requirement
1.4's "no dependency outside the Python 3 standard library" constraint.

USAGE
-----
Stage A (review only -- no approval gate involved at all):

    python3 scripts/changeguard_launch.py \\
        --gateway-url http://127.0.0.1:8787 --stage review

Stage B (remediation onward -- only after Stage A produced
`artifacts/change-blocked-result.json`; refuses to run otherwise):

    python3 scripts/changeguard_launch.py \\
        --gateway-url http://127.0.0.1:8787 --stage remediation

Both stages default `--agent` to `crew-runner`; pass a different value
only for local experimentation, never for a real ChangeGuard run.

Stage A first removes stale run-specific artifacts (never
`artifacts/baseline-plan.json`) via `cleanup_run_artifacts.py`'s explicit
allow-list, unless `--skip-cleanup` is passed -- this is what starts a
genuinely fresh run. Stage B deliberately does NOT run cleanup: it must
read the very `change-blocked-result.json` that Stage A just produced,
so wiping run-specific artifacts immediately before Stage B would delete
the input it depends on.

IMPORTANT CAVEAT ON REQUEST/RESPONSE FIELD NAMES
--------------------------------------------------
`/api/taskrunner/plan`'s exact response field names (`task_id`, `steps`,
and each step's `index`/`description`/`force_approval`) are confirmed
directly from `dashboard/handlers/taskrunner.py::api_taskrunner_plan`'s
source. This script still fails loudly (non-zero exit, explicit stderr
diagnostic) rather than guessing silently if a live gateway's response
ever does not contain an expected field -- so a mismatch against an
untested gateway version surfaces as an explicit error, never as a
silently-skipped approval gate.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cleanup_run_artifacts import cleanup_run_artifacts  # noqa: E402

DEFAULT_REVIEW_WORKFLOW = ".kiro/crew/changeguard-workflow.yaml"
DEFAULT_REMEDIATION_WORKFLOW = ".kiro/crew/changeguard-workflow-remediation.yaml"
DEFAULT_BLOCKED_ARTIFACT = os.path.join("artifacts", "change-blocked-result.json")

# The run-scoped Kiro CLI agent every ChangeGuard DAG task is executed
# against. Confirmed live (design.md's "Kiro Crew 0.2.0 Orchestration
# Mapping" / the shell-semantics probe): TaskRunner has exactly one
# per-run agent (`self._agent`), supplied once via the `agent` field on
# both `POST /api/taskrunner/plan` and `POST /api/taskrunner/{task_id}/
# execute` (dashboard/handlers/taskrunner.py::api_taskrunner_plan reads
# `body.get("agent", "")`; api_taskrunner_execute_plan reads the same
# field). Every DAG task is an LLM/ACP chat turn against this one agent,
# never Crew's default `kirocrew-lite` persona -- ChangeGuard must always
# supply `crew-runner` explicitly on both calls, never rely on the field
# being omitted.
CREW_RUNNER_AGENT = "crew-runner"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="changeguard_launch.py",
        description=(
            "Plan and execute one stage of the two-stage ChangeGuard Kiro "
            "Crew workflow against a running kirocrew gateway. For the "
            "remediation stage, sets force_approval=true via the safe "
            "plan -> locate -> update -> verify -> execute sequence "
            "BEFORE execution starts. No policy logic; no fake approval; "
            "never calls /api/approvals/*."
        ),
    )
    parser.add_argument(
        "--gateway-url",
        required=True,
        help="Base URL of the already-running kirocrew gateway, e.g. http://127.0.0.1:8787",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["review", "remediation"],
        help=(
            "'review' plans+executes Stage A (candidate-plan/reviewers/"
            "aggregate-review) with no approval gate involved. "
            "'remediation' plans Stage B, applies+verifies "
            "force_approval=true on the remediation task, then executes "
            "-- refuses to run at all unless the blocked artifact exists."
        ),
    )
    parser.add_argument(
        "--agent",
        default=CREW_RUNNER_AGENT,
        help=(
            "Run-scoped Kiro CLI agent supplied on both the plan and "
            "execute calls (default: crew-runner). ChangeGuard must never "
            "omit this and fall back to Crew's default persona."
        ),
    )
    parser.add_argument("--review-workflow", default=DEFAULT_REVIEW_WORKFLOW, help="Path to the Stage A YAML DAG.")
    parser.add_argument("--remediation-workflow", default=DEFAULT_REMEDIATION_WORKFLOW, help="Path to the Stage B YAML DAG.")
    parser.add_argument("--remediation-node", default="remediation", help="Node name/title of the task that must receive force_approval=true.")
    parser.add_argument("--blocked-artifact", default=DEFAULT_BLOCKED_ARTIFACT, help="Path checked to decide whether Stage B may run at all.")
    parser.add_argument("--artifacts-dir", default="artifacts", help="Directory cleaned of stale run-specific artifacts before planning.")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip the stale-artifact cleanup step (for tests/dry-runs only).")
    parser.add_argument(
        "--workspace-dir",
        default="",
        help=(
            "Absolute path Crew's TaskRunner should use as this run's "
            "work_dir/cwd (passed through to both the plan and execute "
            "calls' 'workspace_dir' field). Required for safely pointing "
            "a live run at a disposable temporary copy of this repo "
            "instead of wherever the gateway process happens to be "
            "running from. Left empty (Crew's own default) if omitted."
        ),
    )
    parser.add_argument(
        "--internal-secret",
        default="",
        help=(
            "Gateway's X-Internal-Secret value (its own machine-to-"
            "machine auth token, e.g. the contents of "
            "~/.kiro/crew/.local_secret) sent on every request. Leave "
            "empty only if the gateway does not require it."
        ),
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP request timeout in seconds.")
    return parser.parse_args(argv)


def _http_json(url, method, payload, timeout, internal_secret=""):
    """Perform one HTTP request with a JSON body and return the parsed JSON
    response. Raises RuntimeError (never a bare urllib exception) on any
    connection/HTTP failure, with the URL and underlying error included.

    `internal_secret`, when non-empty, is sent as the `X-Internal-Secret`
    header -- the gateway's own machine-to-machine auth mechanism for its
    "mixed" API paths (dashboard/browser session OR internal secret).
    Purely a transport/auth detail of reaching the already-documented
    REST endpoints; it changes no endpoint, no request sequence, and no
    field name discussed elsewhere in this module.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if internal_secret:
        headers["X-Internal-Secret"] = internal_secret
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}. Is `kirocrew gateway` running?") from exc
    except urllib.error.HTTPError as exc:  # pragma: no cover - HTTPError is a URLError subclass but explicit is clearer
        raise RuntimeError(f"{method} {url} returned HTTP {exc.code}: {exc.reason}") from exc
    return json.loads(body) if body else {}


def plan_workflow(gateway_url, workflow_path, timeout, agent=CREW_RUNNER_AGENT, workspace_dir="", internal_secret=""):
    """POST /api/taskrunner/plan with the YAML DAG's text. Decomposes
    WITHOUT executing. Returns the parsed response body.

    Always supplies `agent` explicitly (defaulting to CREW_RUNNER_AGENT)
    so the run's single per-run agent is the restricted `crew-runner`
    Kiro CLI agent, never Crew's default `kirocrew-lite` persona.
    `workspace_dir`, when non-empty, is passed through to
    `TaskRunner.plan()`'s own `workspace_dir` parameter (confirmed in
    `api_taskrunner_plan`: `body.get("workspace_dir", "")`), so a live
    verification run can be pointed at a disposable temporary copy of
    this repo instead of wherever the gateway process's own cwd is.
    """
    with open(workflow_path, "r") as workflow_file:
        yaml_text = workflow_file.read()
    url = gateway_url.rstrip("/") + "/api/taskrunner/plan"
    payload = {"source": "yaml", "input": yaml_text, "agent": agent}
    if workspace_dir:
        payload["workspace_dir"] = workspace_dir
    return _http_json(url, "POST", payload, timeout, internal_secret=internal_secret)


def find_task_by_node_name(plan_response, node_name):
    """Locate exactly one decomposed task whose description contains
    `node_name` (decompose_yaml embeds each node's YAML key into
    Task.description). Returns that task's `index`. Raises RuntimeError
    (fail closed) if the response is unusable, if no task matches, or if
    more than one task matches -- ambiguity is refused, never guessed."""
    steps = plan_response.get("steps")
    if not isinstance(steps, list):
        raise RuntimeError(
            f"plan response has no usable 'steps' list; cannot locate the "
            f"'{node_name}' task. Response was: {json.dumps(plan_response)[:500]}"
        )

    matches = [step for step in steps if node_name in str(step.get("description", ""))]
    if len(matches) == 0:
        raise RuntimeError(
            f"no decomposed task matched node name '{node_name}'. "
            f"Decomposed steps were: {json.dumps(steps)[:1000]}"
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"{len(matches)} decomposed tasks matched node name '{node_name}'; "
            "refusing to guess which one is the remediation gate. "
            f"Matches were: {json.dumps(matches)[:1000]}"
        )

    index = matches[0].get("index")
    if index is None:
        raise RuntimeError(f"matched task for node '{node_name}' has no usable 'index' field: {matches[0]}")
    return index


def set_and_verify_force_approval(gateway_url, task_id, task_index, timeout, internal_secret=""):
    """PATCH /api/taskrunner/{task_id}/tasks/{index} to set
    force_approval=true, then verify the response reflects that value.

    Raises RuntimeError (fail closed) if the request fails or if the
    response's "force_approval" field is not exactly True -- this is the
    "verify the update succeeded" step the safe lifecycle requires, and it
    happens BEFORE this function returns, i.e. before main() is allowed to
    call execute.
    """
    url = f"{gateway_url.rstrip('/')}/api/taskrunner/{task_id}/tasks/{task_index}"
    response = _http_json(url, "PATCH", {"force_approval": True}, timeout, internal_secret=internal_secret)
    if response.get("force_approval") is not True:
        raise RuntimeError(
            f"force_approval update did not verify: expected "
            f"force_approval == true in the response, got: {json.dumps(response)[:500]}"
        )
    return response


def execute_plan(gateway_url, task_id, timeout, agent=CREW_RUNNER_AGENT, workspace_dir="", internal_secret=""):
    """POST /api/taskrunner/{task_id}/execute to start a planned run.

    Always supplies `agent` explicitly (defaulting to CREW_RUNNER_AGENT)
    -- `api_taskrunner_execute_plan` reads its own `agent` field from the
    request body independently of what `plan_workflow` submitted, so both
    calls must agree on `crew-runner`. `workspace_dir` mirrors
    `plan_workflow`'s parameter for the same reason.
    """
    url = f"{gateway_url.rstrip('/')}/api/taskrunner/{task_id}/execute"
    payload = {"agent": agent}
    if workspace_dir:
        payload["workspace_dir"] = workspace_dir
    return _http_json(url, "POST", payload, timeout, internal_secret=internal_secret)


def run_review_stage(args):
    """Plan and immediately execute Stage A. No approval gate involved --
    Stage A's DAG contains no remediation/force_approval node at all."""
    try:
        plan_response = plan_workflow(
            args.gateway_url, args.review_workflow, args.timeout,
            agent=args.agent, workspace_dir=args.workspace_dir,
            internal_secret=args.internal_secret,
        )
    except (OSError, RuntimeError) as exc:
        print(f"changeguard_launch.py: Stage A planning failed: {exc}", file=sys.stderr)
        return 1

    task_id = plan_response.get("task_id")
    if not task_id:
        print(
            f"changeguard_launch.py: Stage A plan response has no usable "
            f"'task_id' field. Response was: {json.dumps(plan_response)[:500]}",
            file=sys.stderr,
        )
        return 1

    try:
        execute_response = execute_plan(
            args.gateway_url, task_id, args.timeout,
            agent=args.agent, workspace_dir=args.workspace_dir,
            internal_secret=args.internal_secret,
        )
    except RuntimeError as exc:
        print(f"changeguard_launch.py: Stage A execute failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "stage_a_executing", "task_id": task_id, "gateway_response": execute_response}))
    return 0


def run_remediation_stage(args):
    """Plan Stage B, gate it with a verified force_approval, then execute.

    Refuses outright (fail closed, no plan call at all) if
    --blocked-artifact does not exist -- Stage B must never be planned for
    a safe candidate, and this is the script's own independent check of
    that invariant (in addition to, not instead of, the fact that a safe
    run never even generates this artifact).
    """
    if not os.path.isfile(args.blocked_artifact):
        print(
            f"changeguard_launch.py: refusing to plan the remediation "
            f"stage -- '{args.blocked_artifact}' does not exist, meaning "
            "Stage A reported SAFE_TO_SHIP (or has not run yet). A safe "
            "candidate must never reach the remediation approval gate.",
            file=sys.stderr,
        )
        return 1

    try:
        plan_response = plan_workflow(
            args.gateway_url, args.remediation_workflow, args.timeout,
            agent=args.agent, workspace_dir=args.workspace_dir,
            internal_secret=args.internal_secret,
        )
    except (OSError, RuntimeError) as exc:
        print(f"changeguard_launch.py: Stage B planning failed: {exc}", file=sys.stderr)
        return 1

    task_id = plan_response.get("task_id")
    if not task_id:
        print(
            f"changeguard_launch.py: Stage B plan response has no usable "
            f"'task_id' field. Response was: {json.dumps(plan_response)[:500]}",
            file=sys.stderr,
        )
        return 1

    try:
        task_index = find_task_by_node_name(plan_response, args.remediation_node)
    except RuntimeError as exc:
        print(f"changeguard_launch.py: {exc}", file=sys.stderr)
        return 1

    try:
        update_response = set_and_verify_force_approval(
            args.gateway_url, task_id, task_index, args.timeout,
            internal_secret=args.internal_secret,
        )
    except RuntimeError as exc:
        print(f"changeguard_launch.py: force_approval update/verification failed: {exc}", file=sys.stderr)
        return 1

    try:
        execute_response = execute_plan(
            args.gateway_url, task_id, args.timeout,
            agent=args.agent, workspace_dir=args.workspace_dir,
            internal_secret=args.internal_secret,
        )
    except RuntimeError as exc:
        print(f"changeguard_launch.py: Stage B execute failed: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "stage_b_executing",
                "task_id": task_id,
                "remediation_task_index": task_index,
                "force_approval_confirmed": True,
                "update_response": update_response,
                "execute_response": execute_response,
            }
        )
    )
    return 0


def main(argv=None):
    args = parse_args(argv)

    if args.stage == "review":
        # Stale-artifact cleanup only ever happens at the start of a fresh
        # Stage A run -- never before Stage B, which must read the
        # change-blocked-result.json Stage A just produced.
        if not args.skip_cleanup:
            cleanup_run_artifacts(args.artifacts_dir)
        return run_review_stage(args)

    return run_remediation_stage(args)


if __name__ == "__main__":
    sys.exit(main())
