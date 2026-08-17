# ChangeGuard AI

ChangeGuard AI is a local, reproducible Terraform change review board: it catches four specific, high-risk infrastructure regressions before they ship, blocks them until a human explicitly approves a deterministic fix, and proves the fix worked with a second real Terraform plan — all without ever calling `terraform apply` or touching a real AWS account.

## Why this exists

A Terraform diff can be syntactically valid and pass `terraform validate` while quietly introducing a real security or reliability regression — an SSH ingress rule widened to the entire internet, a database's deletion protection turned off, a service's redundancy dropped to a single task. Nothing in `terraform plan`'s own output flags that as dangerous; it's just a value that changed. ChangeGuard AI exists to catch exactly that class of regression, using genuine Terraform plan evidence and two independent AI reviewer agents, before a human ever has to approve a fix.

## How it works

```text
baseline plan (safe terraform/main.tf)
  -> real Terraform plan            => artifacts/baseline-plan.json
candidate plan (change under review)
  -> real Terraform plan            => artifacts/candidate-plan.json
  -> parallel Security Reviewer + Reliability Reviewer (Kiro CLI agents)
  -> aggregation
     - both PASS  -> SAFE_TO_SHIP (scoped to the four rules below)
     - any FAIL/INCOMPLETE -> CHANGE_BLOCKED, human approval required
        -> approved -> deterministic remediation applies the one already-approved fix
           -> real Terraform plan   => artifacts/remediated-plan.json
           -> parallel re-review (Baseline vs. Remediated)
           -> SAFE_TO_SHIP if both PASS, otherwise still blocked
        -> rejected -> REMEDIATION_REJECTED, terraform/main.tf left untouched
```

Every plan referenced above (`baseline-plan.json`, `candidate-plan.json`, `remediated-plan.json`) is a **real** `terraform show -json` output from a real local `terraform plan` — never a fabricated diff, never an LLM's guess at what changed. A finding is only ever produced by comparing two of these genuine plans' `.change.after` values against each other.

The baseline and candidate plans are deliberately generated **independently**, from two separate Terraform runs, rather than diffed from one combined plan: the baseline always comes from the known-safe repository configuration, generated out of band before any candidate exists, so a candidate is never compared against itself and there is no way for a single mutated run to fabricate its own "before" state.

**No AWS account, real AWS credentials, LocalStack, or Docker are required anywhere in this project.** The AWS Terraform provider is configured with fake credentials and `skip_credentials_validation`/`skip_metadata_api_check`/`skip_requesting_account_id` (`terraform/versions.tf`), so plans compute locally with no network calls to AWS.

## Supported MVP rules

ChangeGuard intentionally supports exactly four rules — no more:

| Rule | Condition | Severity |
|---|---|---|
| `SEC-001` | TCP/22 ingress CIDR: `10.0.0.0/8` → `0.0.0.0/0` | CRITICAL |
| `SEC-002` | TCP/5432 ingress CIDR: `10.0.0.0/8` → `0.0.0.0/0` | CRITICAL |
| `REL-001` | ECS `desired_count`: baseline `>= 3` → candidate `== 1` | HIGH |
| `BR-001` | RDS `deletion_protection`: `true` → `false` | CRITICAL |

ChangeGuard is deliberately **not** a generic Terraform security scanner. It does not evaluate IAM, encryption, networking beyond the two named ports, or any condition outside this fixed list. An unrecognized condition never becomes a finding — it is simply not evaluated.

**`SAFE_TO_SHIP` scope disclaimer:** `SAFE_TO_SHIP` means only that the candidate (or remediated) configuration passed these four supported ChangeGuard rules. It does **not** mean the infrastructure is universally safe or production-ready — `artifacts/final-verdict.json` restates this scope limitation every time it is produced.

## Architecture

