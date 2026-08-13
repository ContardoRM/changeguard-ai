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
  - [ ] 4.3 Write `tests/test_security_reviewer.py`
    - Unit-test the evidence-extraction functions from 4.1/4.2 directly: assert each returns a plain evidence record for well-formed fixture pairs, and an evidence-unavailable/malformed signal for the malformed/missing-field fixture from Task 6.4 — never a verdict of any kind
    - Test the full Security Reviewer judgment behavior via the judgment logic implemented in Task 8.1 (a plain, directly-importable pure function the agent invokes for its decision step, kept separate from the LLM's free-form behavior, so this is unit-testable without a live agent runtime):
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
  - [ ] 5.3 Write `tests/test_reliability_reviewer.py`
    - Unit-test the evidence-extraction functions from 5.1/5.2 directly: assert each returns a plain evidence record for well-formed fixture pairs, and an evidence-unavailable/malformed signal for the malformed/missing-field fixture from Task 6.4 — never a verdict of any kind
    - Test the full Reliability Reviewer judgment behavior via the judgment logic implemented in Task 8.2 (a plain, directly-importable pure function the agent invokes for its decision step, kept separate from the LLM's free-form behavior, so this is unit-testable without a live agent runtime):
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

- [ ] 9. Define the Remediator Kiro Crew agent (`.kiro/agents/`)
  - [x] 9.1 Author the Remediator agent definition
    - Trigger boundary: only invocable by the Orchestrator, and only after an explicit human approval signal — no code path in the agent definition invokes `apply_remediation.py` on its own initiative
    - Input boundary: accepts only the approved `Finding` record(s) (never raw plan JSON, never `terraform/main.tf` directly)
    - Behavior: for each approved finding, invokes `python3 scripts/apply_remediation.py --terraform-dir <path> --rule-id <finding.rule_id> --resource <finding.resource> --restore-value <finding.baseline_value>` — the restore value is always the finding's recorded `baseline_value`, never invented
    - Refusal boundary: blocks (does not invoke the script for) any finding whose `rule_id` is outside `{SEC-001, SEC-002, REL-001, BR-001}`
    - Never opens or edits `terraform/main.tf` itself
    - _Requirements: 9.1, 9.2, 9.4, 9.6, 9.7_
    - Implemented as `.kiro/agents/remediator.json` with its policy-free operational prompt in `.kiro/agents/remediator-prompt.md`. Permissions restrict `shell` to exactly `python3 scripts/apply_remediation.py *` (wildcard shell deny beneath it) and deny `fs_write` entirely — the agent holds no generic write tool at all; the only file mutation capability in this phase lives inside the deterministic script. The prompt explicitly states the agent assumes its caller (the future Orchestrator) has already obtained human approval — it implements no approval mechanism of its own (no `input()`, no env var check, no approval file), consistent with this phase's scope boundary. Verified live via `kiro-cli chat --agent remediator --no-interactive` against a temporary Terraform working copy (never the repository baseline): a REL-001 Finding correctly derived `--restore-value 3` from `finding.baseline_value` (not `candidate_value`) and successfully remediated; an unsupported rule ID (`IAM-001`) was refused before any shell invocation occurred; a Finding with a `resource` mismatched to its `rule_id` was passed through to the script, which rejected it, and the agent relayed that `remediation_failed` result honestly rather than retrying or guessing.

- [ ] 10. Implement the Safety Hook (`.kiro/hooks/`)
  - [ ] 10.1 Author the `preToolUse` safety hook blocking destructive commands workspace-wide
    - Register as `preToolUse` against shell-executing tool calls, applied workspace-wide (not scoped to a single agent)
    - Deny execution when the literal command text contains `terraform apply`, contains `terraform destroy`, matches an AWS CLI invocation pattern, or contains a destructive filesystem operation (including `rm -rf` / `rm -fr` and recursive-force variants)
    - On a match, block the call and return a denial message stating which pattern triggered it
    - Contains no SEC-001/SEC-002/REL-001/BR-001 rule evaluation and no plan-JSON parsing — purely textual/pattern-based
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  > Task 10.1 depends only on the fixed pattern list in design.md's "Kiro Hook / Safety Strategy" section and can be implemented independently of every agent and script task above.

- [ ] 11. Define the Orchestrator agent and wire the Kiro Crew workflow
  - [ ] 11.1 Author the Orchestrator agent definition
    - Coordination-only permission boundary: never reads plan JSON to make a rule decision, never writes `terraform/main.tf`, never invokes `apply_remediation.py` directly, never implements SEC-001/SEC-002/REL-001/BR-001 logic itself
    - Behavior: invokes `run_tf_plan.py` to produce `artifacts/baseline-plan.json` and `artifacts/candidate-plan.json`; invokes the Security Reviewer and Reliability Reviewer; aggregates their `ReviewResult`s as a pure union of finding lists
    - `CHANGE_BLOCKED` payload: when one or more findings exist, emit one record per finding with `rule_id`, `severity` (SEC-001→CRITICAL, SEC-002→CRITICAL, REL-001→HIGH, BR-001→CRITICAL), `resource`, `baseline_value`, `candidate_value`, `reason`, `proposed_remediation`, and stop before invoking the Remediator or touching `terraform/main.tf`
    - Approval branch: on explicit human approval, invoke the Remediator with the approved finding(s); after remediation, invoke `run_tf_plan.py` to produce `artifacts/remediated-plan.json`; invoke both reviewers again against Baseline vs. Remediated
    - Rejection branch: on explicit human rejection, leave `terraform/main.tf` unmodified, never invoke the Remediator, and report `REMEDIATION_REJECTED`
    - Verdict: report `SAFE_TO_SHIP` only when the relevant Terraform plan execution succeeded and both reviewers returned `PASS` on the same evidence pair; a `FAIL` or `INCOMPLETE` from either reviewer, or a plan-generation/tool error, independently blocks `SAFE_TO_SHIP`; the `SAFE_TO_SHIP` message states it reflects only the four supported rule IDs
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - [ ] 11.2 Define the Kiro Crew task/workflow wiring the four agents end to end
    - Implement the pre-approval phase: Orchestrator → Terraform Plan Tool (baseline, then candidate) → concurrent invocation of Security Reviewer and Reliability Reviewer (issued as independent, concurrently-invokable tasks with no ordering dependency) → aggregation
    - Implement the approval-gated phase: human approval capture point → Remediator → Remediation Script → Terraform Plan Tool (remediated) → concurrent re-invocation of both reviewers against Baseline vs. Remediated
    - Ensure the workflow definition matches the sequence diagram in design.md's "End-to-End Workflow" section exactly (no additional steps, no additional agents)
    - _Requirements: 4.1, 4.2, 7.1, 7.2, 7.3, 10.1, 10.2_

- [ ] 12. Checkpoint - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Write `tests/test_baseline_pass.py`
  - [ ] 13.1 Implement the safe-baseline PASS test
    - Run the Security Reviewer's and Reliability Reviewer's judgment logic (Tasks 8.1, 8.2) — via evidence extracted from `tests/fixtures/baseline_plan.json` vs. `tests/fixtures/candidate_safe.json` — not just the raw evidence-extraction modules, since only the reviewer's judgment step returns a `PASS`/`FAIL`/`INCOMPLETE` verdict
    - Assert both return `status: PASS` with an empty findings list
    - _Requirements: 12.2, 12.10_

- [ ] 14. Write the remediation and remediated-plan integration tests
  - [ ] 14.1 Implement `tests/test_remediation_script.py`
    - Guard with `unittest.skipUnless(shutil.which("terraform"), ...)` per design.md's Testing Strategy
    - Run `apply_remediation.py` against a temporary copy of `terraform/main.tf` for each of the four supported rule IDs and assert the targeted value is corrected and nothing else changed
    - Assert a non-zero exit and no file modification for an unsupported `--rule-id`
    - _Requirements: 12.7, 12.9, 12.10_
  - [ ] 14.2 Implement `tests/test_remediated_plan.py`
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
