/**
 * Fixture ControlRoomState objects for visual development.
 *
 * These are NOT live ChangeGuard data. They exist because the real live
 * Kiro Crew workflow is slow and consumes Kiro credits (see
 * docs/hackathon-retrospective.md's finding on repeated live-verification
 * cost). Each fixture below is a hand-authored, fully-typed
 * ControlRoomState matching the exact shape src/lib/changeguard/state.ts
 * produces from real artifacts -- so every component renders identically
 * regardless of whether its state came from a fixture or a real run.
 *
 * Fixture mode NEVER calls the Gateway approval API (see
 * src/lib/changeguard/gateway.ts's fixture-mode guard).
 */

import type { ControlRoomState } from "../types/changeguard";

const REL001_CANDIDATE = {
  ruleId: "REL-001" as const,
  resource: "aws_ecs_service.payments_api",
  attribute: "desired_count",
  baselineValue: "3",
  candidateValue: "1",
};

const SEC001_CANDIDATE = {
  ruleId: "SEC-001" as const,
  resource: "aws_security_group.payments_sg",
  attribute: "cidr_blocks (TCP/22)",
  baselineValue: "10.0.0.0/8",
  candidateValue: "0.0.0.0/0",
};

const REL001_FINDING = {
  rule_id: "REL-001" as const,
  severity: "HIGH" as const,
  resource: "aws_ecs_service.payments_api",
  baseline_value: 3,
  candidate_value: 1,
  reason: "ECS desired_count is reduced to a single task, removing workload redundancy.",
  proposed_remediation: "Restore desired_count to 3.",
};

function emptyArtifacts(existing: string[] = []): ControlRoomState["artifacts"] {
  const names = [
    "baseline-plan.json",
    "candidate-plan.json",
    "change-blocked-result.json",
    "remediation-result.json",
    "remediated-plan.json",
    "security-remediated-review-result.json",
    "reliability-remediated-review-result.json",
    "final-verdict.json",
  ] as const;
  return names.map((name) => ({ name, exists: existing.includes(name) }));
}

/** 1. SAFE_BASELINE — nothing injected yet; baseline plan exists, nothing
 * else has run. */
export const SAFE_BASELINE: ControlRoomState = {
  mode: "fixture",
  scenario: "REL-001",
  crewStatus: "OFFLINE",
  approvalRequired: false,
  candidateChange: REL001_CANDIDATE,
  securityReviewer: { state: "IDLE", rules: ["SEC-001", "SEC-002"], findings: [] },
  reliabilityReviewer: { state: "IDLE", rules: ["REL-001", "BR-001"], findings: [] },
  changeBlocked: false,
  approval: { decision: "NOT_REQUIRED" },
  remediator: { state: "STANDBY" },
  findings: [],
  artifacts: emptyArtifacts(["baseline-plan.json"]),
  activity: [
    { id: "a1", label: "Baseline plan generated", timestamp: "2026-08-16T10:00:00Z" },
  ],
  finalVerdict: null,
};

/** 2. REVIEWING — Stage A in progress, both reviewers concurrently
 * dispatched. */
export const REVIEWING: ControlRoomState = {
  ...SAFE_BASELINE,
  crewStatus: "LIVE",
  securityReviewer: { state: "REVIEWING", rules: ["SEC-001", "SEC-002"], findings: [] },
  reliabilityReviewer: { state: "REVIEWING", rules: ["REL-001", "BR-001"], findings: [] },
  artifacts: emptyArtifacts(["baseline-plan.json", "candidate-plan.json"]),
  activity: [
    ...SAFE_BASELINE.activity,
    { id: "a2", label: "Candidate plan generated", timestamp: "2026-08-16T10:01:00Z" },
    { id: "a3", label: "Security Reviewer started", timestamp: "2026-08-16T10:01:05Z" },
    { id: "a4", label: "Reliability Reviewer started", timestamp: "2026-08-16T10:01:05Z" },
  ],
};

/** 3. CHANGE_BLOCKED_REL001 — Security PASS, Reliability FAIL/REL-001. */
export const CHANGE_BLOCKED_REL001: ControlRoomState = {
  ...REVIEWING,
  securityReviewer: { state: "PASS", rules: ["SEC-001", "SEC-002"], findings: [] },
  reliabilityReviewer: {
    state: "FAIL",
    rules: ["REL-001", "BR-001"],
    findings: [REL001_FINDING],
  },
  changeBlocked: true,
  findings: [REL001_FINDING],
  artifacts: emptyArtifacts([
    "baseline-plan.json",
    "candidate-plan.json",
    "change-blocked-result.json",
  ]),
  activity: [
    ...REVIEWING.activity,
    { id: "a5", label: "Security Reviewer: PASS", timestamp: "2026-08-16T10:01:20Z" },
    { id: "a6", label: "Reliability Reviewer detected REL-001", timestamp: "2026-08-16T10:01:22Z" },
    { id: "a7", label: "Change blocked", timestamp: "2026-08-16T10:01:23Z" },
  ],
};

/** 4. WAITING_APPROVAL — genuine pending Gateway approval reached. */
export const WAITING_APPROVAL: ControlRoomState = {
  ...CHANGE_BLOCKED_REL001,
  approvalRequired: true,
  approval: { decision: "PENDING", approvalId: "task-gate-1-fixture" },
  remediator: { state: "WAITING_FOR_APPROVAL" },
  activity: [
    ...CHANGE_BLOCKED_REL001.activity,
    { id: "a8", label: "Awaiting human approval", timestamp: "2026-08-16T10:01:30Z" },
  ],
};

