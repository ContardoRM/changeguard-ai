# ChangeGuard AI

ChangeGuard AI is a local, reproducible Terraform change review board: it catches four specific, high-risk infrastructure regressions before they ship, blocks them until a human explicitly approves a deterministic fix, and proves the fix worked with a second real Terraform plan — all without ever calling `terraform apply` or touching a real AWS account.

## Why this exists

A Terraform diff can be syntactically valid and pass `terraform validate` while quietly introducing a real security or reliability regression — an SSH ingress rule widened to the entire internet, a database's deletion protection turned off, a service's redundancy dropped to a single task. Nothing in `terraform plan`'s own output flags that as dangerous; it's just a value that changed. ChangeGuard AI exists to catch exactly that class of regression, using genuine Terraform plan evidence and two independent AI reviewer agents, before a human ever has to approve a fix.

## How it works

```text
safe baseline (terraform/main.tf)
  -> real Terraform plan            => artifacts/baseline-plan.json
judge-introduced candidate change
  -> real Terraform plan            => artifacts/candidate-plan.json
  -> parallel Security Reviewer + Reliability Reviewer (Kiro CLI agents)
  -> aggregate
     - both PASS  -> SAFE_TO_SHIP (scoped to the four rules below)
     - any FAIL/INCOMPLETE -> CHANGE_BLOCKED, human approval required
        -> approved -> Remediator applies the one already-approved fix
           -> real Terraform plan   => artifacts/remediated-plan.json
           -> parallel re-review (Baseline vs. Remediated)
           -> SAFE_TO_SHIP if both PASS, otherwise still blocked
        -> rejected -> REMEDIATION_REJECTED, terraform/main.tf left untouched
```

Every plan referenced above (`baseline-plan.json`, `candidate-plan.json`, `remediated-plan.json`) is a real `terraform show -json` output from a real local `terraform plan` — never a fabricated diff, never an LLM's guess at what changed. A finding is only ever produced by comparing two of these genuine plans' `.change.after` values against each other.

## Supported MVP rules

ChangeGuard intentionally supports exactly four rules — no more:

| Rule | Condition | Severity |
|---|---|---|
| `SEC-001` | TCP/22 ingress CIDR: `10.0.0.0/8` → `0.0.0.0/0` | CRITICAL |
| `SEC-002` | TCP/5432 ingress CIDR: `10.0.0.0/8` → `0.0.0.0/0` | CRITICAL |
| `REL-001` | ECS `desired_count`: baseline `>= 3` → candidate `== 1` | HIGH |
| `BR-001` | RDS `deletion_protection`: `true` → `false` | CRITICAL |

ChangeGuard is deliberately **not** a generic Terraform security scanner. It does not evaluate IAM, encryption, networking beyond the two named ports, or any condition outside this fixed list. An unrecognized condition never becomes a finding — it is simply not evaluated.

## Architecture

- **Kiro Crew** provides the real orchestration substrate: a two-stage YAML DAG (`.kiro/crew/changeguard-workflow.yaml` for review, `.kiro/crew/changeguard-workflow-remediation.yaml` for remediation/re-review), genuine dependency-based concurrent scheduling for the two reviewer pairs, and the Gateway's `force_approval` human-approval gate.
- **Security Reviewer** and **Reliability Reviewer** are independent, read-only Kiro CLI agents. They own all rule-satisfaction *judgment* (`PASS`/`FAIL`/`INCOMPLETE`) — that logic lives in each agent's own prompt, not in any Python function.
- **Deterministic Python** (`scripts/security_rules.py`, `scripts/reliability_rules.py`) only extracts plain facts from plan JSON for the reviewers to judge — it never returns a verdict. `scripts/apply_remediation.py` performs the one narrow, whitelisted HCL edit a finding calls for, after human approval, and never decides whether a value is "safe."
- **Remediator** is a restricted Kiro CLI agent: it selects and executes the already-approved remediation intent (which finding, which command) — it does not judge policy and cannot invoke the remediation script for anything outside the four supported rules.
- **`scripts/final_verdict.py`** is an independent, fail-closed backstop: it requires the remediation execution artifact to report `status == "remediated"`, the remediated plan to have succeeded, and both re-reviews to `PASS` before it will ever emit `SAFE_TO_SHIP` — regardless of what Kiro Crew's own task status reports.
- **Human approval is mandatory** before any remediation: the Gateway's `force_approval` gate blocks execution until a real approval/rejection decision is made through the dashboard.
- **`terraform apply` is never executed anywhere in this system** — only `init`/`fmt`/`validate`/`plan`/`show`, enforced by a fixed subcommand allow-list independent of the Kiro safety hook.

