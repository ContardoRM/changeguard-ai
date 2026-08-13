# Requirements Document

## Introduction

ChangeGuard AI is a local, reproducible AI Change Review Board for Terraform. It demonstrates Kiro Crew orchestration, specialized read-only reviewer agents, a mandatory human approval gate, deterministic remediation, and hook-enforced safety guards, without requiring an AWS account, real AWS credentials, LocalStack, Docker, or deployed infrastructure.

Because a freshly cloned repository has no Terraform state, ChangeGuard never infers a historical "before" value from a single plan. Every finding is supported by comparing two genuine Terraform plan JSON files:

```text
safe repository config      -> artifacts/baseline-plan.json
change under review         -> artifacts/candidate-plan.json
approved remediation applied -> artifacts/remediated-plan.json
```

The end-to-end evidence and approval flow is:

1. The safe `terraform/main.tf` produces `baseline-plan.json`.
2. A change to `terraform/main.tf` produces `candidate-plan.json`.
3. The Security Reviewer and Reliability Reviewer compare baseline vs. candidate evidence.
4. If a supported finding exists, the Orchestrator reports `CHANGE_BLOCKED` and requests human approval.
5. Upon explicit approval, the Remediator delegates a narrow, deterministic correction to `terraform/main.tf`.
6. A fresh `remediated-plan.json` is generated and re-reviewed against the baseline.
7. The Orchestrator reports `SAFE_TO_SHIP` only if the remediated plan passes both reviewers.

`SAFE_TO_SHIP` means only that the candidate or remediated configuration passed the four supported ChangeGuard MVP rules. It does not mean the infrastructure is universally safe or production-ready.

## Glossary

- **ChangeGuard System**: The overall Kiro Crew workflow that generates Terraform plan evidence, coordinates specialist review, manages human approval, and produces a final verdict.
- **Orchestrator**: The Kiro Crew agent that coordinates the ChangeGuard workflow — requesting evidence, invoking reviewer agents, aggregating results, requesting human approval, delegating approved remediation, triggering post-remediation verification, and producing the final verdict. The Orchestrator does not itself evaluate SEC-001, SEC-002, REL-001, or BR-001.
- **Security Reviewer**: The read-only agent that evaluates only SEC-001 and SEC-002 findings by comparing Terraform plan evidence.
- **Reliability Reviewer**: The read-only agent that evaluates only REL-001 and BR-001 findings by comparing Terraform plan evidence.
- **Remediator**: The agent that, only after explicit human approval, determines which supported rule ID requires correction and invokes the Remediation Script to apply that correction.
- **Terraform Plan Tool**: The deterministic local tool that runs real Terraform commands (`terraform plan -refresh=false`, `terraform show -json`) to produce plan JSON evidence. It contains no risk-detection logic.
- **Remediation Script**: The deterministic local Python script that applies one approved, narrowly scoped correction to `terraform/main.tf` for a supported rule ID. It exposes no generic arbitrary file-writing capability.
- **Safety Hook**: The Kiro hook that blocks execution of `terraform apply`, `terraform destroy`, AWS CLI commands, and destructive filesystem operations within the ChangeGuard workflow.
- **Baseline Plan**: The Terraform plan JSON generated from the safe repository configuration, written to `artifacts/baseline-plan.json`.
- **Candidate Plan**: The Terraform plan JSON generated after a change is introduced, written to `artifacts/candidate-plan.json`.
- **Remediated Plan**: The Terraform plan JSON generated after approved remediation is applied, written to `artifacts/remediated-plan.json`.
- **Finding**: A supported rule violation (SEC-001, SEC-002, REL-001, or BR-001) identified by comparing exactly two plan JSON files.
- **Human Approver**: The person who reviews a `CHANGE_BLOCKED` result and explicitly approves or rejects the proposed remediation.
- **Test Suite**: The automated test code, written using only the Python 3 standard library, that verifies each supported rule and the remediation path.

## Out of Scope

The MVP supports exactly three risk categories and four rule IDs: `SEC-001`, `SEC-002`, `REL-001`, and `BR-001`. The following are explicitly not part of the MVP and are not evaluated, generated, or enforced by the ChangeGuard System:

- IAM analysis, S3 analysis, encryption checks, cost optimization, CloudTrail, or CloudWatch analysis
- Kubernetes, OPA, Checkov, or tfsec integration
- GitHub API integration or pull-request automation
- MCP servers, Amazon Bedrock, AWS Lambda, or ECS/RDS deployment
- LocalStack or Docker Compose
- A frontend, database, authentication system, web dashboard, or telemetry backend
- Generic AWS best-practice scanning beyond the four supported rule IDs

## Requirements

### Requirement 1: Local-Only Execution Environment

**User Story:** As a judge evaluating ChangeGuard without AWS access, I want the system to run entirely without real cloud credentials or infrastructure, so that I can evaluate it safely on my own machine.

#### Acceptance Criteria

1. THE ChangeGuard System SHALL execute all demo scenarios without real AWS credentials, an AWS account, LocalStack, Docker, or deployed infrastructure.
2. WHERE the Terraform AWS provider is not already cached locally, THE ChangeGuard System SHALL require internet access during `terraform init` to download the provider.
3. THE ChangeGuard System SHALL run only on Kiro/Kiro Crew, the Terraform CLI with the AWS provider, Python 3 standard library, and Git.
4. THE ChangeGuard System SHALL NOT require a database, external SaaS dependency, AWS SDK, or any Python package outside the Python 3 standard library.

### Requirement 2: Real Terraform Evidence Generation

**User Story:** As a reviewer, I want ChangeGuard to generate real Terraform plan evidence, so that findings are grounded in actual Terraform behavior rather than assumptions.

#### Acceptance Criteria

1. WHEN the demo baseline is prepared, THE Terraform Plan Tool SHALL run `terraform plan -refresh=false` and `terraform show -json` against the safe repository configuration and SHALL write the result to `artifacts/baseline-plan.json`.
2. WHEN a review begins, THE Terraform Plan Tool SHALL run `terraform plan -refresh=false` and `terraform show -json` against the candidate configuration and SHALL write the result to `artifacts/candidate-plan.json`.
3. THE Terraform Plan Tool SHALL contain no risk-detection logic.
4. THE Terraform Plan Tool SHALL NOT execute `terraform apply` or `terraform destroy` under any circumstance.

### Requirement 3: Baseline-Relative Comparison

**User Story:** As a reviewer, I want findings based only on genuine before/after plan evidence, so that ChangeGuard never fabricates a historical state that a fresh Terraform run cannot provide.

#### Acceptance Criteria

1. THE ChangeGuard System SHALL derive baseline values only from `artifacts/baseline-plan.json`.
2. THE ChangeGuard System SHALL derive candidate values only from `artifacts/candidate-plan.json`, and remediated values only from `artifacts/remediated-plan.json`.
3. IF a finding is not supported by comparing exactly two genuine Terraform plan JSON files, THEN THE ChangeGuard System SHALL NOT report that finding.
4. THE ChangeGuard System SHALL NOT treat Terraform source code alone as sufficient evidence for a finding.

### Requirement 4: Orchestrator Workflow Coordination

**User Story:** As a workflow operator, I want a single orchestrator that coordinates evidence generation, review, approval, and remediation, so that responsibility for process control is separated from responsibility for rule evaluation.

#### Acceptance Criteria

1. THE Orchestrator SHALL request generation of Terraform plan evidence via the Terraform Plan Tool.
2. THE Orchestrator SHALL invoke the Security Reviewer and the Reliability Reviewer for each comparison cycle.
3. THE Orchestrator SHALL aggregate findings returned by the Security Reviewer and the Reliability Reviewer.
4. WHEN one or more findings exist, THE Orchestrator SHALL stop the workflow before any modification to `terraform/main.tf`.
5. THE Orchestrator SHALL request human approval before delegating remediation to the Remediator.
6. AFTER approved remediation completes, THE Orchestrator SHALL trigger post-remediation verification.
7. THE Orchestrator SHALL produce the final verdict of the workflow.
8. THE Orchestrator SHALL NOT implement SEC-001, SEC-002, REL-001, or BR-001 rule evaluation logic itself.

### Requirement 5: Security Reviewer Scope and Constraints