/** 5. REMEDIATING — approved; Remediator actively running. */
export const REMEDIATING: ControlRoomState = {
  ...WAITING_APPROVAL,
  approval: { decision: "APPROVED", approvalId: "task-gate-1-fixture" },
  remediator: { state: "REMEDIATING" },
  activity: [
    ...WAITING_APPROVAL.activity,
    { id: "a9", label: "Remediation approved", timestamp: "2026-08-16T10:02:00Z" },
    { id: "a10", label: "Remediator started", timestamp: "2026-08-16T10:02:01Z" },
  ],
};

/** 6. REREVIEWING — remediation complete, re-review of Baseline vs.
 * Remediated in progress. */
export const REREVIEWING: ControlRoomState = {
  ...REMEDIATING,
  remediator: {
    state: "COMPLETE",
    result: {
      status: "remediated",
      rule_id: "REL-001",
      resource: "aws_ecs_service.payments_api",
      restored_value: 3,
    },
  },
  securityReviewer: { state: "REVIEWING", rules: ["SEC-001", "SEC-002"], findings: [] },
  reliabilityReviewer: { state: "REVIEWING", rules: ["REL-001", "BR-001"], findings: [] },
  changeBlocked: false,
  findings: [],
  artifacts: emptyArtifacts([
    "baseline-plan.json",
    "candidate-plan.json",
    "change-blocked-result.json",
    "remediation-result.json",
    "remediated-plan.json",
  ]),
  activity: [
    ...REMEDIATING.activity,
    { id: "a11", label: "Remediation completed", timestamp: "2026-08-16T10:02:30Z" },
    { id: "a12", label: "Re-review started", timestamp: "2026-08-16T10:02:31Z" },
  ],
};

/** 7. SAFE_TO_SHIP — both re-reviews PASS, final verdict is SAFE_TO_SHIP. */
export const SAFE_TO_SHIP: ControlRoomState = {
  ...REREVIEWING,
  crewStatus: "OFFLINE",
  approvalRequired: false,
  securityReviewer: { state: "PASS", rules: ["SEC-001", "SEC-002"], findings: [] },
  reliabilityReviewer: { state: "PASS", rules: ["REL-001", "BR-001"], findings: [] },
  artifacts: emptyArtifacts([
    "baseline-plan.json",
    "candidate-plan.json",
    "change-blocked-result.json",
    "remediation-result.json",
    "remediated-plan.json",
    "security-remediated-review-result.json",
    "reliability-remediated-review-result.json",
    "final-verdict.json",
  ]),
  activity: [
    ...REREVIEWING.activity,
    { id: "a13", label: "SAFE_TO_SHIP", timestamp: "2026-08-16T10:03:00Z" },
  ],
  finalVerdict: {
    status: "SAFE_TO_SHIP",
    scope: ["SEC-001", "SEC-002", "REL-001", "BR-001"],
    scope_note:
      "SAFE_TO_SHIP means only that the candidate passed the supported ChangeGuard MVP rules. It does not mean the infrastructure is universally safe or production-ready.",
    findings: [],
  },
};

/** 8. REJECTED — human denied the approval gate; no mutation, no
 * downstream remediation/re-review/verdict. */
export const REJECTED: ControlRoomState = {
  ...WAITING_APPROVAL,
  approval: { decision: "REJECTED", approvalId: "task-gate-1-fixture" },
  remediator: { state: "STANDBY" },
  activity: [
    ...WAITING_APPROVAL.activity,
    { id: "a9r", label: "Approval rejected", timestamp: "2026-08-16T10:02:00Z" },
  ],
  finalVerdict: null,
};

/** SEC-001 primary demo variant of CHANGE_BLOCKED, for the alternate
 * scenario selector. */
export const CHANGE_BLOCKED_SEC001: ControlRoomState = {
  ...CHANGE_BLOCKED_REL001,
  scenario: "SEC-001",
  candidateChange: SEC001_CANDIDATE,
  securityReviewer: {
    state: "FAIL",
    rules: ["SEC-001", "SEC-002"],
    findings: [
      {
        rule_id: "SEC-001",
        severity: "CRITICAL",
        resource: "aws_security_group.payments_sg",
        baseline_value: ["10.0.0.0/8"],
        candidate_value: ["0.0.0.0/0"],
        reason: "TCP/22 ingress became public via 0.0.0.0/0.",
        proposed_remediation: "Restore cidr_blocks on the port-22 ingress rule to 10.0.0.0/8.",
      },
    ],
  },
  reliabilityReviewer: { state: "PASS", rules: ["REL-001", "BR-001"], findings: [] },
  findings: [
    {
      rule_id: "SEC-001",
      severity: "CRITICAL",
      resource: "aws_security_group.payments_sg",
      baseline_value: ["10.0.0.0/8"],
      candidate_value: ["0.0.0.0/0"],
      reason: "TCP/22 ingress became public via 0.0.0.0/0.",
      proposed_remediation: "Restore cidr_blocks on the port-22 ingress rule to 10.0.0.0/8.",
    },
  ],
};

export const FIXTURE_STATES = {
  SAFE_BASELINE,
  REVIEWING,
  CHANGE_BLOCKED_REL001,
  WAITING_APPROVAL,
  REMEDIATING,
  REREVIEWING,
  SAFE_TO_SHIP,
  REJECTED,
  CHANGE_BLOCKED_SEC001,
} as const;

export type FixtureStateName = keyof typeof FIXTURE_STATES;

export const FIXTURE_STATE_ORDER: FixtureStateName[] = [
  "SAFE_BASELINE",
  "REVIEWING",
  "CHANGE_BLOCKED_REL001",
  "WAITING_APPROVAL",
  "REMEDIATING",
  "REREVIEWING",
  "SAFE_TO_SHIP",
  "REJECTED",
];
