# ChangeGuard Engineering Principles

ChangeGuard is an infrastructure change reviewer, not a deployment system.

## Safety

Never execute:

- `terraform apply`
- `terraform destroy`
- AWS CLI commands
- destructive filesystem operations

No real AWS credentials are required or expected.

## Evidence

A finding must be supported by comparison of two genuine Terraform plan JSON files:

1. the safe repository baseline plan;
2. the candidate (or remediated) plan.

Source code alone is not sufficient evidence for a finding.

## Human control

Analysis may be autonomous. Any modification to `terraform/main.tf` requires explicit human approval.

## Determinism

The MVP supports exactly four rule IDs:

- `SEC-001`: TCP/22 becomes public via `0.0.0.0/0`
- `SEC-002`: TCP/5432 becomes public via `0.0.0.0/0`
- `REL-001`: ECS `desired_count >= 3` becomes `1`
- `BR-001`: RDS `deletion_protection = true` becomes `false`

Unknown conditions must not be converted into findings.

## Final verdict

`SAFE_TO_SHIP` means only that the candidate passed the supported ChangeGuard MVP rules. It does not mean the infrastructure is universally safe or production-ready.