- **AI owns policy judgment; deterministic Python owns everything mechanical.** The Security Reviewer and Reliability Reviewer are independent, read-only Kiro CLI agents — they are the *only* components authorized to decide `PASS`/`FAIL`/`INCOMPLETE` or to produce a finding. Deterministic Python (`scripts/security_rules.py`, `scripts/reliability_rules.py`) only extracts plain facts from plan JSON for the reviewers to judge — it never returns a verdict. `scripts/apply_remediation.py` performs the one narrow, whitelisted HCL edit a finding calls for, after human approval, and never decides whether a value is "safe." `scripts/run_tf_plan.py` only runs real Terraform commands; it contains no risk-detection logic at all.
- **Kiro Crew** provides the real orchestration substrate: a two-stage YAML DAG (`.kiro/crew/changeguard-workflow.yaml` for review, `.kiro/crew/changeguard-workflow-remediation.yaml` for remediation/re-review), genuine dependency-based concurrent scheduling for the two reviewer pairs, and the Gateway's `force_approval` human-approval gate.
- **Human approval is mandatory** before any remediation: the Gateway's `force_approval` gate blocks execution until a real approval/rejection decision is made — through the Gateway dashboard, or through the Control Room UI (see below), never simulated or auto-approved by any script in this repository.
- **Remediator** is a restricted Kiro CLI agent: it selects and executes the already-approved remediation intent (which finding, which command) — it does not judge policy and cannot invoke the remediation script for anything outside the four supported rules.
- **Fail-closed reviewer semantics.** Each reviewer's `ReviewResult.status` is exactly one of `PASS` (evidence was evaluated and the rule was not triggered), `FAIL` (evidence was evaluated and the rule was triggered, producing a finding), or `INCOMPLETE` (the required resource/field was missing, malformed, or otherwise unusable). Missing or malformed evidence is never treated as `PASS` — a `FAIL` or an `INCOMPLETE` from either reviewer independently blocks `SAFE_TO_SHIP`, and neither can be overridden by the other reviewer's `PASS`.
- **`scripts/final_verdict.py`** is an independent, fail-closed backstop: it requires the remediation execution artifact to report `status == "remediated"`, the remediated plan to have succeeded, and both re-reviews to `PASS` before it will ever emit `SAFE_TO_SHIP` — regardless of what Kiro Crew's own task status reports.
- **`terraform apply` is never executed anywhere in this system** — only `init`/`fmt`/`validate`/`plan`/`show`, enforced by a fixed subcommand allow-list independent of the Kiro safety hook.

Two live-verified nuances worth stating plainly, since they shape the safety design above: Kiro Crew executes every DAG node as an LLM/agent chat turn, not as a literal, deterministic subprocess — so a DAG node's own `shell:`/`prompt:` text is not a guarantee of exact execution, and a nested command's non-zero exit code inside that chat turn does not reliably block Crew's own task-level pass/fail or its downstream DAG propagation. That's why `final_verdict.py` never trusts Crew's task status or a reviewer's plain PASS as proof of a successful remediation — it independently validates a dedicated, path-confined execution artifact (`artifacts/.remediation-execution-*.json`) written only by the deterministic remediation script itself.

## Safety model

- Never runs `terraform apply`.
- Never runs `terraform destroy`.
- Never calls the AWS CLI.
- No AWS account or real AWS credentials are required or used.
- The AWS Terraform provider is used only to compute real local plans (`terraform plan`/`show -json`) — no resources are ever created.
- A `preToolUse` Kiro hook (`scripts/safety_guard.py`) blocks any shell command matching `terraform apply`, `terraform destroy`, an AWS CLI invocation, or a destructive recursive+forced filesystem delete (`rm -rf`/`-fr`, in any flag ordering) — attached to every ChangeGuard agent that holds a `shell` tool. This is backed by a second, independent layer: the deterministic scripts themselves have no code path capable of constructing an `apply`/`destroy`/`aws`/`rm -rf` invocation in the first place.
- Baseline evidence (`artifacts/baseline-plan.json`) is generated out of band, from the known-safe configuration, before any candidate is reviewed — a candidate is never compared against itself.
- Remediation approval is a genuine `force_approval` gate, resolved through the real Gateway dashboard or the Control Room UI — never simulated, never auto-approved by any script in this repository.

## Control Room (optional live UI)

`apps/control-room/` is an optional browser UI that visualizes the same workflow above and lets a human approve/reject through a web UI instead of the Gateway dashboard. It is strictly additive — the CLI/Makefile workflow works fully without it.

- **The browser never receives any Gateway credential.** All Gateway authentication happens server-side, inside the Vite dev-server's Node process (`apps/control-room/server/`), never in code shipped to the browser.
- The Gateway's `/api/approvals*` endpoints only accept the same cookie-based dashboard session a human browser would use — not the `X-Internal-Secret` machine token. The Control Room's server-side proxy mints a short-lived dashboard link token via the installed `kirocrew token` CLI (fixed executable, no shell interpolation) and exchanges it for a session cookie, entirely server-side; only that cookie is ever attached to outbound Gateway requests, and it is never returned to the browser.
- **Isolated Gateway homes are supported** via `CONTROL_ROOM_KIROCREW_HOME`, for pointing the Control Room at a Gateway running with a non-default `KIROCREW_HOME` (e.g. a disposable/isolated dev instance) — read server-side only, never exposed to the browser or serialized into any response.
- See `apps/control-room/README.md` for setup and the full security rationale.