**User Story:** As a security-conscious engineer, I want a dedicated read-only security reviewer limited to supported rules, so that security findings stay accurate and within the demonstrated MVP scope.

#### Acceptance Criteria

1. THE Security Reviewer SHALL evaluate only SEC-001 and SEC-002.
2. THE Security Reviewer SHALL compare values from the Baseline Plan against values from the Candidate Plan or Remediated Plan to evaluate each finding.
3. THE Security Reviewer SHALL NOT modify any file.
4. THE Security Reviewer SHALL NOT execute remediation.
5. THE Security Reviewer SHALL NOT report AWS security recommendations outside SEC-001 and SEC-002.
6. IF evidence is insufficient to prove a SEC-001 or SEC-002 transition, THEN THE Security Reviewer SHALL NOT report a finding.

### Requirement 6: Reliability Reviewer Scope and Constraints

**User Story:** As a reliability-conscious engineer, I want a dedicated read-only reliability reviewer limited to supported rules, so that reliability and blast-radius findings stay accurate and within the demonstrated MVP scope.

#### Acceptance Criteria

1. THE Reliability Reviewer SHALL evaluate only REL-001 and BR-001.
2. THE Reliability Reviewer SHALL compare values from the Baseline Plan against values from the Candidate Plan or Remediated Plan to evaluate each finding.
3. THE Reliability Reviewer SHALL NOT modify any file.
4. THE Reliability Reviewer SHALL NOT execute remediation.
5. THE Reliability Reviewer SHALL NOT report reliability or availability recommendations outside REL-001 and BR-001.
6. IF evidence is insufficient to prove a REL-001 or BR-001 transition, THEN THE Reliability Reviewer SHALL NOT report a finding.
7. IF the Reliability Reviewer fails to complete evaluation of REL-001 or BR-001, THEN THE Reliability Reviewer SHALL NOT report any finding for the incomplete evaluation.

### Requirement 7: Independent Parallel Review

**User Story:** As a workflow operator, I want the Security Reviewer and Reliability Reviewer to run independently, so that neither reviewer's evaluation depends on or is delayed by the other.

#### Acceptance Criteria

1. THE Orchestrator SHALL invoke both the Security Reviewer and the Reliability Reviewer as independent agents that do not share mutable state during every comparison cycle.
2. WHERE Kiro Crew parallel execution primitives are available, THE Orchestrator SHALL invoke the Security Reviewer and the Reliability Reviewer concurrently.
3. THE finding set reported by the Security Reviewer SHALL NOT depend on the finding set reported by the Reliability Reviewer, and the finding set reported by the Reliability Reviewer SHALL NOT depend on the finding set reported by the Security Reviewer.

### Requirement 8: Human Approval Gate

**User Story:** As a human approver, I want the workflow to stop and present complete finding details before any Terraform file is modified, so that I retain explicit control over infrastructure changes.

#### Acceptance Criteria

1. WHEN one or more supported findings exist, THE Orchestrator SHALL report `CHANGE_BLOCKED`.
2. WHEN reporting `CHANGE_BLOCKED`, THE Orchestrator SHALL present, for each finding, the rule ID, severity, affected resource, baseline value, candidate value, reason, and proposed remediation.
3. THE Orchestrator SHALL NOT modify `terraform/main.tf` until the Human Approver explicitly approves the proposed remediation.
4. IF the Human Approver denies approval, THEN THE Orchestrator SHALL leave `terraform/main.tf` unmodified and SHALL report `REMEDIATION_REJECTED`.
5. IF the Human Approver denies approval, THEN THE Orchestrator SHALL NOT invoke the Remediator.

### Requirement 9: Deterministic Remediation

**User Story:** As a security-conscious engineer, I want remediation to be applied by a deterministic script rather than free-form LLM edits, so that the change to my Terraform configuration is predictable and narrowly scoped.

#### Acceptance Criteria

