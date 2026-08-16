/**
 * ChangeGuard domain types.
 *
 * These types mirror the JSON shapes ChangeGuard's existing, already-reviewed
 * Python scripts actually produce (see scripts/aggregate_review.py,
 * scripts/final_verdict.py, scripts/run_remediation_stage.py, and the
 * `Finding`/`ReviewResult` shapes documented in
 * .kiro/specs/change-review/design.md's "Data Models" section). The frontend
 * NEVER computes a verdict, a severity, or a PASS/FAIL/INCOMPLETE judgment --
 * it only parses and displays whatever these existing artifacts already
 * contain.
 */

/** The four, and only four, ChangeGuard MVP rule IDs. Fixed list -- the
 * frontend never invents a fifth. */
export type RuleId = "SEC-001" | "SEC-002" | "REL-001" | "BR-001";

export type Severity = "CRITICAL" | "HIGH";

/** One reviewer-produced finding, exactly as shaped by
 * .kiro/agents/security-reviewer-prompt.md /
 * .kiro/agents/reliability-reviewer-prompt.md's Output contracts and
 * design.md's Finding record. */
export interface Finding {
  rule_id: RuleId | null;
  severity?: Severity;
  resource?: string;
  baseline_value?: string | number | boolean | string[];
  candidate_value?: string | number | boolean | string[];
  reason?: string;
  proposed_remediation?: string;
  /** Present only on aggregate_review.py's synthetic diagnostic entries
   * for a reviewer whose own result was unreadable/INCOMPLETE. */
  reviewer?: string;
  status?: string;
}

export type ReviewStatus = "PASS" | "FAIL" | "INCOMPLETE";

/** artifacts/security-review-result.json /
 * artifacts/reliability-review-result.json /
 * artifacts/security-remediated-review-result.json /
 * artifacts/reliability-remediated-review-result.json */
export interface ReviewResult {
  agent: "security-reviewer" | "reliability-reviewer";
  status: ReviewStatus;
  findings: Finding[];
  /** Present only when scripts/aggregate_review.py's _load_review_result
   * synthesized this because the real file was missing/unreadable. */
  error?: string;
}

export type ChangeBlockedStatus = "CHANGE_BLOCKED";

/** artifacts/change-blocked-result.json (scripts/aggregate_review.py) */
export interface ChangeBlockedResult {
  status: ChangeBlockedStatus;
  findings: Finding[];
}

/** artifacts/remediation-result.json, as written by
 * scripts/run_remediation_stage.py. */
export interface RemediationStageResult {
  status: "skipped" | "noop" | "remediated" | "partial" | "failed";
  reason?: string;
  results?: RemediationFindingResult[];
}

export interface RemediationFindingResult {
  status: "remediated" | "remediation_failed" | "refused";
  rule_id: RuleId | null;
  resource?: string;
  restored_value?: string | number | boolean;
  error?: string;
}

/** artifacts/final-verdict.json, as written by scripts/final_verdict.py
 * (post-remediation path) or scripts/aggregate_review.py (early
 * SAFE_TO_SHIP, no remediation needed). */
export type FinalVerdictStatus =
  | "SAFE_TO_SHIP"
  | "CHANGE_BLOCKED"
  | "REMEDIATION_FAILED";

export interface FinalVerdict {
  status: FinalVerdictStatus;
  scope?: RuleId[];
  scope_note?: string;
  findings?: Finding[];
}

/** The fixed set of artifact filenames the Control Room's Artifacts panel
 * displays, in workflow order. Mirrors the paths enumerated in
 * .kiro/crew/changeguard-workflow.yaml and
 * .kiro/crew/changeguard-workflow-remediation.yaml's header comments --
 * this list is descriptive of the existing contract, not a new one. */
export const ARTIFACT_FILENAMES = [
  "baseline-plan.json",
  "candidate-plan.json",
  "change-blocked-result.json",
  "remediation-result.json",
  "remediated-plan.json",
  "security-remediated-review-result.json",
  "reliability-remediated-review-result.json",
  "final-verdict.json",
] as const;

export type ArtifactFilename = (typeof ARTIFACT_FILENAMES)[number];

export interface ArtifactAvailability {
  name: ArtifactFilename;
  exists: boolean;
}

/** Rule scope table entry -- static, fixed display data, not policy logic.
 * Matches design.md's Human Approval Gate severity table and
 * README.md's Supported MVP rules table exactly. */
export interface RuleScopeEntry {
  ruleId: RuleId;
  summary: string;
  severity: Severity;
}

export const RULE_SCOPE: readonly RuleScopeEntry[] = [
  { ruleId: "SEC-001", summary: "TCP/22 internal → 0.0.0.0/0", severity: "CRITICAL" },
  { ruleId: "SEC-002", summary: "TCP/5432 internal → 0.0.0.0/0", severity: "CRITICAL" },
  { ruleId: "REL-001", summary: "ECS desired_count >=3 → 1", severity: "HIGH" },
  { ruleId: "BR-001", summary: "RDS deletion_protection true → false", severity: "CRITICAL" },
] as const;

export const SAFE_TO_SHIP_SCOPE_NOTE =
  "SAFE_TO_SHIP covers only supported ChangeGuard MVP rules.";

/** Agent visual/lifecycle state -- rendering-only vocabulary the Control
 * Room uses to represent each agent card. Not a ChangeGuard policy
 * concept; derived FROM ReviewResult/RemediationStageResult data. */
export type ReviewerVisualState = "IDLE" | "REVIEWING" | "PASS" | "FAIL" | "INCOMPLETE";
export type RemediatorVisualState =
  | "STANDBY"
  | "WAITING_FOR_APPROVAL"
  | "REMEDIATING"
  | "COMPLETE"
  | "FAILED";

export type DemoScenario = "REL-001" | "SEC-001";

export type ApprovalDecision = "PENDING" | "APPROVED" | "REJECTED" | "NOT_REQUIRED" | "AUTH_ERROR";

/** One human-readable Crew Activity timeline entry. In live mode this is
 * built only from artifact existence/content transitions actually
 * observed -- never fabricated. Fixtures may include example
 * timestamps for visual development only. */
export interface CrewActivityEvent {
  id: string;
  label: string;
  /** ISO 8601. Fixture-only in fixture mode; real wall-clock time when
   * an event is derived from a live observation. */
  timestamp?: string;
}

/** The normalized, presentation-ready state every Control Room component
 * consumes. Built exclusively by src/lib/changeguard/state.ts from either
 * fixture data or (in live mode) real artifact/Gateway data -- components
 * never parse raw artifact JSON or call the Gateway directly. */
export interface ControlRoomState {
  mode: "fixture" | "live";
  scenario: DemoScenario;
  crewStatus: "LIVE" | "OFFLINE";
  approvalRequired: boolean;
  candidateChange: {
    ruleId: RuleId;
    resource: string;
    attribute: string;
    baselineValue: string;
    candidateValue: string;
  };
  securityReviewer: {
    state: ReviewerVisualState;
    rules: RuleId[];
    findings: Finding[];
  };
  reliabilityReviewer: {
    state: ReviewerVisualState;
    rules: RuleId[];
    findings: Finding[];
  };
  changeBlocked: boolean;
  approval: {
    decision: ApprovalDecision;
    /** Present only when a genuine pending Gateway approval has been
     * observed in live mode. Never fabricated in fixture mode. */
    approvalId?: string;
  };
  remediator: {
    state: RemediatorVisualState;
    result?: RemediationFindingResult;
  };
  findings: Finding[];
  artifacts: ArtifactAvailability[];
  activity: CrewActivityEvent[];
  finalVerdict: FinalVerdict | null;
}