## Prerequisites

Run `make setup` to check for everything below — it verifies binaries only and installs nothing:

- `terraform` (real local plan/init/validate; no cloud state, no `apply`)
- `python3` (standard library only — no `pip install` needed for this repository's own code)
- `kiro-cli` (runs the Security Reviewer, Reliability Reviewer, and Remediator agents)
- `kirocrew` (runs the Gateway that hosts the DAG, the approval dashboard, and the `crew-runner` agent that executes DAG nodes)

This repository does not pin specific versions of `terraform`, `kiro-cli`, or `kirocrew` — use whatever recent version `make setup` finds on your `PATH`.

## 5-minute demo

```bash
make setup
make baseline
make demo-rel                 # or: make demo-sec
```

Before running the printed command below, start a Kiro Crew gateway on the same port it expects (in a separate terminal, left running for the whole demo):

```bash
kirocrew gateway --approval interactive --port 8787
```

`make demo-rel`/`make demo-sec` print the exact next command — copy/paste it once that gateway is up:

```bash
python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage review
```

Then:

1. Open the Kiro Crew Gateway dashboard URL printed when the gateway started (or start the Control Room — `cd apps/control-room && npm install && CONTROL_ROOM_GATEWAY_URL=http://127.0.0.1:8787 npm run dev:live` — and open `http://localhost:5173`).
2. Watch Stage A run: it generates the candidate plan and invokes the Security Reviewer and Reliability Reviewer concurrently. If both `PASS`, you're done — `SAFE_TO_SHIP`, skip to step 5.
3. If Stage A reports `CHANGE_BLOCKED` (`artifacts/change-blocked-result.json` exists), plan and gate Stage B the same way:
   ```bash
   python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage remediation
   ```
4. Approve or reject the remediation when prompted — through the Gateway dashboard, or the Control Room UI.
5. Inspect `artifacts/final-verdict.json` for the outcome.
6. Run `make reset` before trying another scenario.

```bash
make reset
```

Want the security scenario instead of the reliability one? Same flow, just swap the candidate target:

```bash
make demo-sec
```

Full presenter walkthrough: `docs/demo-script.md`.

## What you should see

**Safe candidate** (no injected change): `PASS` + `PASS` → `SAFE_TO_SHIP`.

**Unsafe candidate, approved**: `CHANGE_BLOCKED` → human approval → deterministic remediation applies the fix → `PASS` + `PASS` on the remediated plan → `SAFE_TO_SHIP`.

**Unsafe candidate, rejected**: `CHANGE_BLOCKED` → human rejection → no Terraform mutation, no remediation invoked, no downstream re-review or verdict step runs.

## Live End-to-End Validation

Beyond the deterministic test suite, the full REL-001 path was exercised against a real, running Kiro Crew Gateway and a real Control Room instance:

```text
candidate desired_count: 3 -> 1
  -> Security Reviewer  PASS
  -> Reliability Reviewer  FAIL / REL-001
  -> CHANGE_BLOCKED
  -> genuine force_approval gate reached (Gateway pending approval)
  -> Control Room rendered HUMAN APPROVAL REQUIRED
  -> approved from the Control Room UI (server-side Gateway session, not a direct browser call)
  -> Gateway resumed remediation
  -> remediation-result.json: status = remediated, desired_count restored 1 -> 3
  -> real remediated Terraform plan generated
  -> Security re-review PASS, Reliability re-review PASS
  -> final-verdict.json: SAFE_TO_SHIP
  -> Control Room rendered SAFE_TO_SHIP from the real live artifact
```

Verified alongside this run: the browser received no Gateway link token, no session cookie value, no `CONTROL_ROOM_INTERNAL_SECRET`, no `CONTROL_ROOM_KIROCREW_HOME`, and no private filesystem path — every credential stayed server-side, and no Terraform mutation occurred before the human approval.

Automated verification at the same point in time: Control Room test suite — **111 passed, 0 failed**; core Python test suite — **286 passed, 24 live-only skipped, 0 failed**.

## Testing

```bash
make test
```

Runs the full deterministic `unittest` suite under `tests/` with `CHANGEGUARD_SKIP_LIVE_TESTS=1` — no live `kiro-cli` agent calls, no Kiro Crew Gateway contact, no credits spent. This is the default and the one to run while iterating.

```bash
make test-live
```

Runs the same suite *without* skipping the live-agent judgment tests — these invoke real `kiro-cli chat --agent <name> --no-interactive` calls against the Security Reviewer and Reliability Reviewer. Slower, requires `kiro-cli` to be installed and configured, and consumes real Kiro credits. Only run this when you specifically want to re-verify live agent judgment behavior.

```bash
cd apps/control-room && npm test && npm run build
```

Runs the Control Room's own Vitest suite (fixture-mode unit tests, a real Vite/Connect middleware integration test, and server-side proxy tests — no live Gateway required) and its production build.

## Repository layout

```text
.kiro/
├── agents/                # Security Reviewer, Reliability Reviewer, Remediator, and the
│                          # narrow crew-runner execution agent (Kiro CLI agent configs + prompts)
├── crew/                  # the two-stage YAML DAG (review, then gated remediation/re-review)
├── specs/change-review/   # requirements.md / design.md / tasks.md — the spec this was built from
└── steering/              # changeguard-principles.md — the non-negotiable safety/scope rules

terraform/                 # the safe demo fixture (main.tf) ChangeGuard reviews changes to
scripts/                   # deterministic Python: plan generation, evidence extraction,
                           # remediation, aggregation, the final verdict, and demo helpers
artifacts/                 # generated plan/review/verdict JSON (gitignored; baseline-plan.json
                           # is the one artifact expected to persist across runs)
tests/                     # unittest suite (fast/deterministic by default; see Testing above)
apps/control-room/         # optional browser UI (see Control Room section above)
```

## Known Kiro Crew limitations

- Kiro Crew executes every DAG node as an LLM/ACP chat turn against the run-scoped `crew-runner` agent, not as a literal deterministic subprocess — a node's `shell:`/`prompt:` text is not a guarantee of exact execution.
- A nested shell command's non-zero exit code inside that chat turn does not reliably propagate to Crew's own task-level pass/fail or block downstream DAG execution — this is why `final_verdict.py` independently validates a dedicated execution artifact rather than trusting Crew's task status.
- `kiro-cli` chat stdout is presentation-oriented (narration, tool-output echoes, and the final structured result can all share one stream) and is never treated as the authoritative signal for a reviewer's verdict or a remediation's success — both are instead persisted through a dedicated, path-confined artifact written by a narrow deterministic script the agent invokes directly.
- Crew's own per-task status can lag behind a DAG node's artifact already having been written; this repository always treats the artifact on disk, not Crew's task-status field, as ground truth.

## Design decisions and limitations

- Exactly four MVP rules (`SEC-001`, `SEC-002`, `REL-001`, `BR-001`) — this is a fixed scope decision, not a placeholder for more.
- The filesystem is the data plane between Kiro Crew DAG tasks: every node writes a fixed `artifacts/*.json` path, and every dependent node reads it explicitly. Crew does not automatically pass one task's result into the next task's prompt.
- No cloud deployment, no `terraform apply`, ever.
- ChangeGuard's purpose is targeted change review for four known-risky transitions, not general-purpose IaC security scanning.

## Built with Kiro

This project's core mechanism is Kiro, end to end: two independent Kiro CLI reviewer agents own all policy judgment; a restricted Remediator agent executes (never decides) an already-approved fix; a Kiro Crew YAML DAG drives real concurrent scheduling and the human-approval gate; and every agent operates inside an explicit Kiro permission/safety boundary — narrow shell allow-lists, a `preToolUse` safety hook blocking `terraform apply`/`destroy`/AWS CLI/destructive filesystem commands, and no agent ever holds a generic file-write tool.

## Third-party software, costs, and access

ChangeGuard AI uses open-source tooling including Terraform, the HashiCorp
AWS provider, React, Vite, TypeScript, Vitest, jsdom, and Testing Library.

Kiro CLI and Kiro Crew are required for the live agent workflow.

No AWS account, AWS credentials, paid cloud infrastructure, database,
LocalStack, or Docker are required to run the project.

The deterministic test suite can be executed locally without consuming
Kiro agent usage:

```bash
make test
```