1. WHEN the Human Approver approves remediation, THE Remediator SHALL determine which supported rule ID requires correction.
2. THE Remediator SHALL apply the correction only by invoking the Remediation Script.
3. THE Remediation Script SHALL modify only the value in `terraform/main.tf` associated with the approved, supported rule ID.
4. THE Remediator SHALL NOT rewrite HCL directly.
5. THE Remediation Script SHALL NOT expose a generic arbitrary file-writing capability.
6. THE Remediator SHALL NOT invoke the Remediation Script without prior explicit human approval.
7. IF a finding maps to a rule ID other than SEC-001, SEC-002, REL-001, or BR-001, THEN THE Remediator SHALL block all remediation for that finding.

### Requirement 10: Post-Remediation Verification

**User Story:** As a reviewer, I want remediation verified against a freshly generated real Terraform plan, so that the final verdict reflects actual Terraform output rather than static validation alone.

#### Acceptance Criteria

1. AFTER remediation completes, THE Terraform Plan Tool SHALL generate `artifacts/remediated-plan.json` using real Terraform commands.
2. THE Orchestrator SHALL invoke the Security Reviewer and the Reliability Reviewer to compare the Baseline Plan with the Remediated Plan.
3. THE ChangeGuard System SHALL report `SAFE_TO_SHIP` only when Terraform execution succeeds and both the Security Reviewer and the Reliability Reviewer return `PASS`.
4. THE ChangeGuard System SHALL NOT rely solely on `terraform validate` to confirm remediation.
5. WHEN reporting `SAFE_TO_SHIP`, THE ChangeGuard System SHALL state that the verdict reflects only the four supported rule IDs and does not indicate the infrastructure is universally safe or production-ready.

### Requirement 11: Safety Hook Enforcement

**User Story:** As a workspace owner, I want an automated guard that blocks destructive commands, so that a misbehaving agent cannot execute a real infrastructure change or destructive file operation even by mistake.

#### Acceptance Criteria

1. THE Safety Hook SHALL block execution of any command containing `terraform apply`.
2. THE Safety Hook SHALL block execution of any command containing `terraform destroy`.
3. THE Safety Hook SHALL block execution of any AWS CLI command.
4. THE Safety Hook SHALL block execution of any destructive filesystem operation, including `rm -rf`.
5. THE Safety Hook SHALL enforce these blocks without performing SEC-001, SEC-002, REL-001, or BR-001 rule evaluation.
6. IF the Safety Hook fails to block a command described in Acceptance Criteria 1 through 4, THEN THE ChangeGuard System SHALL apply a secondary enforcement layer that prevents execution of that command.

### Requirement 12: Minimal Automated Test Coverage

**User Story:** As a maintainer, I want a minimal automated test suite using only the Python standard library, so that I can verify each supported rule and the remediation path without adding external dependencies.

#### Acceptance Criteria

1. THE Test Suite SHALL use the Python 3 standard library `unittest` module.
2. THE Test Suite SHALL verify that a safe baseline configuration produces a `PASS` result.
3. THE Test Suite SHALL verify that a SEC-001 transition (TCP/22 from an internal CIDR to `0.0.0.0/0`) produces a `FAIL` result.
4. THE Test Suite SHALL verify that a SEC-002 transition (TCP/5432 from an internal CIDR to `0.0.0.0/0`) produces a `FAIL` result.
5. THE Test Suite SHALL verify that a REL-001 transition (`desired_count` from 3 to 1) produces a `FAIL` result.
6. THE Test Suite SHALL verify that a BR-001 transition (`deletion_protection` from `true` to `false`) produces a `FAIL` result.
7. THE Test Suite SHALL verify that approved remediation corrects the associated value in `terraform/main.tf`.
8. THE Test Suite SHALL verify that the Remediated Plan produces a `PASS` result.

### Requirement 13: Judge Evaluation Experience

**User Story:** As a hackathon judge, I want to walk through one complete scenario in about five minutes, so that I can evaluate ChangeGuard efficiently.

#### Acceptance Criteria

1. THE ChangeGuard System SHALL support a judge workflow consisting of: cloning the repository, generating the Baseline Plan, injecting one supported change, running the ChangeGuard workflow via Kiro Crew, observing specialist findings, approving remediation, and observing the Remediated Plan and final verdict.
2. THE ChangeGuard System SHALL support completing the judge workflow described in Acceptance Criterion 1 in approximately five minutes.
