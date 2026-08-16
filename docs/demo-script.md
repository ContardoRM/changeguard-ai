# ChangeGuard AI — 5-Minute Demo

A presenter script for walking a hackathon judge through one complete ChangeGuard AI scenario, live, in about five minutes. Pairs with `README.md` (which owns full setup/reference docs) — this file only covers what to say and run during the timed walkthrough.

## Before the demo

- [ ] Repository already cloned.
- [ ] `make setup` passes (all four binaries found).
- [ ] Kiro Crew Gateway ready to start (or already running) on the port you'll use below.
- [ ] Terminal windows arranged: one for commands, one for the Gateway process/logs, one browser tab for the Gateway dashboard.
- [ ] Safe Terraform baseline restored (`git diff -- terraform/main.tf` is empty; run `make reset` if unsure).

Full setup instructions live in `README.md` — do not re-derive them here.

## 0:00–0:30 — Problem

> "A Terraform diff can be syntactically valid and still introduce a real security or reliability regression — nothing in `terraform plan`'s own output flags a widened SSH rule or a dropped ECS replica count as dangerous. ChangeGuard reviews infrastructure changes *before* they'd ever be applied, using two independent AI reviewer agents that judge a deliberately small, explicit policy scope — four rules, not a general scanner. And critically: the AI never applies a fix on its own. A human stays in control of every remediation."

(~75 spoken words.)

## 0:30–1:00 — Show the safe baseline

```bash
make baseline
```

Briefly show `terraform/main.tf`: ECS `desired_count = 3`, ingress CIDRs scoped to `10.0.0.0/8`, RDS `deletion_protection = true`. Explain that the baseline plan is generated once, out of band, from this known-safe configuration — never regenerated from the candidate.

## 1:00–1:30 — Inject a real change

```bash
make demo-rel
```

Show the diff in `terraform/main.tf`:

```text
desired_count       = 3
->
desired_count       = 1
```

Emphasize:
- This is a real Terraform source edit, not a simulated one.
- No AWS account or credentials are involved anywhere in this demo.
- No `terraform apply` happens — ChangeGuard only ever plans.

## 1:30–2:30 — AI review

```bash
python3 scripts/changeguard_launch.py \
  --gateway-url http://127.0.0.1:8787 \
  --stage review
```

While it runs, explain:
- Security Reviewer and Reliability Reviewer are independent Kiro CLI agents — each owns its own policy judgment.
- Kiro Crew schedules them concurrently in the same DAG batch.
- They compare real Baseline vs. Candidate Terraform plan evidence — not source text.
- Python only extracts facts from the plan JSON; the AI agents own the PASS/FAIL/INCOMPLETE judgment.

Expected result:

```text
Security Reviewer   = PASS
Reliability Reviewer = FAIL / REL-001
CHANGE_BLOCKED
```

## 2:30–3:15 — Human approval

```bash
python3 scripts/changeguard_launch.py \
  --gateway-url http://127.0.0.1:8787 \
  --stage remediation
```

Show the genuine pending approval in the Gateway dashboard.

> "ChangeGuard cannot mutate Terraform until a human approves this remediation."

Pause visibly before approving. Approve the request in the dashboard.

## 3:15–4:15 — Remediation and re-review

Show `terraform/main.tf` again:

```text
desired_count       = 3
```

Then show the artifacts written by this stage:

```bash
cat artifacts/remediation-result.json
cat artifacts/remediated-plan.json
cat artifacts/security-remediated-review-result.json
cat artifacts/reliability-remediated-review-result.json
```

Explain:
- The Remediator agent executes only the already-approved intent — it does not decide policy.
- Deterministic Python (`apply_remediation.py`) performs the one narrow, whitelisted mutation.
- Terraform generates a brand-new real plan from the corrected source.
- Both reviewers run again, this time against Baseline vs. Remediated evidence.

Expected:

```text
Security     = PASS
Reliability  = PASS
```

## 4:15–4:40 — Final verdict

```bash
cat artifacts/final-verdict.json
```

Expected: `SAFE_TO_SHIP`. Point explicitly to the scope note in that same file.

> "SAFE_TO_SHIP does not mean universally production-safe. It means the change passed ChangeGuard's four supported MVP rules."

## 4:40–5:00 — Close

> "What you just saw: real Terraform plan evidence, not guesses. Independent AI reviewers with a deliberately narrow policy scope. Real Kiro Crew concurrency and a genuine human approval gate. Deterministic, fail-closed remediation. And at no point did anything touch a real cloud account or run `terraform apply`."

(~48 spoken words.)

## Rejection fallback

If the judge asks "What if I reject?" — explain succinctly, without running it as part of the timed demo:

```text
Reject the approval
  -> desired_count remains 1 (never restored)
  -> Remediator never executes
  -> no remediated-plan.json is produced
  -> no re-review, no final SAFE_TO_SHIP path runs at all
```

## Demo recovery

```bash
make reset
```

Restores `terraform/main.tf` to the safe baseline and removes only the run-generated artifacts — `artifacts/baseline-plan.json` is preserved and reusable for the next run, per the current Makefile's `reset` target.

## Presenter warnings

- Never run `terraform apply` — this demo only ever plans.
- Don't improvise commands during judging; use exactly what's in this script.
- Keep the Kiro Crew Gateway already running before you start the clock.
- Prefer the REL-001 scenario (`make demo-rel`) for the main demo — the `desired_count` change is the most visually obvious.
- If the live agents are slow, narrate what's happening rather than restarting the run.
- Never claim `SAFE_TO_SHIP` is a universal security certification — it covers only the four supported MVP rules.
