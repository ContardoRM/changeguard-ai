# Kiro Phase 1 Prompt — Design from Requirements

We are building **ChangeGuard AI** for the 2026 **Ready, Spec, Ship Hackathon**.

ChangeGuard is a local, reproducible AI Change Review Board for Terraform, orchestrated with Kiro Crew.

This repository intentionally begins at the Requirements phase. Before writing implementation code, review:

- `.kiro/specs/change-review/requirements.md`
- `.kiro/steering/changeguard-principles.md`
- the safe Terraform fixture in `terraform/`

Use a **Requirements-First** workflow.

## MVP scope

The supported rules are exactly:

1. `SEC-001`: TCP port 22 changes from a non-public CIDR to `0.0.0.0/0`.
2. `SEC-002`: TCP port 5432 changes from a non-public CIDR to `0.0.0.0/0`.
3. `REL-001`: ECS `desired_count >= 3` in the baseline changes to `desired_count = 1`.
4. `BR-001`: RDS `deletion_protection = true` in the baseline changes to `false`.

Do not broaden this rule set.

## Critical architecture constraint

A freshly cloned repository has no Terraform state. Therefore ChangeGuard must not falsely claim that the candidate plan contains historical `before` values.

Design the system around three genuine Terraform planned-state artifacts:

- `artifacts/baseline-plan.json` generated from the safe repository configuration;
- `artifacts/candidate-plan.json` generated after the judge changes `terraform/main.tf`;
- `artifacts/remediated-plan.json` generated after approved remediation.

The reviewer agents compare baseline vs candidate, and later baseline vs remediated.

## Runtime requirements

The final demo must require no AWS account, no real AWS credentials, no LocalStack, no Docker, and no deployed infrastructure.

Terraform execution must be real. `terraform apply` and `terraform destroy` are forbidden.

The target runtime architecture must include:

- a ChangeGuard orchestrator;
- an independent read-only Security Reviewer custom agent;
- an independent read-only Reliability Reviewer custom agent;
- parallel specialist review whenever possible;
- a Remediator custom agent that can act only after explicit human approval;
- deterministic Python remediation rather than arbitrary LLM HCL rewriting;
- Kiro permission boundaries;
- at least one Kiro hook enforcing a safety invariant;
- a second real Terraform plan after remediation;
- stdlib-only Python scripts;
- repeatable local tests for all three scenarios.

## Your task now

1. Inspect the existing requirements for contradictions, ambiguity, missing acceptance criteria, or behavior that cannot be verified locally.
2. Update `requirements.md` only where necessary, preserving the closed MVP scope.
3. Generate `.kiro/specs/change-review/design.md`.
4. In the design, explicitly define:
   - the baseline/candidate/remediated evidence model;
   - Kiro Crew orchestration and parallel agent boundaries;
   - human approval placement;
   - custom-agent permissions;
   - hook safety behavior;
   - deterministic script interfaces;
   - test strategy;
   - the expected five-minute judge workflow.
5. Stop after `design.md` is complete.
6. Summarize the major architectural decisions and any requirement changes for human review.

Do **not** generate `tasks.md` yet.
Do **not** implement agents, hooks, Python scripts, Makefile targets, or tests yet.
Do **not** add external dependencies, a frontend, database, LocalStack, MCP server, or AWS SDK.