Two live-verified nuances worth stating plainly, since they shape the safety design above: Kiro Crew executes every DAG node as an LLM/agent chat turn, not as a literal, deterministic subprocess — so a DAG node's own `shell:`/`prompt:` text is not a guarantee of exact execution, and a nested command's non-zero exit code inside that chat turn does not reliably block Crew's own task-level pass/fail or its downstream DAG propagation. That's why `final_verdict.py` never trusts Crew's task status or a reviewer's plain PASS as proof of a successful remediation — it independently validates a dedicated, path-confined execution artifact (`artifacts/.remediation-execution-*.json`) written only by the deterministic remediation script itself.

## Safety model

- Never runs `terraform apply`.
- Never runs `terraform destroy`.
- Never calls the AWS CLI.
- No AWS account or real AWS credentials are required or used.
- The AWS Terraform provider is used only to compute real local plans (`terraform plan`/`show -json`) — no resources are ever created.
- Baseline evidence (`artifacts/baseline-plan.json`) is generated out of band, from the known-safe configuration, before any candidate is reviewed — a candidate is never compared against itself.
- Remediation approval is a genuine Kiro Crew Gateway `force_approval` gate, resolved through the real dashboard approval mechanism — never simulated, never auto-approved by any script in this repository.
- `SAFE_TO_SHIP` covers only `SEC-001`, `SEC-002`, `REL-001`, and `BR-001`. It is **not** a certification of universal production-readiness — the final verdict explicitly states this scope limitation every time it is produced.

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

1. Open the Kiro Crew Gateway dashboard URL printed when the gateway started.
2. Watch Stage A run: it generates the candidate plan and invokes the Security Reviewer and Reliability Reviewer concurrently. If both `PASS`, you're done — `SAFE_TO_SHIP`, skip to step 5.
3. If Stage A reports `CHANGE_BLOCKED` (`artifacts/change-blocked-result.json` exists), plan and gate Stage B the same way:
   ```bash
   python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage remediation
   ```
4. Approve or reject the remediation when the Gateway dashboard prompts you.
5. Inspect `artifacts/final-verdict.json` for the outcome.
6. Run `make reset` before trying another scenario.

```bash
make reset
```

Want the security scenario instead of the reliability one? Same flow, just swap the candidate target:

```bash
make demo-sec
```

## What you should see

**Safe candidate** (no injected change): `PASS` + `PASS` → `SAFE_TO_SHIP`.

**Unsafe candidate, approved**: `CHANGE_BLOCKED` → human approval → Remediator applies the fix → `PASS` + `PASS` on the remediated plan → `SAFE_TO_SHIP`.

**Unsafe candidate, rejected**: `CHANGE_BLOCKED` → human rejection → no Terraform mutation, no remediation invoked, no downstream re-review or verdict step runs.

## Testing

```bash
make test
```

Runs the full deterministic `unittest` suite under `tests/` with `CHANGEGUARD_SKIP_LIVE_TESTS=1` — no live `kiro-cli` agent calls, no Kiro Crew Gateway contact, no credits spent. This is the default and the one to run while iterating.

```bash
make test-live
```

Runs the same suite *without* skipping the live-agent judgment tests — these invoke real `kiro-cli chat --agent <name> --no-interactive` calls against the Security Reviewer and Reliability Reviewer. Slower, requires `kiro-cli` to be installed and configured, and consumes real Kiro credits. Only run this when you specifically want to re-verify live agent judgment behavior.

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
```

## Design decisions and limitations

- Exactly four MVP rules (`SEC-001`, `SEC-002`, `REL-001`, `BR-001`) — this is a fixed scope decision, not a placeholder for more.
- The filesystem is the data plane between Kiro Crew DAG tasks: every node writes a fixed `artifacts/*.json` path, and every dependent node reads it explicitly. Crew does not automatically pass one task's result into the next task's prompt.
- `kiro-cli` chat stdout is presentation-oriented (narration, tool-output echoes, progress text can all share one stream) and is never treated as the authoritative signal for whether a remediation succeeded.
- Remediation success is instead proven by a dedicated, per-invocation execution artifact, confined by path to `artifacts/.remediation-execution-*.json` and validated field-by-field against the approved finding before `final_verdict.py` will ever consider `SAFE_TO_SHIP`.
- No cloud deployment, no `terraform apply`, ever.
- ChangeGuard's purpose is targeted change review for four known-risky transitions, not general-purpose IaC security scanning.

## Built with Kiro

This project's core mechanism is Kiro, end to end: two independent Kiro CLI reviewer agents own all policy judgment; a restricted Remediator agent executes (never decides) an already-approved fix; a Kiro Crew YAML DAG drives real concurrent scheduling and the human-approval gate; and every agent operates inside an explicit Kiro permission/safety boundary — narrow shell allow-lists, a `preToolUse` safety hook blocking `terraform apply`/`destroy`/AWS CLI/destructive filesystem commands, and no agent ever holds a generic file-write tool.
