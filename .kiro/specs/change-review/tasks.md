# Implementation Plan: ChangeGuard AI (change-review)

## Overview

This plan implements exactly the MVP architecture approved in `design.md`: two deterministic CLI scripts (`scripts/run_tf_plan.py`, `scripts/apply_remediation.py`), four Kiro Crew agents (Security Reviewer, Reliability Reviewer, Remediator, Orchestrator) under `.kiro/agents/`, one Kiro safety hook under `.kiro/hooks/`, the Kiro Crew workflow wiring those four agents, the five stdlib `unittest` modules named in design.md's Testing Strategy, one supplemental end-to-end integration test covering all four rule IDs plus the rejection path (traceable to Requirement 13), Makefile convenience targets, and the README judge walkthrough. No AWS deployment, LocalStack, Docker, MCP, frontend, GitHub API integration, generic Terraform security scanning, or rule IDs beyond `SEC-001`/`SEC-002`/`REL-001`/`BR-001` are introduced anywhere below.

**Implementation language**: Python 3 (standard library only), matching design.md's explicit CLI contracts and Requirement 1.4/12.1. No language selection question is needed — design.md already fixes the language and dependency constraints.

**Necessary implementation detail not fully pinned by design.md**: design.md separates evidence extraction from rule judgment (Requirements 5.10/5.11, 6.10/6.11) but leaves the exact code shape of each to the tasks phase. Without an LLM in the loop and without inventing new architecture, this plan resolves that as follows: the SEC-001/SEC-002/REL-001/BR-001 evidence-extraction logic (reading `resource_changes[]`, matching the relevant field, and returning a plain evidence record or an evidence-unavailable/malformed signal — never a verdict) lives in a plain, directly-importable Python module per reviewer (`scripts/security_rules.py`, `scripts/reliability_rules.py`). These are **not** additional CLI tools and carry no `--flag` contract of their own — they are in-process, facts-only libraries that each reviewer agent invokes for evidence, distinct from the two documented CLI scripts (`run_tf_plan.py`, `apply_remediation.py`). The rule-satisfaction judgment itself — deciding whether an evidence record's fact pattern satisfies a rule, and assembling the resulting `ReviewResult`/`Finding`(s) — is the Security Reviewer's and Reliability Reviewer's own responsibility (Requirements 5.11, 6.11), never the evidence-extraction module's. This plan implements that judgment step as agent-definition logic that may itself be a plain, directly-importable pure function the agent invokes for its decision step (kept separate from the LLM's free-form behavior), so the mandatory judgment-behavior tests (Requirement 12.10) remain automatable via `unittest` without requiring a live agent runtime. This keeps design.md's "exactly two deterministic local scripts" claim intact (those two are the only scripts with an external CLI contract) while making both the evidence-extraction facts and the reviewer judgment directly unit-testable per the Determinism principle and Requirement 12.

## Tasks

- [x] 1. Implement the Terraform Plan Tool (`scripts/run_tf_plan.py`)
  - [x] 1.1 Implement CLI argument parsing (`--terraform-dir`, `--output`) and the fixed subcommand allow-list guard
    - Parse `--terraform-dir` and `--output` from argv (no shell string construction; argv lists only, per design.md's Terraform Plan Tool contract)
    - Define the fixed allow-list `{init, fmt, validate, plan, show}` and check every subcommand against it before any `subprocess.run` call, so no code path can reach `apply`/`destroy`/`aws` — this is the Requirement 11.6 secondary enforcement layer for this script
    - _Requirements: 2.3, 2.4, 11.6_
  - [x] 1.2 Implement the init/fmt-check/validate/plan/show pipeline with fail-fast error handling
    - Run, in order, `terraform init -input=false`, `terraform fmt -check`, `terraform validate`, `terraform plan -refresh=false -out=<tmpfile>`, `terraform show -json <tmpfile>` against `--terraform-dir`
    - Write the final command's stdout byte-for-byte to `--output`, overwriting any existing file at that path (Artifact Lifecycle overwrite semantics)
    - On any non-zero exit from a subcommand, abort immediately, write nothing to `--output` (no partial/corrupt JSON), and return a non-zero exit status with captured stderr
    - Contain no risk-detection or rule-evaluation logic of any kind
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 1.3 Write unit tests for `run_tf_plan.py`'s allow-list guard and error handling
    - Test that a stubbed/mocked subprocess call outside `{init, fmt, validate, plan, show}` is rejected before invocation
    - Test that a simulated non-zero subcommand exit results in no write to `--output` and a non-zero return code
    - Test that re-running the tool against the same `--output` path overwrites the prior file content (Artifact Lifecycle overwrite behavior)
    - _Requirements: 2.3, 2.4, 11.6_

- [x] 2. Implement the Remediation Script (`scripts/apply_remediation.py`)
  - [x] 2.1 Implement CLI argument parsing (`--terraform-dir`, `--rule-id`, `--resource`, `--restore-value`) and the 4-entry rule whitelist
    - Parse the four flags from argv (no shell string construction, no additional file-path or free-form content argument)
    - Define the fixed whitelist mapping `rule_id -> (expected resource type/address, expected HCL attribute/block, value type)` for exactly `SEC-001`, `SEC-002`, `REL-001`, `BR-001`
    - If `--rule-id` is not one of the four, or `--resource` does not match the expected resource type/address for that rule, exit non-zero and write nothing
    - _Requirements: 9.2, 9.3, 9.5, 9.7_
  - [x] 2.2 Implement the four narrow, rule-specific HCL edits with per-rule type validation
    - SEC-001: rewrite `cidr_blocks` on the port-22 ingress block of `aws_security_group.payments_sg`, validating `--restore-value` is a CIDR-list string
    - SEC-002: restore `cidr_blocks` on the existing baseline port-5432 ingress entry of the matched security group resource to the exact baseline value, symmetric with SEC-001's port-22 restore — never inventing a new value or a new ingress block — validating `--restore-value` is a CIDR-list string
    - REL-001: rewrite `desired_count` on `aws_ecs_service.payments_api`, validating `--restore-value` is an int
    - BR-001: rewrite `deletion_protection` on `aws_db_instance.payments_db`, validating `--restore-value` is a bool
    - Perform only the one targeted edit for the matched rule and nothing else in `terraform/main.tf`
    - _Requirements: 9.3, 9.5_
  - [x] 2.3 Write unit tests for whitelist rejection and each of the four narrow edits
    - Test a non-zero exit and no file modification for an unsupported `--rule-id`
    - Test a non-zero exit and no file modification when `--resource` does not match the rule's expected type/address
    - Test each of the four supported rule IDs against a temporary copy of `terraform/main.tf`, asserting only the targeted attribute changed and the rest of the file is untouched (including that SEC-002's restore targets the existing port-5432 entry, not a newly created one)
    - Test that an incorrectly-typed `--restore-value` is rejected before any write
    - _Requirements: 9.3, 9.5, 9.7, 12.7_
    - Implemented in `scripts/apply_remediation.py` (deterministic stdlib brace-counting/regex parsing scoped to the fixed demo Terraform structure — no external HCL parser, no unrestricted global search-and-replace) and `tests/test_apply_remediation.py` (27 tests, all operating on temporary copies of `terraform/main.tf`, never the repository baseline). Covers all four mandatory positive scenarios (SEC-001, SEC-002, REL-001, BR-001, each asserting the unrelated attribute/ingress block and unrelated file content are byte-for-byte unchanged) and all seven mandatory negative scenarios (unsupported rule ID, wrong resource for a supported rule, malformed restore value, missing target resource, missing target attribute/ingress block, already-remediated no-op target, and ambiguous/duplicate targets) — every negative case asserts `main.tf` content is identical before and after the rejected call. Writes are atomic (temp file + `os.replace`) so no partially written `main.tf` can ever be observed.

- [ ] 3. Checkpoint - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement the Security Reviewer evidence-extraction module (`scripts/security_rules.py`) and its tests
  - [x] 4.1 Implement SEC-001 evidence extraction
    - Read `resource_changes[]` where `.address == "aws_security_group.payments_sg"`, then `.change.after.ingress[]`, matching the entry where `from_port <= 22 <= to_port` and `protocol` is `"tcp"` or `"-1"`
    - Return a plain evidence record `{resource, baseline: {cidr_blocks: [...]}, candidate: {cidr_blocks: [...]}}` when both the Baseline Plan and the Candidate/Remediated Plan contain a matching entry
    - Return an evidence-unavailable/malformed signal — never `PASS`/`FAIL`/`INCOMPLETE`, never a `Finding` — when the resource address is missing, the field is missing/wrong-typed, or no ingress entry covers port 22, in either plan
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 5.10_
  - [x] 4.2 Implement SEC-002 evidence extraction
    - Same resource/field path as 4.1, matching the ingress entry where `from_port <= 5432 <= to_port`
    - The baseline's port-5432 ingress entry is explicit in `terraform/main.tf`'s baseline configuration (not inferred or defaulted), so this reads it directly from the Baseline Plan's `.change.after.ingress[]`, symmetric with SEC-001's port-22 entry
    - Return an evidence record or an evidence-unavailable/malformed signal, using the same rules as 4.1, applied to port 5432
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 5.10_
  - [x] 4.3 Write `tests/test_security_reviewer.py`
    - Unit-test the evidence-extraction functions from 4.1/4.2 directly: assert each returns a plain evidence record for well-formed fixture pairs, and an evidence-unavailable/malformed signal for the malformed/missing-field fixture from Task 6.4 — never a verdict of any kind
    - Test the full Security Reviewer judgment behavior via the Security Reviewer Kiro agent implemented in Task 8.1: `security_rules.py` supplies evidence extraction only (never a verdict), and the `PASS`/`FAIL`/`INCOMPLETE` judgment for SEC-001/SEC-002 lives entirely in the Security Reviewer's agent prompt — there is no importable Python function that implements that policy judgment. These verdict-level assertions invoke the real agent via `kiro-cli chat --agent security-reviewer --no-interactive`:
      - SEC-001 transition fixture pair produces `FAIL` with a `SEC-001` finding (Req 12.3)
      - SEC-002 transition fixture pair produces `FAIL` with a `SEC-002` finding (Req 12.4)
      - safe baseline/candidate pair produces `PASS`
      - the malformed/missing-field fixture produces `INCOMPLETE` — never `PASS` and never a fabricated finding
    - Property check: every finding returned across all fixture pairs has `rule_id in {SEC-001, SEC-002}` (design.md Property 2)
    - Property check: every finding is derived only from comparing two fixture plan JSON files' `.change.after` values, never from a single plan's `.change.before` (design.md Property 1)
    - This test module depends on both the evidence-extraction functions (Task 4.1, 4.2) and the Security Reviewer's judgment logic (Task 8.1) — see the Task Dependency Graph
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 5.1, 5.5, 5.6, 5.9, 5.10, 5.11, 12.3, 12.4, 12.10_

- [ ] 5. Implement the Reliability Reviewer evidence-extraction module (`scripts/reliability_rules.py`) and its tests
  - [x] 5.1 Implement REL-001 evidence extraction
    - Read `resource_changes[]` where `.address == "aws_ecs_service.payments_api"`, then `.change.after.desired_count`
    - Return a plain evidence record `{resource, baseline: {desired_count: <int>}, candidate: {desired_count: <int>}}` when both plans contain the field with the expected type
    - Return an evidence-unavailable/malformed signal — never `PASS`/`FAIL`/`INCOMPLETE`, never a `Finding` — when the resource address or field is missing or wrong-typed in either plan
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 6.10_
  - [x] 5.2 Implement BR-001 evidence extraction
    - Read `resource_changes[]` where `.address == "aws_db_instance.payments_db"`, then `.change.after.deletion_protection`
    - Return a plain evidence record `{resource, baseline: {deletion_protection: <bool>}, candidate: {deletion_protection: <bool>}}` when both plans contain the field with the expected type
    - Return an evidence-unavailable/malformed signal — never `PASS`/`FAIL`/`INCOMPLETE`, never a `Finding` — when the resource address or field is missing or wrong-typed in either plan
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 6.10_
  - [x] 5.3 Write `tests/test_reliability_reviewer.py`
    - Unit-test the evidence-extraction functions from 5.1/5.2 directly: assert each returns a plain evidence record for well-formed fixture pairs, and an evidence-unavailable/malformed signal for the malformed/missing-field fixture from Task 6.4 — never a verdict of any kind
    - Test the full Reliability Reviewer judgment behavior via the Reliability Reviewer Kiro agent implemented in Task 8.2: `reliability_rules.py` supplies evidence extraction only (never a verdict), and the `PASS`/`FAIL`/`INCOMPLETE` judgment for REL-001/BR-001 lives entirely in the Reliability Reviewer's agent prompt — there is no importable Python function that implements that policy judgment. These verdict-level assertions invoke the real agent via `kiro-cli chat --agent reliability-reviewer --no-interactive`:
      - REL-001 transition fixture pair produces `FAIL` with a `REL-001` finding (Req 12.5)
      - BR-001 transition fixture pair produces `FAIL` with a `BR-001` finding (Req 12.6)
      - safe baseline/candidate pair produces `PASS`
      - the malformed/missing-field fixture produces `INCOMPLETE` — never `PASS` and never a fabricated finding
    - Property check: every finding returned across all fixture pairs has `rule_id in {REL-001, BR-001}` (design.md Property 2)
    - Property check: every finding is derived only from comparing two fixture plan JSON files' `.change.after` values (design.md Property 1)
    - This test module depends on both the evidence-extraction functions (Task 5.1, 5.2) and the Reliability Reviewer's judgment logic (Task 8.2) — see the Task Dependency Graph
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 6.1, 6.5, 6.6, 6.9, 6.10, 6.11, 12.5, 12.6, 12.10_

- [x] 6. Create rule-evaluation test fixtures (`tests/fixtures/*.json`)
  - [x] 6.1 Create `tests/fixtures/baseline_plan.json` and `tests/fixtures/candidate_safe.json`
    - `baseline_plan.json`: an explicit port-22 ingress entry with `cidr_blocks = ["10.0.0.0/8"]` AND an explicit port-5432 ingress entry with `cidr_blocks = ["10.0.0.0/8"]` (both explicit, symmetric — matching design.md's Data Models example), `desired_count = 3`, `deletion_protection = true`, matching the `resource_changes[]` schema in design.md's Data Models section
    - `candidate_safe.json`: identical safe values (no supported transition), used for the PASS scenario in both reviewers and in `test_baseline_pass.py`
    - _Requirements: 12.2, 12.4_
  - [x] 6.2 Create `tests/fixtures/candidate_sec001.json` and `tests/fixtures/candidate_sec002.json`
    - `candidate_sec001.json`: port-22 ingress `cidr_blocks` changed to `["0.0.0.0/0"]`, all other fields safe
    - `candidate_sec002.json`: the existing baseline port-5432 ingress entry's `cidr_blocks` changed to `["0.0.0.0/0"]` (a baseline-relative CIDR change on the existing explicit entry, not a newly introduced ingress block — symmetric with SEC-001), all other fields safe
    - _Requirements: 12.3, 12.4_
  - [x] 6.3 Create `tests/fixtures/candidate_rel001.json` and `tests/fixtures/candidate_br001.json`
    - `candidate_rel001.json`: `desired_count` changed to `1`, all other fields safe
    - `candidate_br001.json`: `deletion_protection` changed to `false`, all other fields safe
    - _Requirements: 12.5, 12.6_
  - [x] 6.4 Create malformed/missing-field fixtures to support the `INCOMPLETE` test case
    - Create fixture(s) (e.g. `tests/fixtures/candidate_malformed_security.json` and `tests/fixtures/candidate_malformed_reliability.json`) that omit the relevant resource address, or use a wrong-typed/missing field, for at least one rule owned by each reviewer
    - These fixtures directly support the `INCOMPLETE` assertions in Tasks 4.3 and 5.3 (evidence-unavailable/malformed signal from extraction, `INCOMPLETE` from reviewer judgment)
    - _Requirements: 5.6, 5.9, 6.6, 6.9, 12.10_
    - Note: implemented at the evidence-extraction layer only, per the phase's architectural boundary — extraction returns a structural `EvidenceStatus` (`MISSING_RESOURCE` / `MISSING_FIELD` / `MALFORMED`), never `INCOMPLETE` itself. Additional fixture variants (`candidate_missing_resource_{security,reliability}.json`, `candidate_missing_field_{security,reliability}.json`, `candidate_malformed_reliability_int_for_bool.json`, `invalid_json.json`) were added beyond the two named here to cover each distinct evidence-status outcome independently.

- [ ] 7. Checkpoint - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Define the Security Reviewer and Reliability Reviewer Kiro Crew agents (`.kiro/agents/`) and their rule-judgment logic
  - [x] 8.1 Author the Security Reviewer agent definition
    - Read-only permission boundary: no file-write tool, no Terraform-execution tool, no remediation-script-invocation tool
    - Scope instructions restricted to `SEC-001`/`SEC-002`; for each rule, invokes `scripts/security_rules.py`'s evidence-extraction function against the two artifact paths supplied by the Orchestrator, receiving back either a plain evidence record or an evidence-unavailable/malformed signal
    - Implements the rule-satisfaction judgment as a plain, directly-importable Python function (not a CLI script, not additional evidence-extraction logic) that the agent invokes for its decision step: given an evidence record, judges whether the fact pattern satisfies SEC-001/SEC-002; given an evidence-unavailable/malformed signal, treats it as insufficient evidence for that rule
    - Assembles and returns the `ReviewResult` itself — `PASS` when both SEC-001 and SEC-002 judgment complete with no finding, `FAIL` with `Finding`(s) restricted to `rule_id in {SEC-001, SEC-002}` when either rule's judgment identifies a violation, `INCOMPLETE` when either rule's evidence was unavailable/malformed or the judgment step failed to complete — the agent, not `security_rules.py`, produces the verdict
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11_
    - Implemented as `.kiro/agents/security-reviewer.json` (Kiro CLI 2.18.0's native agent schema: `name`/`description`/`prompt`/`tools`/`allowedTools`/`resources`/`permissions.rules`) with its policy prompt in `.kiro/agents/security-reviewer-prompt.md`. The rule-satisfaction judgment for SEC-001/SEC-002 lives entirely in the agent's system prompt (LLM-owned policy), not in any Python function; the agent's only permitted tool action is `shell` restricted via `permissions.rules` to `python3 scripts/print_security_evidence.py *` (a thin, policy-free JSON serializer over `scripts/security_rules.py`'s existing extraction functions), with a wildcard shell deny and a wildcard `fs_write` deny beneath it. Verified live via `kiro-cli chat --agent security-reviewer --no-interactive` against the four required fixture scenarios (safe -> `PASS`; `candidate_sec001.json` -> `FAIL`/`SEC-001`; `candidate_sec002.json` -> `FAIL`/`SEC-002`; `candidate_malformed_security.json` -> `INCOMPLETE`), all matching the required `ReviewResult` JSON contract exactly.
  - [x] 8.2 Author the Reliability Reviewer agent definition
    - Read-only permission boundary identical in kind to 8.1, scoped to `REL-001`/`BR-001`
    - For each rule, invokes `scripts/reliability_rules.py`'s evidence-extraction function against the two artifact paths supplied by the Orchestrator, receiving back either a plain evidence record or an evidence-unavailable/malformed signal
    - Implements the rule-satisfaction judgment as a plain, directly-importable Python function that the agent invokes for its decision step, analogous to 8.1
    - Assembles and returns the `ReviewResult` itself — `PASS` when both REL-001 and BR-001 judgment complete with no finding, `FAIL` with `Finding`(s) restricted to `rule_id in {REL-001, BR-001}` when either rule's judgment identifies a violation, `INCOMPLETE` when either rule's evidence was unavailable/malformed or the judgment step failed to complete — the agent, not `reliability_rules.py`, produces the verdict
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11_
    - Implemented as `.kiro/agents/reliability-reviewer.json` with its policy prompt in `.kiro/agents/reliability-reviewer-prompt.md`, mirroring 8.1's structure exactly: judgment for REL-001/BR-001 lives entirely in the prompt, the only permitted shell command is `python3 scripts/print_reliability_evidence.py *` (a thin, policy-free JSON serializer over `scripts/reliability_rules.py`), with the same wildcard shell/fs_write deny rules. Verified live via `kiro-cli chat --agent reliability-reviewer --no-interactive` against the four required fixture scenarios (safe -> `PASS`; `candidate_rel001.json` -> `FAIL`/`REL-001`; `candidate_br001.json` -> `FAIL`/`BR-001`; `candidate_malformed_reliability.json` -> `INCOMPLETE`), all matching the required `ReviewResult` JSON contract exactly.

  > Tasks 8.1 and 8.2 have no shared state or data dependency (design.md "Parallel Reviewer Execution") and can be implemented in parallel. Both depend on the corresponding evidence-extraction module (Task 4 for 8.1, Task 5 for 8.2) being complete.
  > Note: `scripts/security_rules.py` and `scripts/reliability_rules.py` themselves were NOT modified in this phase and still contain zero policy-decision logic (no rule IDs, no PASS/FAIL/INCOMPLETE, no thresholds) — verified by grep. Two new thin serialization CLIs, `scripts/print_security_evidence.py` and `scripts/print_reliability_evidence.py`, were added solely so each agent's `shell` tool has a single, narrowly-permitted, non-interactive command to invoke; both serializers only call the existing extraction functions and print their result as JSON, with no comparison/threshold/verdict logic of their own.

- [x] 9. Define the Remediator Kiro Crew agent (`.kiro/agents/`)
  - [x] 9.1 Author the Remediator agent definition
    - Trigger boundary: only invocable by the Orchestrator, and only after an explicit human approval signal — no code path in the agent definition invokes `apply_remediation.py` on its own initiative
    - Input boundary: accepts only the approved `Finding` record(s) (never raw plan JSON, never `terraform/main.tf` directly)
    - Behavior: for each approved finding, invokes `python3 scripts/apply_remediation.py --terraform-dir <path> --rule-id <finding.rule_id> --resource <finding.resource> --restore-value <finding.baseline_value>` — the restore value is always the finding's recorded `baseline_value`, never invented
    - Refusal boundary: blocks (does not invoke the script for) any finding whose `rule_id` is outside `{SEC-001, SEC-002, REL-001, BR-001}`
    - Never opens or edits `terraform/main.tf` itself
    - _Requirements: 9.1, 9.2, 9.4, 9.6, 9.7_
    - Implemented as `.kiro/agents/remediator.json` with its policy-free operational prompt in `.kiro/agents/remediator-prompt.md`. Permissions restrict `shell` to exactly `python3 scripts/apply_remediation.py *` (wildcard shell deny beneath it) and deny `fs_write` entirely — the agent holds no generic write tool at all; the only file mutation capability in this phase lives inside the deterministic script. The prompt explicitly states the agent assumes its caller (the future Orchestrator) has already obtained human approval — it implements no approval mechanism of its own (no `input()`, no env var check, no approval file), consistent with this phase's scope boundary. Verified live via `kiro-cli chat --agent remediator --no-interactive` against a temporary Terraform working copy (never the repository baseline): a REL-001 Finding correctly derived `--restore-value 3` from `finding.baseline_value` (not `candidate_value`) and successfully remediated; an unsupported rule ID (`IAM-001`) was refused before any shell invocation occurred; a Finding with a `resource` mismatched to its `rule_id` was passed through to the script, which rejected it, and the agent relayed that `remediation_failed` result honestly rather than retrying or guessing.

- [x] 10. Implement the Safety Guard (`preToolUse` hook)
  - [x] 10.1 Author the `preToolUse` safety guard blocking destructive commands
    - Register as `preToolUse` against shell-executing tool calls
    - Deny execution when the literal command text contains `terraform apply`, contains `terraform destroy`, matches an AWS CLI invocation pattern, or contains a destructive filesystem operation (including `rm -rf` / `rm -fr` and recursive-force variants)
    - On a match, block the call and return a denial message stating which pattern triggered it
    - Contains no SEC-001/SEC-002/REL-001/BR-001 rule evaluation and no plan-JSON parsing — purely textual/pattern-based
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
    - **Installed-CLI discrepancy discovered and resolved (see design.md's "Kiro Hook / Safety Strategy" discrepancy note):** `kiro-cli 2.18.0` has no standalone `.kiro/hooks/*.json` mechanism and no truly workspace-wide hook scope — `preToolUse` hooks are an embedded `hooks.preToolUse` field inside each agent's own JSON config, and are therefore agent-scoped. Implemented as a deterministic stdlib Python script, `scripts/safety_guard.py` (no third-party packages), registered via `hooks.preToolUse` (matcher `execute_bash`, command `python3 scripts/safety_guard.py`) on all three existing ChangeGuard agents that hold a `shell` tool: `.kiro/agents/security-reviewer.json`, `.kiro/agents/reliability-reviewer.json`, `.kiro/agents/remediator.json`. Only the `hooks` field was added to each config; `tools`, `allowedTools`, and `permissions.rules` were left untouched (confirmed via `git diff`), so no existing agent's shell/write permissions were broadened. The script reads the hook's JSON stdin payload (`{"tool_input": {"command": "..."}}`), shell-tokenizes the command (handling `;`/`&&`/`||`/`|` chaining so a prohibited command hidden after a benign one is still caught), and exits `0` (allow) or `2` (deny) — the exact exit-code contract this installed CLI version requires (empirically confirmed exit code `1` does **not** block the tool call). Fails closed on malformed/missing hook input. Contains zero ChangeGuard policy vocabulary (verified by a dedicated self-check unit test scanning the module source for SEC-001/SEC-002/REL-001/BR-001/severities/PASS/FAIL/INCOMPLETE/etc.). Real Kiro hook smoke test performed via `kiro-cli chat --agent <temporary-probe-agent> --no-interactive --trust-all-tools` against a disposable temporary directory (never the repository baseline, never a real destructive action): `rm -rf <disposable-dir>` and `aws s3 ls` were both genuinely intercepted before execution (`PreToolHook blocked the tool execution: safety_guard.py: DENIED - ...`, disposable directory confirmed to still exist afterward), while `terraform validate` and a plain `echo` both executed normally (hook reported `✓ ... hooks finished`). All three affected agent configs re-validated with `kiro-cli agent validate` (exit 0).

  > All three ChangeGuard agents holding a `shell` tool now carry this hook; the future Orchestrator agent must carry the same `hooks.preToolUse` entry when it is implemented, since this CLI version has no workspace-wide or built-in-default-agent-override mechanism to apply it automatically.

- [x] 11. Implement the ChangeGuard orchestration workflow via Kiro Crew's `TaskRunner` (no `orchestrator.json` agent)
  - [x] 11.1 Author the deterministic Kiro Crew YAML DAG(s) and their file-based transport utilities
    - Kiro Crew's own `TaskRunner` — not a `.kiro/agents/orchestrator.json` Kiro CLI agent — is the ChangeGuard Orchestrator, per the Phase 8A empirical discovery documented in design.md's "Kiro Crew 0.2.0 Orchestration Mapping". No orchestrator agent file exists or is created anywhere in this repo.
    - `decompose_yaml()` has no conditional-skip/branching primitive, so the DAG is split into two YAML files rather than one unconditional file (Phase 8B correction — a single-file DAG would force a safe PASS+PASS candidate to still reach the approval-gated `remediation` node): **Stage A** (`.kiro/crew/changeguard-workflow.yaml`) is `candidate-plan` → `{security-review, reliability-review}` (no dependency between them, so Crew's `group_parallel_tasks()`/`asyncio.gather()` batches them into the same concurrent ready group) → `aggregate-review`. **Stage B** (`.kiro/crew/changeguard-workflow-remediation.yaml`) is `remediation` (the human-approval-gated node) → `remediated-plan` → `{security-re-review, reliability-re-review}` (concurrent) → `final-verdict`. `scripts/changeguard_launch.py` plans/executes Stage A unconditionally, then plans/executes Stage B only when Stage A's own `artifacts/change-blocked-result.json` output exists on disk — a safe candidate never has a `remediation` task decomposed at all.
    - `candidate-plan` fails closed if `artifacts/baseline-plan.json` does not already exist; this workflow never generates the baseline itself, preventing an already-modified candidate `main.tf` from being mistaken for the baseline
    - Per-node Security Reviewer / Reliability Reviewer / Remediator invocation uses the existing Kiro CLI agents via `kiro-cli chat --agent <name> --no-interactive "<prompt>"` inside each node's `shell:` command — `agent:` in the YAML is a cosmetic label only (per the Phase 8A finding that `decompose_yaml()` never reads it back as an executor selector), never an actual agent binding
    - Structured data flow between nodes is file-based only (fixed `artifacts/*.json` paths written and then explicitly read by each dependent node's own shell command), since Crew never auto-injects a predecessor `Task.result` into a dependent node's prompt
    - Four stdlib-only, policy-free transport scripts back the DAG's non-agent nodes: `scripts/run_agent_and_save.py`, `scripts/aggregate_review.py`, `scripts/run_remediation_stage.py`, `scripts/final_verdict.py` — none of them re-implement SEC/REL policy, calculate severity, or make a remediation decision
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
    - Implemented as `.kiro/crew/changeguard-workflow.yaml` (Stage A) and `.kiro/crew/changeguard-workflow-remediation.yaml` (Stage B), each decomposed with no LLM involvement by `decompose_yaml()`. **Architecture correction from a live semantics probe** (see design.md's "Kiro Crew 0.2.0 Orchestration Mapping"): `decompose_yaml()` folds a node's `prompt:`/`shell:` text verbatim into `Task.description`, and Crew executes every task as one LLM/ACP chat turn against the run's single per-run agent — there is no deterministic, Crew-owned literal-subprocess execution of a node's command text. Both YAML files were therefore rewritten to use `prompt:` (not `shell:`, which misleadingly suggested literal shell execution) and to specify `agent: crew-runner` on every node; each node's prompt text explicitly instructs the agent to "Execute exactly this command and no other command: `<command>`," keeping the command fixed and non-interpolated. `.kiro/agents/crew-runner.json`/`crew-runner-prompt.md` define a new, extremely narrow run-scoped Kiro CLI agent — `shell` tool only, `permissions.rules` allow-listing exactly the ChangeGuard transport scripts below (plus the one baseline-existence check), explicitly denying `apply_remediation.py`/`terraform`/`aws`/`rm`, and carrying the same `hooks.preToolUse` safety_guard.py hook as the other three ChangeGuard agents. `scripts/changeguard_launch.py` supplies `agent: crew-runner` explicitly on both `POST /api/taskrunner/plan` and `POST /api/taskrunner/{task_id}/execute` for both stages — never Crew's default `kirocrew-lite` persona. `scripts/run_agent_and_save.py` (invoked by `crew-runner`, not by Crew itself) runs `kiro-cli chat --agent <name> --no-interactive` (rejecting any `--agent` outside `{security-reviewer, reliability-reviewer, remediator}` before invoking anything, as a defense-in-depth allow-list) and atomically persists its JSON stdout to a fixed artifact path — the only working data-flow channel between DAG nodes. `scripts/aggregate_review.py` performs a pure union of the two `ReviewResult`s; writes an early minimal `SAFE_TO_SHIP` directly on PASS+PASS (removing any stale `artifacts/change-blocked-result.json` left over from a prior run), or atomically replaces `artifacts/change-blocked-result.json` otherwise; no SEC/REL re-evaluation. `scripts/run_remediation_stage.py` no-ops (`status: "noop"`) when there's nothing to remediate; otherwise invokes the remediator agent once per approved, supported finding, passing only that finding's JSON — never raw plan JSON — and rolls up per-finding outcomes into `remediated`/`partial`/`failed`/`noop` for observability only. `scripts/final_verdict.py` produces the authoritative post-remediation verdict, including the steering doc's `SAFE_TO_SHIP` scope-limitation sentence. `scripts/cleanup_run_artifacts.py` removes exactly the run-specific `artifacts/*.json` allow-list (never `artifacts/baseline-plan.json`) via `os.remove` only, no recursive deletion. The human-approval-gate mechanism itself (`force_approval`) cannot be expressed in either YAML — see Task 11.2. Deterministic tests: `tests/test_aggregate_review.py`, `tests/test_run_agent_and_save.py`, `tests/test_run_remediation_stage.py`, `tests/test_cleanup_run_artifacts.py`, `tests/test_changeguard_launch.py`, `tests/test_crew_runner_agent_config.py` (Gateway HTTP calls mocked; no live gateway contacted).
  - [x] 11.2 Apply Gateway-backed `force_approval` to the `remediation` task, verify DAG concurrency grouping, and perform approval/rejection smoke tests
    - Use Crew's supported `TaskRunner.update_task` API — exposed as `PATCH /api/taskrunner/{task_id}/tasks/{index}` on a running `kirocrew gateway` — to set `force_approval: true` on Stage B's decomposed `remediation` task by name. The confirmed safe sequence (implemented in `scripts/changeguard_launch.py`, verified by `tests/test_changeguard_launch.py`'s mocked-Gateway tests) is: `POST /api/taskrunner/plan` (decompose only, does not start execution) → locate the `remediation` task by name → `PATCH .../tasks/{index}` → verify the response confirms `force_approval == true` → only then `POST /api/taskrunner/{task_id}/execute`. Execution is never started before that verification succeeds; planning failure, an unusable/missing/ambiguous task match, an update failure, or a verification failure each independently abort before any execute call (Phase 8B correction — the original implementation used the combined submit-and-start `POST /api/taskrunner` endpoint, which begins executing before a separate `force_approval` call could land, reopening the race this task exists to close).
    - `scripts/changeguard_launch.py` is the stdlib-only helper that performs this plan → locate → update → verify → execute sequence against the official Gateway REST API only, and separately refuses to plan Stage B at all unless `artifacts/change-blocked-result.json` exists on disk; it must not modify Kiro Crew source, monkey-patch Crew, call `/api/approvals/*` itself, or simulate approval, and must contain no SEC/REL policy logic
    - **Live verification complete (all three approval-gate outcomes re-verified against a real Gateway, each against a fresh disposable workspace, after the Phase 8B/8C transport and path-confinement corrections):**
      - **Safe-candidate path**: both reviewers PASS, `SAFE_TO_SHIP`, Stage B never planned (verified in an earlier live pass; not re-run this session per explicit instruction).
      - **Approved-remediation path (regression re-run after the Phase 8B/8C fixes)**: REL-001 candidate (`desired_count = 1` vs. baseline `3`) → Stage A: Security PASS, Reliability FAIL/REL-001, `change-blocked-result.json` = `CHANGE_BLOCKED`. Stage B planned, `force_approval` PATCHed and verified `true` on the decomposed `remediation` task (index 1) before `execute`, `TaskRunner` agent confirmed `crew-runner`. Pre-approval invariants confirmed (`desired_count = 1`; `remediation-result.json`, `remediated-plan.json`, and any `.remediation-execution-*.json` all absent) via a genuine pending `GET /api/approvals` entry, approved via the real `/api/approvals/{id}/approve` Gateway endpoint. Post-approval: `artifacts/remediation-result.json` reported `status: "remediated"` with `rule_id: "REL-001"`, `resource: "aws_ecs_service.payments_api"`, `restored_value: 3` exactly matching the approved Finding (the authoritative execution-artifact contract, never inferred from `kiro-cli` stdout or from Terraform's resulting state); no internal `.remediation-execution-*.json` artifact remained afterward (cleaned up by `run_remediation_stage.py`). `terraform/main.tf` (disposable copy) showed `desired_count = 3`; `artifacts/remediated-plan.json` existed and was valid. Post-remediation re-review: Security re-review PASS, Reliability re-review PASS (Baseline vs. Remediated). `artifacts/final-verdict.json` reported `SAFE_TO_SHIP` with the four-rule scope-limitation sentence present, valid only because `remediation-result.status == "remediated"` AND remediated-plan success AND both re-reviews PASS.
      - **Rejection path (this session's live run)**: same REL-001 scenario in a fresh disposable workspace. Stage A: Security PASS, Reliability FAIL/REL-001, `change-blocked-result.json` = `CHANGE_BLOCKED`. Pre-Stage-B invariants confirmed (`desired_count = 1`; `remediation-result.json`, `remediated-plan.json`, `.remediation-execution-*.json` all absent). Stage B planned (`task_id = plan_1786832016`), `force_approval` PATCHed and verified `true` on task index 1, `TaskRunner` agent confirmed `crew-runner`, execution started. A genuine pending approval (`id = task-gate-1-5eac5891`) was observed via `GET /api/approvals`; invariants (`desired_count = 1`, both result artifacts absent) re-confirmed immediately before rejecting. Rejected via the real `POST /api/approvals/{id}/reject` Gateway endpoint (`{"ok": true}`). Post-rejection: the run settled into a stable terminal state — `status: "paused"`, `error: "Task 1 approval denied — paused for editing"`, `completed: 0`, `failed: 0` — with the gated `remediation` task itself still `"status": "pending"` (never executed; its mutation never ran). Confirmed on disk: `desired_count` unchanged at `1`; `remediation-result.json` absent; `remediated-plan.json` absent; `security-remediated-review-result.json`/`reliability-remediated-review-result.json`/`final-verdict.json` all absent (no downstream re-review or final-verdict node ran); no `.remediation-execution-*.json` artifact present. The real repository's `terraform/main.tf` (`git diff -- terraform/main.tf`) was empty before and after every run in this session.
      - **Tooling note (not a safety/architecture defect):** `scripts/changeguard_launch.py`'s `find_task_by_node_name` substring-matches `--remediation-node` against each decomposed task's `description`; because the `final-verdict` node's description also contains the literal substring `artifacts/remediation-result.json`, the default `--remediation-node remediation` now matches two tasks (the gated node and final-verdict) and is correctly refused as ambiguous rather than guessed. This live session's runs passed the more specific `--remediation-node "run_remediation_stage.py"` to disambiguate; the script's own fail-closed ambiguity handling worked exactly as designed (refused, no execute call) rather than misfiring — no code change was made, per this session's explicit no-architecture-change constraint. `scripts/changeguard_launch.py`'s default `--remediation-node` value may be worth revisiting in a later pass, but no live run in this session or any prior one depended on the default value being unambiguous.
      - Both the fail-open bug (Phase 8B — `run_remediation_stage.py`'s unconditional exit 0, `final_verdict.py` never consulting the remediation-result artifact) and the follow-on `--result-file` path-confinement hardening (Phase 8C — confining the execution-artifact path to `artifacts/.remediation-execution-<id>.json`, rejecting absolute/traversal/symlink/wrong-filename paths fail-closed) are implemented, unit-tested (`tests/test_final_verdict.py`, `tests/test_run_remediation_stage.py`, `tests/test_apply_remediation.py`), and now confirmed live end-to-end across all three approval-gate outcomes.
    - Using Crew's own plan/task inspection, verify and report the actual observed grouping (not an assumption from YAML shape alone): confirm `security-review`/`reliability-review` land in the same ready/parallel group, and that `security-re-review`/`reliability-re-review` land in the same ready/parallel group — confirmed in an earlier live pass (Stage A: both reviewer results appeared in the same polling window; not re-proven in every subsequent regression run per explicit instruction to avoid re-proving already-demonstrated concurrency).
    - Perform one real approval smoke test under `kirocrew gateway --approval interactive`: reach the `remediation` task, confirm the Gateway emits a genuine pending approval, confirm no source modification occurred before approval, then approve and confirm Remediator → remediated plan → parallel re-review → both PASS → `SAFE_TO_SHIP` — done (see approved-remediation path above).
    - Perform one real rejection smoke test: reach `CHANGE_BLOCKED`, reject, and confirm the Remediator is never invoked, `terraform/main.tf` remains unmodified, no `remediated-plan.json` is produced — done (see rejection path above). Observed live outcome is Crew's `TaskRunner` run settling into `status: "paused"` with `error: "Task 1 approval denied — paused for editing"` (rather than a `task.error = "user denied force_approval gate"` string on the run itself); this observed status/error text is what ChangeGuard's operational tooling should key off of to report `REMEDIATION_REJECTED` to a human operator — no code in this repo currently parses or maps that text automatically (no requirement asked for that mapping to be automated, and none is implemented), so this is documented here as the confirmed real signal rather than an assumption.
    - A `force_approval` task with no Gateway approval handler attached (i.e., run via bare `kirocrew run`) must fail closed, never auto-proceed — bare `kirocrew run` must not be used as, or advertised as, the Human Approval demo path
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [ ] 12. Checkpoint - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Write `tests/test_baseline_pass.py`
  - [x] 13.1 Implement the safe-baseline PASS test
    - Run the Security Reviewer's and Reliability Reviewer's judgment logic (Tasks 8.1, 8.2) — via evidence extracted from `tests/fixtures/baseline_plan.json` vs. `tests/fixtures/candidate_safe.json` — not just the raw evidence-extraction modules, since only the reviewer's judgment step returns a `PASS`/`FAIL`/`INCOMPLETE` verdict
    - Assert both return `status: PASS` with an empty findings list
    - _Requirements: 12.2, 12.10_

- [x] 14. Write the remediation and remediated-plan integration tests
  - [x] 14.1 Implement `tests/test_remediation_script.py`
    - Guard with `unittest.skipUnless(shutil.which("terraform"), ...)` per design.md's Testing Strategy
    - Run `apply_remediation.py` against a temporary copy of `terraform/main.tf` for each of the four supported rule IDs and assert the targeted value is corrected and nothing else changed
    - Assert a non-zero exit and no file modification for an unsupported `--rule-id`
    - _Requirements: 12.7, 12.9, 12.10_
  - [x] 14.2 Implement `tests/test_remediated_plan.py`
    - Guard with `unittest.skipUnless(shutil.which("terraform"), ...)`
    - Apply a real remediation via `apply_remediation.py` to a temporary copy of `terraform/main.tf`, generate a real remediated plan via `run_tf_plan.py`, and run both the Security Reviewer's and Reliability Reviewer's judgment logic (Tasks 8.1, 8.2) against Baseline vs. that Remediated Plan
    - Assert both reviewers return `PASS`
    - _Requirements: 12.8, 12.10_

  > Task 14.1 depends only on the completed `run_tf_plan.py` and `apply_remediation.py` scripts (Tasks 1-2) and does not depend on the agent definitions or Kiro Crew wiring (Tasks 8-11). Task 14.2 additionally depends on the Security Reviewer and Reliability Reviewer agent definitions (Tasks 8.1, 8.2), since asserting a `PASS` verdict requires invoking each reviewer's judgment logic, not just its evidence-extraction module.

- [ ] 15. Checkpoint - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Write the end-to-end workflow verification test
  - [ ]* 16.1 Implement `tests/test_end_to_end_workflow.py`
    - Guard with `unittest.skipUnless(shutil.which("terraform"), ...)` for the real-Terraform portions
    - For each of the four rule IDs (`SEC-001`, `SEC-002`, `REL-001`, `BR-001`) in turn: generate a baseline plan, apply the corresponding candidate transition to a temporary copy of `terraform/main.tf`, generate a candidate plan, run both reviewers' judgment logic (Tasks 8.1, 8.2) and assert a `CHANGE_BLOCKED`-equivalent result (one `FAIL` with the expected `rule_id`), simulate approval, invoke `apply_remediation.py`, generate a remediated plan, and assert both reviewers return `PASS` (`SAFE_TO_SHIP`-equivalent)
    - Additionally exercise the rejection path once: given a `CHANGE_BLOCKED`-equivalent result, simulate rejection and assert `terraform/main.tf` is left unmodified and `apply_remediation.py` is never invoked (`REMEDIATION_REJECTED`-equivalent)
    - This test supplements, and does not replace, the mandatory fixture/integration test modules from Tasks 4, 5, 13, and 14 — it specifically validates the full baseline→candidate→blocked→approve→remediate→remediated→verdict wiring across all four rule IDs plus the rejection branch, per Requirement 13's judge walkthrough. Per Requirement 12.11, this is the one test in the suite that may remain optional, because it requires runtime agent behavior that is difficult to automate reliably
    - _Requirements: 12.11, 13.1, 13.2, 8.5, 8.6, 9.7, 10.3, 10.4, 10.5, 10.6_

- [ ] 17. Add Makefile convenience targets
  - [ ] 17.1 Add `make` targets for the demo commands
    - `baseline`: run `run_tf_plan.py --terraform-dir terraform --output artifacts/baseline-plan.json`
    - `candidate`: run `run_tf_plan.py --terraform-dir terraform --output artifacts/candidate-plan.json`
    - `remediated`: run `run_tf_plan.py --terraform-dir terraform --output artifacts/remediated-plan.json`
    - `test`: run the full `unittest` suite under `tests/`
    - _Requirements: 13.1_

- [ ] 18. Write the README judge instructions
  - [ ] 18.1 Add the five-minute demo walkthrough to `README.md`
    - Document the steps from design.md's "Five-Minute Demo Walkthrough": clone, generate baseline, inject one supported change, run the ChangeGuard workflow via Kiro Crew, observe `CHANGE_BLOCKED` findings, approve remediation, observe the remediated plan and final verdict — and the alternate rejection branch showing `REMEDIATION_REJECTED`
    - State plainly that `SAFE_TO_SHIP` reflects only the four supported MVP rules and is not a claim of universal production-readiness (Requirement 10.8)
    - _Requirements: 10.8, 13.1, 13.2_

  > Tasks 17.1 and 18.1 depend only on the CLI contracts fixed in Tasks 1-2 (flags, artifact paths) and are independent of every agent/hook implementation detail in Tasks 8-11; they can proceed in parallel with each other and with Tasks 8-16 once Tasks 1-2 are complete.

- [ ] 19. Final checkpoint - Ensure all tests pass, ask the user if questions arise.

## Notes

- Per Requirement 12.10/12.11, only the full end-to-end Kiro Crew automation test (Task 16.1, `test_end_to_end_workflow.py`) is marked `*` and may be treated as optional, because it requires runtime agent behavior that is difficult to automate reliably. Every other test-writing sub-task in this plan is mandatory and must be implemented like any other task — including the `run_tf_plan.py`/`apply_remediation.py` unit tests (Tasks 1.3, 2.3), the evidence-extraction and reviewer-judgment tests (Tasks 4.3, 5.3), the safe-baseline `PASS` test (Task 13.1), the remediation-script tests (Task 14.1), and the remediated-plan test (Task 14.2). Core implementation tasks (no `*`) are always mandatory.
- Security Reviewer and Reliability Reviewer evidence-extraction implementation (Tasks 4 and 5) are independent of each other — no shared state or data dependency — and can be implemented in parallel, per design.md's "Parallel Reviewer Execution" section and Requirement 7.
- `run_tf_plan.py` (Task 1) and `apply_remediation.py` (Task 2) are independent scripts with no shared code or shared file target and can be implemented in parallel.
- Fixture creation (Task 6) depends only on the JSON path contracts already fixed in design.md's "Baseline/Candidate/Remediated Evidence Model" and can proceed in parallel with Tasks 4 and 5's evidence-extraction implementation.
- Tasks 4.3, 5.3, 13.1, and 14.2 test reviewer `PASS`/`FAIL`/`INCOMPLETE` judgment behavior and therefore depend on the Security Reviewer and Reliability Reviewer agent definitions (Tasks 8.1, 8.2), not only on the evidence-extraction modules (Tasks 4.1/4.2, 5.1/5.2) — see the Task Dependency Graph.
- The Safety Hook (Task 10) depends only on the fixed pattern list in design.md and can be implemented at any point, independent of every other task.
- README and Makefile work (Tasks 17-18) depend only on the CLI contracts fixed in Tasks 1-2 and are independent of agent/hook internals.
- `scripts/security_rules.py` and `scripts/reliability_rules.py` (Tasks 4, 5) are internal, deterministic evidence-extraction libraries backing the Security Reviewer and Reliability Reviewer agents respectively. They return only plain evidence records or evidence-unavailable/malformed signals — never a verdict. They are supporting libraries with no independent CLI contract, not additional CLI tools, and are distinct from the two documented CLI scripts (`run_tf_plan.py`, `apply_remediation.py`) named in design.md. The rule-satisfaction judgment and `ReviewResult` assembly live in the agent definitions (Tasks 8.1, 8.2), not in these libraries.
- No task in this plan covers AWS deployment, LocalStack, Docker, MCP, a frontend, GitHub API integration, generic Terraform security scanning, or any rule ID beyond `SEC-001`/`SEC-002`/`REL-001`/`BR-001`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "6.1", "6.2", "6.3", "6.4", "10.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "4.1", "5.1"] },
    { "id": 2, "tasks": ["1.3", "2.3", "4.2", "5.2", "9.1"] },
    { "id": 3, "tasks": ["8.1", "8.2"] },
    { "id": 4, "tasks": ["4.3", "5.3", "13.1", "14.1", "14.2"] },
    { "id": 5, "tasks": ["11.1"] },
    { "id": 6, "tasks": ["11.2"] },
    { "id": 7, "tasks": ["17.1", "18.1"] },
    { "id": 8, "tasks": ["16.1"] }
  ]
}
```
