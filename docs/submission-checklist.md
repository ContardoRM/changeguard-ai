# ChangeGuard AI — Submission Checklist

## Repository

- [ ] Public repository URL: `<fill in before submitting — e.g. https://github.com/<org>/changeguard-ai>`
- [x] Main branch clean (`git status --short` reports no pending changes at time of this checklist).
- [x] `README.md` present, judge-facing, and up to date.
- [x] `docs/demo-script.md` present (5-minute presenter script).
- [x] `Makefile` present (`setup`, `baseline`, `demo-rel`, `demo-sec`, `reset`, `test`, `test-live`, `help`).
- [x] `terraform/.terraform.lock.hcl` present and committed.
- [x] No secrets committed — the only credential-shaped string in the repo is the fixed demo fixture's fake `password = "changeguard-demo-password"` in `terraform/main.tf` (and its mirrored test fixtures), never a real AWS key or token.
- [x] No temporary live-run artifacts committed — `artifacts/*.json` is gitignored except `artifacts/.gitkeep`; `terraform/.terraform/` and `*.tfstate*`/`*.tfplan` are gitignored.
- [x] No Terraform source diff from the safe baseline (`git diff -- terraform/main.tf` is empty).
- [x] `TASK_PROGRESS.md` (an ephemeral Kiro Crew checkpoint file previously captured by a live Crew run using this repository as its working directory) has been removed from version control and is now covered by a narrow `/TASK_PROGRESS.md` `.gitignore` entry, so it cannot be recommitted accidentally.

## Judge quick-start

Exactly the commands documented in `README.md`'s "5-minute demo" section:

```bash
make setup
make baseline
make demo-rel
```

```bash
kirocrew gateway --approval interactive --port 8787
```

```bash
python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage review
```

```bash
python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage remediation
```

```bash
make reset
```

## Verified live scenarios

Three end-to-end scenarios were verified against a real, running Kiro Crew Gateway.

**1. Safe candidate**
- Security Reviewer → `PASS`
- Reliability Reviewer → `PASS`
- Result: `SAFE_TO_SHIP`

**2. Unsafe REL-001 candidate, human Approve**
- `CHANGE_BLOCKED` (Reliability Reviewer `FAIL` / `REL-001`)
- Genuine `force_approval` gate reached and approved through the real Gateway dashboard
- Remediator invoked; `desired_count` restored `1 → 3`
- New Remediated Plan generated
- Re-review: Security `PASS`, Reliability `PASS`
- Result: `SAFE_TO_SHIP`

**3. Unsafe REL-001 candidate, human Reject**
- `CHANGE_BLOCKED` reached the same way
- Genuine approval rejection through the real Gateway dashboard
- No Terraform mutation occurred
- No `remediation-result.json` produced
- No remediated plan produced
- No final `SAFE_TO_SHIP` path ran at all

## Safety claims allowed in submission

Claims supported by the implementation and live-verified evidence:

- Never runs `terraform apply`.
- Never runs `terraform destroy`.
- Never calls the AWS CLI.
- No AWS account or real AWS credentials required.
- Every plan compared is a real, local `terraform plan`/`show -json` output.
- Security Reviewer and Reliability Reviewer are independent Kiro CLI agents.
- Kiro Crew dispatches the reviewer pairs concurrently — real, not simulated.
- A genuine human approval gate (Gateway `force_approval`) must be granted before any remediation runs.
- Remediation is a narrow, deterministic, whitelisted HCL edit — not free-form AI-written Terraform.
- The final verdict is an independent, fail-closed check (validates the remediation execution artifact, plan success, and both re-reviews before ever reporting `SAFE_TO_SHIP`).
- `SAFE_TO_SHIP` is scoped to exactly four rules: `SEC-001`, `SEC-002`, `REL-001`, `BR-001`.

Claims **not** to make:

- ChangeGuard is a generic Terraform security scanner. (It is not — four fixed rules only.)
- `SAFE_TO_SHIP` is a universal production-safety certification. (It is not — scoped to the four rules above.)
- Kiro Crew executes DAG shell nodes deterministically as literal subprocesses. (It does not — every node runs as an LLM/agent chat turn.)
- A nested shell command's non-zero exit code reliably stops Kiro Crew's own DAG propagation. (It does not — that's precisely why the fail-closed final verdict exists as an independent check.)
- ChangeGuard performs any cloud deployment or remediation against a real AWS account. (It does not — local Terraform planning only, no `apply`.)

## Submission links

- [ ] GitHub repository: `<fill in>`
- [ ] Demo video: `<fill in>`
- [ ] Live demo URL (if required by the challenge): `<fill in, or mark N/A — this is a local-only demo by design>`
- [ ] DEV.to / challenge write-up post (if required): `<fill in>`
- [ ] Screenshots/GIFs (if required): `<fill in>`

No URLs are fabricated here — every entry above is a placeholder to be filled in before the actual submission, since none currently exist in the repository.
