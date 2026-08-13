# ChangeGuard AI

**AI Change Review Board for Terraform, built with Kiro Crew.**

This repository is intentionally starting at **Phase 0: Requirements** for the Ready, Spec, Ship hackathon.

The implementation is not pre-generated. The development workflow is deliberately spec-driven:

1. Requirements (already defined)
2. Kiro generates `design.md`
3. Human architecture review
4. Kiro generates `tasks.md`
5. Kiro implements the MVP
6. Run the three local demo scenarios
7. Polish the five-minute judge experience

## MVP scope

ChangeGuard will support exactly these rules:

- `SEC-001`: TCP/22 changes from an internal CIDR to `0.0.0.0/0`
- `SEC-002`: TCP/5432 changes from an internal CIDR to `0.0.0.0/0`
- `REL-001`: ECS `desired_count >= 3` changes to `1`
- `BR-001`: RDS `deletion_protection = true` changes to `false`

No other policies are part of the hackathon MVP.

## Local-only design constraint

The final demo must not require:

- an AWS account
- real AWS credentials
- LocalStack
- Docker
- deployed infrastructure

Terraform validation/planning must be real. The initial `terraform init` may require internet access to download the AWS provider when it is not cached.

Because a freshly cloned repository has no Terraform state, the product must compare **two genuine Terraform planned states** rather than pretending a plan contains historical values:

```text
safe repository config -> baseline-plan.json
judge change            -> candidate-plan.json
approved remediation    -> remediated-plan.json
```

## Current repository state

```text
.kiro/
├── agents/                    # intentionally empty until implementation
├── hooks/                     # intentionally empty until implementation
├── specs/
│   └── change-review/
│       └── requirements.md    # source of truth for Phase 0
└── steering/
    └── changeguard-principles.md

artifacts/                     # generated plan evidence later
scripts/                       # implemented by Kiro after design/tasks
terraform/
├── main.tf                    # safe demo fixture
└── versions.tf

tests/                         # implemented by Kiro after design/tasks
KIRO_START_PROMPT.md
README.md
```

## First development step

Open this repository in Kiro and use the prompt in `KIRO_START_PROMPT.md`.

The first Kiro session must stop after producing/reviewing `design.md`. Do not implement code or generate `tasks.md` in the same step.
