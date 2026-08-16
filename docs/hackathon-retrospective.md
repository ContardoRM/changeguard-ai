# ChangeGuard AI — Kiro Hackathon Retrospective

This is a detailed retrospective on building ChangeGuard AI, based on the actual commit history, `design.md`'s documented live-probe findings, the repository's four agent prompts, and the live-verification passes actually performed in this repository. It is not generic Kiro advice — every claim below traces to something that happened in this repo. The condensed, reusable checklist derived from this document lives in `docs/kiro-hackathon-playbook.md`.

This document is split into two parts:

- **Part A — ChangeGuard-specific findings**: things that happened because of this project's particular design choices (four rules, two-stage DAG, four near-duplicate agent prompts, the specific Makefile/README timeline).
- **Part B — Reusable Kiro Crew patterns and limitations**: things that are properties of the Kiro Crew runtime itself, discovered here but applicable to any future Kiro Crew project.

---

## Part A — ChangeGuard-specific findings

### A1. Prompt efficiency

`security-reviewer-prompt.md` (175 lines) and `reliability-reviewer-prompt.md` (174 lines) are structurally near-identical — same section order (Role, Evidence command, Output contract, Permission boundaries), same sentence templates, differing mainly in rule IDs and resource names. `remediator-prompt.md` (179 lines) and `crew-runner-prompt.md` (107 lines) each independently re-derive the same "you never do X" permission-boundary list (no `terraform apply`/`destroy`, no AWS CLI, no generic file-write tool, refuse unsupported rule IDs, single-JSON-object output contract). None of these four prompts were generated from a shared template; each was authored and iterated on independently across separate turns.

