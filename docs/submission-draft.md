# ChangeGuard AI

## What I built

ChangeGuard AI is an AI Change Review Board for Terraform. It compares a known-safe baseline plan against a candidate Terraform plan, runs two independent Kiro reviewer agents — one for security, one for reliability — to judge the change, and blocks anything risky. If a change is blocked, nothing is touched until a human grants genuine approval; only then does a Remediator agent apply the already-approved fix, a new Terraform plan is generated, both reviewers run again, and ChangeGuard issues a final verdict scoped explicitly to what it actually checked.

## The problem

A Terraform diff can pass `terraform validate` and look completely reasonable while quietly introducing serious risk: SSH opened from an internal CIDR to the entire internet, a PostgreSQL port doing the same, an ECS service's replica count dropping from three to one, or an RDS instance's deletion protection getting flipped off. None of that is a syntax error. `terraform plan` will not warn you. ChangeGuard exists to catch exactly this class of regression before it ever reaches `apply`.

## How Kiro is used

Kiro is not a side tool here — it's the core mechanism:

- A **Security Reviewer** Kiro agent independently judges SEC-001/SEC-002.
- A **Reliability Reviewer** Kiro agent independently judges REL-001/BR-001.
- A **Remediator** Kiro agent executes an already-approved fix — it never decides policy on its own.
- **Kiro Crew's** YAML DAG drives real dependency-based scheduling, dispatching the two reviewer pairs concurrently.
- The **Kiro Crew Gateway's `force_approval`** mechanism is the genuine human-approval gate remediation cannot bypass.
- Every agent operates inside an explicit Kiro permission/safety boundary: narrow shell allow-lists, a `preToolUse` hook blocking `terraform apply`/`destroy`/AWS CLI/destructive filesystem commands, and no agent holding a generic file-write tool.

## What is deterministic

The AI reviewers judge policy; everything else around them is deterministic Python:

- Real Terraform plan generation (`terraform init`/`fmt`/`validate`/`plan`/`show`).
- Evidence extraction from plan JSON — plain facts only, never a verdict.
- Validation of the remediation execution artifact — success is never inferred from an agent's chat output.
- The one narrow, whitelisted HCL mutation a finding calls for.
- The final verdict check itself: fail-closed, requiring the remediation artifact, the remediated plan, and both re-reviews to all confirm success before `SAFE_TO_SHIP` is ever produced.

AI judgment is not deterministic, and this project doesn't pretend otherwise — that's precisely why every mechanical step around that judgment is.

## Human-in-the-loop safety

ChangeGuard cannot modify `terraform/main.tf` until a real, explicit approval is granted through the Kiro Crew Gateway's approval dashboard — this is a genuine blocking gate, not a formality. The reject path was verified live as well: rejecting the approval leaves Terraform completely unchanged, the Remediator never runs, and no downstream remediation, re-review, or final verdict step executes at all.

## Supported MVP rules

- `SEC-001` — SSH (TCP/22): internal CIDR → world (`0.0.0.0/0`) — **CRITICAL**
- `SEC-002` — PostgreSQL (TCP/5432): internal CIDR → world (`0.0.0.0/0`) — **CRITICAL**
- `REL-001` — ECS `desired_count`: `>= 3` → `1` — **HIGH**
- `BR-001` — RDS `deletion_protection`: `true` → `false` — **CRITICAL**

## Why the scope is intentionally small

ChangeGuard is a focused demonstration of trustworthy, AI-assisted change review — not a general-purpose IaC scanner. Four explicit, well-understood rules make it possible to reason precisely about what the reviewers judge, what the deterministic code mutates, and what the final verdict actually certifies. That precision is the point.

## Demo

```bash
make baseline
make demo-rel
```

Run the ChangeGuard review, approve the remediation in the Kiro Crew Gateway dashboard, and watch the remediation and re-review happen — ending in `SAFE_TO_SHIP`. Full instructions are in `README.md`; a minute-by-minute presenter script is in `docs/demo-script.md`.

## Key result

Real Terraform evidence, judged by independent AI reviewers, gated by genuine human control, fixed by deterministic and narrowly-scoped remediation, and verified by a fail-closed final check — with no cloud deployment and no `terraform apply` anywhere in the loop.