**Concrete cost:** the Phase 8B "your chat response is diagnostic, not authoritative" correction was applied to `remediator-prompt.md` only, because that was the prompt actively being debugged at the time. The same underlying lesson (don't trust your own stdout) would apply equally to the reviewer prompts' output-contract sections if that failure mode had surfaced there instead — the lack of a shared source made it easy to fix one agent's prompt and not think to check whether the same fix pattern applied elsewhere.

**Estimate, not a measurement:** a shared permission-boundary fragment, even just as a copy/paste source file maintained separately from the four individual prompts, would plausibly have cut 30-40% of the authoring effort spent on the four prompts' boilerplate sections. This is a rough estimate based on how much of each prompt's line count is boilerplate versus project-specific judgment logic, not a timed measurement.

### A2. Token/credit usage

The three-outcome approval-gate scenario (safe candidate / unsafe + approve / unsafe + reject) was independently live-verified at least four separate times across this project's lifetime:

1. An initial live pass that discovered the fail-open bug (Crew task status marked "passed" despite a failed nested shell command).
2. A corrected approved-remediation regression pass, re-run after the Phase 8B/8C fixes.
3. A rejection-path pass, run in the same session as #2.
4. A second rejection-path pass, run again during final polish.

Each full pass spins up a fresh disposable workspace and runs Stage A (2 concurrent live reviewer agent calls) plus, for the approve/reject paths, Stage B (a `crew-runner` chat turn, and on approve, a `remediator` chat turn plus 2 more reviewer calls). That is roughly 2-6 live agent invocations per pass, times 4 passes — on the order of 15-20 live agent calls spent on approval-gate verification alone, on top of the original per-agent smoke tests done while building each agent (Tasks 8.1, 8.2, 9.1) and the live-judgment test classes in `test_baseline_pass.py`, `test_remediated_plan.py`, `test_security_reviewer.py`, and `test_reliability_reviewer.py` (20 tests capable of invoking `kiro-cli` for real unless explicitly skipped).

**Why this happened:** each time an architecture correction landed (the Phase 8B fail-open fix, then the Phase 8C path-confinement hardening), the full three-outcome matrix was re-verified live, including the safe-candidate path, which had no code touching its own logic in either fix. That path only needed to be verified live once, ever.

**What would have reduced this:**
- `CHANGEGUARD_SKIP_LIVE_TESTS` / `make test` vs. `make test-live` was only added during the Makefile-polish phase, late in the project. Had that gate existed from the first test file, iteration during earlier phases (evidence extraction, agent judgment tuning) would never have risked accidentally triggering a live, credit-consuming call.
- After a fix that only touches one stage (e.g. Stage B's remediation transport), re-verifying only that stage live — trusting the deterministic test suite plus the already-verified prior live pass for everything else — would have avoided re-running the safe-candidate path 3 unnecessary times.

### A3. Workflow design — decisions that should have come earlier

The single largest source of rework in this project was the assumption, made in the original `design.md`, that the Orchestrator would be a fourth `.kiro/agents/orchestrator.json` Kiro CLI agent. `design.md` itself documents this directly: *"this design's original assumption that the Orchestrator would be a fourth Kiro CLI custom agent did not hold once the installed Kiro Crew 0.2.0 runtime was inspected directly."* Discovering this during implementation (not design) forced:

- Replacing the assumed Orchestrator agent with Kiro Crew's own `TaskRunner`, driven by YAML DAGs.
- Inventing the `crew-runner` execution agent, which did not exist in the original design at all.
- Splitting one assumed workflow into two YAML stages (Stage A / Stage B) after discovering `decompose_yaml()` has no conditional-skip primitive.
- Rewriting every DAG node from an assumed `shell:` key to `prompt:` after a live probe proved Crew never executes a node's command text as a literal subprocess.

All of the numbered findings in `design.md`'s "Kiro Crew 0.2.0 Orchestration Mapping" section were discoverable from one disposable-YAML-node probe against a real running gateway — the same kind of probe that was in fact eventually run, just much later than it should have been. That probe should have been the first implementation task, before the Orchestrator section of `design.md` was ever written.

A second, related rework driver: the fail-open bug (Crew task status is not a reliable enforcement boundary; `kiro-cli` chat stdout is not a reliable transport) was only discovered after the remediation transport had already been built around trusting the agent's chat output. Both properties are runtime characteristics of Kiro Crew + `kiro-cli chat` in general, not bugs in this project's specific logic — see Part B.

### A4. What went right (worth repeating)

- The four-rule, explicitly-scoped MVP (`SEC-001`, `SEC-002`, `REL-001`, `BR-001`, nothing else) never caused scope creep or rework. The requirements phase locked this early and it held for the entire project.
- The evidence-extraction/judgment separation (deterministic Python returns only facts or a structural "unavailable" signal; the reviewer agent is the only place a verdict is produced) held up cleanly through every later correction — no fix ever required blurring that boundary.
- The two independent-reviewer-agents-plus-one-remediator-agent structure, and the defense-in-depth pattern (the deterministic remediation script re-checks the same rule-ID whitelist the Remediator agent already checked) both proved robust and required no rework.

---

## Part B — Reusable Kiro Crew patterns and limitations

These are properties of the installed Kiro Crew 0.2.0 runtime, empirically confirmed via live probes documented in `design.md`, not ChangeGuard-specific design choices. They should be assumed true by default for any future Kiro Crew hackathon project rather than rediscovered.

### B1. No deterministic node execution

Every DAG task — regardless of whether the YAML node uses `prompt:` or `shell:` — is folded verbatim into `Task.description` and executed as one LLM/ACP chat turn against the run's single per-run agent. There is no code path that runs a node's command text as a literal, deterministic subprocess. This was confirmed live: a disposable probe node (`shell: "printf ... > /tmp/probe.txt"`) spawned a real `kiro-cli acp` child process and consumed real LLM tokens; the probe file was never created because the chat turn never completed a tool-permission prompt.

**Implication:** always use `prompt:`, never `shell:` (the latter name is misleading about actual behavior), and word each node's text as an explicit, non-negotiable instruction such as "Execute exactly this command and no other command."

### B2. `agent:` in a YAML node is cosmetic

The `agent:` field inside a DAG node is embedded verbatim into the decomposed `Task.description` text; it is never used to select or bind an actual Kiro CLI agent. This was confirmed live: an `agent: probe` value that was not even a registered Crew agent name produced no error, only descriptive text. The only real agent-selection mechanism is `TaskRunner`'s single, run-scoped agent, supplied once via the `agent` field on the plan and execute API calls — every task in one run shares that one agent.

**Implication:** any multi-agent workflow needs one narrow "runner" agent (this project's `crew-runner`) that itself shells out to `kiro-cli chat --agent <name>` for each specialized agent, since Crew itself cannot bind different DAG nodes to different agents.

### B3. No automatic cross-task result injection

A completed task's `.result` text (its LLM output) is never automatically shown to a dependent task's prompt — only the plan's titles, descriptions, and dependency graph are. The only working data-flow channel between tasks in one run is the shared filesystem (every task shares one working directory).

**Implication:** any DAG design must plan fixed, explicitly-named artifact paths from the start, with every dependent node's prompt telling it exactly which path to read.

### B4. No conditional/branching DAG primitive

`decompose_yaml()`'s allowed node schema is exactly `{agent, timeout, depends_on, description, prompt, shell}` — there is no "skip this node if a predecessor's outcome was X" key. A single-file DAG containing both an unconditional review stage and an approval-gated remediation stage would therefore force even a fully safe candidate to reach the approval gate.

**Implication:** split any workflow that needs a conditionally-reached stage into separate YAML files, and decide whether to plan the later file at all using an out-of-Crew script that inspects filesystem state (this project: checking whether a specific artifact file exists) — never by trying to express the condition inside the YAML itself.

### B5. `force_approval` is real, but only settable via a specific REST sequence

The Gateway's `force_approval` gate is a genuine, blocking approval mechanism backed by a real async approval flow — not simulated. However, it cannot be set from the YAML DAG file at all. The confirmed-safe sequence is: `POST /api/taskrunner/plan` (decomposes without starting execution) → locate the target task by name in the response → `PATCH /api/taskrunner/{task_id}/tasks/{index}` with `{"force_approval": true}` → verify the response confirms `force_approval == true` → only then `POST /api/taskrunner/{task_id}/execute`. Using the combined submit-and-run endpoint instead would start execution before the `PATCH` could land, reopening a race condition.

**Implication:** always use the plan → locate → PATCH → verify → execute sequence, in that order, with a fail-closed check after each step, for any Crew workflow needing a human-approval gate.

### B6. Crew task pass/fail tracks the chat turn completing, not a nested command's exit code

This was the most consequential discovery in the project. A second live run proved that when a DAG node's agent runs a nested shell command that exits non-zero, and the agent's chat turn *honestly reports* that failure in its own final message, Crew's `TaskRunner` still marks the task `"passed"` — because Crew's task-level status tracks whether the LLM/ACP chat turn itself completed, not the exit code of whatever command the agent happened to run and describe. The DAG then proceeds to downstream nodes regardless.

**Implication:** never treat a Crew task's own pass/fail status as a safety-relevant enforcement boundary. Any outcome that matters (e.g. "did this remediation actually succeed?") must be verified independently, by deterministic code reading a dedicated artifact — never by trusting Crew's own status field.

### B7. `kiro-cli` chat stdout is not a reliable machine-readable transport

A direct investigation of a real `kiro-cli chat --agent <name> --no-interactive "<prompt>"` invocation confirmed its stdout simultaneously carries human-readable narration, the underlying shell tool's own echoed stdout, Kiro's progress/completion UI text, and the agent's final response — meaning more than one JSON-shaped fragment can legitimately appear in one transcript even on a fully successful run.

**Implication:** never parse chat stdout as the authoritative signal for anything safety-relevant. Use a dedicated, deterministic-script-written artifact file instead (see B8), and treat chat stdout as diagnostic/logging output only.

### B8. The execution-artifact validation pattern (the fix for B6 and B7 together)

Because neither Crew's task status (B6) nor the agent's chat stdout (B7) are trustworthy, this project's answer was: the deterministic script that performs the actual mutation (`apply_remediation.py`) writes a structured JSON result to a caller-specified, per-invocation path — atomically, and only on a fully validated success. The caller (`run_remediation_stage.py`) generates that path itself (confined to a specific directory and filename pattern, to prevent path injection), passes it to the agent, and after the agent's chat turn returns, independently reads and field-validates that artifact — never inferring success from Crew's status or the agent's stdout.

**Implication:** for any Kiro Crew workflow where an LLM-mediated task's success must be *proven*, not assumed, use this pattern: deterministic script writes a confined, per-invocation result artifact on validated success only; the caller reads and validates that artifact directly.

### B9. Live gateway approval requires an approval handler actually wired up

Bare `kirocrew run TASK.md` never wires an approval handler — a `force_approval` task under bare `run` fails immediately with "no approval handler configured"; it does not silently skip the gate, but it also cannot demonstrate a real approval UI. A genuine, visible, blocking approval step requires `kirocrew gateway --approval interactive` (or equivalent) already running, with the workflow submitted through the plan/PATCH/verify/execute sequence in B5 against that gateway's REST API.

### B10. Rejection maps to a specific, discoverable run status/error, not a distinct API outcome

Denying a `force_approval` task does not produce a special Crew-level "rejected" status distinct from other failures. In this project's observed runs, the run settled into `status: "paused"` with an `error` field describing the approval denial. Any project relying on rejection semantics should verify the exact status/error text against its own installed Crew version rather than assuming a fixed string, since this is an interpretation-layer mapping on top of Crew's own internals, not a documented Crew API contract.

---

## Estimated impact (estimates, not measurements)

The following are rough, directional estimates based on the patterns above, not timed or credit-metered measurements:

- Skipping 2-3 of the 4 full three-scenario live-verification passes (by not re-verifying already-proven, untouched paths) would plausibly have removed on the order of 10-15 live agent invocations from this project's total.
- Probing Crew semantics before writing `design.md`'s Orchestrator section would plausibly have avoided the multi-commit architecture correction cycle (Orchestrator-agent → `TaskRunner` + `crew-runner`, `shell:` → `prompt:`, single DAG → two-stage DAG) — realistically on the order of a couple of hours of otherwise-necessary rework, based on the number of files touched across those correction commits.
- A shared agent permission-boundary source file could plausibly have reduced prompt-authoring effort on the four agent prompts by roughly 30-40%, based on how much of each prompt's content is repeated boilerplate versus project-specific judgment logic.
- Combined, a rough, non-measured estimate is that these changes together could have saved somewhere in the range of **20-30% of total session time and a comparable fraction of Kiro credit spend** for this specific project — concentrated in the mid-implementation architecture correction and the repeated live-verification passes. Treat this as a directional estimate for planning purposes, not a benchmarked result.

See `docs/kiro-hackathon-playbook.md` for the condensed, reusable checklist derived from this retrospective.
