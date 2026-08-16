/**
 * Normalizes real ChangeGuard artifact data (never fixtures) into a
 * ControlRoomState. This is the ONLY place live-mode artifact JSON is
 * turned into the frontend's presentation model -- React components never
 * parse artifact JSON directly (see the architecture note in
 * apps/control-room/README.md).
 *
 * This module performs NO policy judgment: every PASS/FAIL/INCOMPLETE,
 * every finding, and every SAFE_TO_SHIP/CHANGE_BLOCKED/REMEDIATION_FAILED
 * value is read verbatim from the existing ChangeGuard result artifacts.
 * It only decides how to *represent* that already-decided data (e.g.
 * mapping ReviewResult.status="FAIL" to the ReviewerVisualState "FAIL").
 */

import type {
  ArtifactFilename,
  ControlRoomState,
  DemoScenario,
  Finding,
  RemediatorVisualState,
  ReviewerVisualState,
} from "../../types/changeguard";
import {
  parseChangeBlockedResult,
  parseFinalVerdict,
  parseRemediationStageResult,
  parseReviewResult,
  type KnownArtifactName,
  type RawArtifact,
} from "./artifacts";

/** Every artifact this normalizer reads, including the two pre-remediation
 * reviewer results which are NOT part of the fixed 8-file Artifacts-panel
 * display list (that list is a display concern; this bundle is a data
 * concern and needs both the pre- and post-remediation reviewer outputs
 * to represent reviewer state correctly across the whole workflow). */
export type LiveArtifactName = KnownArtifactName;

export interface LiveArtifactBundle {
  /** Raw fetch results keyed by filename, for every artifact this
   * normalizer reads. */
  artifacts: Record<LiveArtifactName, RawArtifact>;
  /** Whether the Gateway itself was reachable when this snapshot was taken. */
  crewReachable: boolean;
  /** Distinguishes "the approvals API returned normally" ("ok") from
   * "the Gateway is unreachable" / "the approvals API rejected our
   * session" ("unreachable" | "unauthorized" | "error") /
   * "CONTROL_ROOM_GATEWAY_URL is unset" ("not_configured"). This module
   * must never treat a non-"ok" status as equivalent to "no approval
   * pending" -- see `approvalDecision` below. */
  approvalApiStatus?:
    | "ok"
    | "unauthorized"
    | "unreachable"
    | "session_acquisition_failed"
    | "error"
    | "not_configured";
  /** A genuine pending approval observed via the Gateway's approvals
   * endpoint, if any. Never fabricated -- absent unless the proxy actually
   * observed one (and approvalApiStatus was "ok"). */
  pendingApprovalId?: string;
  /** True once a genuine rejection has been observed for this run (the
   * live proxy sets this from the real Gateway/TaskRunner status text --
   * see server/controlRoomProxyPlugin.ts). Never inferred by this module. */
  approvalRejected?: boolean;
}

const PANEL_ARTIFACT_NAMES: readonly ArtifactFilename[] = [
  "baseline-plan.json",
  "candidate-plan.json",
  "change-blocked-result.json",
  "remediation-result.json",
  "remediated-plan.json",
  "security-remediated-review-result.json",
  "reliability-remediated-review-result.json",
  "final-verdict.json",
];

function reviewerVisualState(
  status: "PASS" | "FAIL" | "INCOMPLETE" | undefined,
  hasStarted: boolean,
): ReviewerVisualState {
  if (!hasStarted) return "IDLE";
  if (status === "PASS") return "PASS";
  if (status === "FAIL") return "FAIL";
  if (status === "INCOMPLETE") return "INCOMPLETE";
  return "REVIEWING";
}

function remediatorVisualState(
  approvalDecision: ControlRoomState["approval"]["decision"],
  remediationStatus: string | undefined,
): RemediatorVisualState {
  if (approvalDecision === "REJECTED") return "STANDBY";
  if (approvalDecision === "PENDING") return "WAITING_FOR_APPROVAL";
  if (remediationStatus === "remediated") return "COMPLETE";
  if (remediationStatus === "remediation_failed" || remediationStatus === "failed" || remediationStatus === "partial") {
    return "FAILED";
  }
  if (approvalDecision === "APPROVED") return "REMEDIATING";
  return "STANDBY";
}

/** Builds a Crew Activity timeline from ONLY the artifacts/approval state
 * actually observed -- never fabricated timestamps, never invented events
 * beyond what the artifact set proves happened. Order follows the fixed
 * workflow order documented in the Crew YAML files' header comments. */
function buildActivity(bundle: LiveArtifactBundle): ControlRoomState["activity"] {
  const events: ControlRoomState["activity"] = [];
  const has = (name: LiveArtifactName) => bundle.artifacts[name]?.exists === true;

  if (has("baseline-plan.json")) events.push({ id: "baseline", label: "Baseline plan generated" });
  if (has("candidate-plan.json")) events.push({ id: "candidate", label: "Candidate plan generated" });
  if (has("security-review-result.json")) events.push({ id: "sec-started", label: "Security Reviewer started" });
  if (has("reliability-review-result.json")) events.push({ id: "rel-started", label: "Reliability Reviewer started" });
  if (has("change-blocked-result.json")) events.push({ id: "blocked", label: "Change blocked" });
  if (bundle.pendingApprovalId) events.push({ id: "awaiting", label: "Awaiting human approval" });
  if (bundle.approvalRejected) events.push({ id: "rejected", label: "Approval rejected" });
  if (has("remediation-result.json")) events.push({ id: "remediation", label: "Remediation stage completed" });
  if (has("remediated-plan.json")) events.push({ id: "remediated-plan", label: "Remediated plan generated" });
  if (has("security-remediated-review-result.json") || has("reliability-remediated-review-result.json")) {
    events.push({ id: "re-review", label: "Re-review started" });
  }
  if (has("final-verdict.json")) {
    const verdict = parseFinalVerdict(bundle.artifacts["final-verdict.json"]);
    if (verdict?.status === "SAFE_TO_SHIP") events.push({ id: "safe", label: "SAFE_TO_SHIP" });
    else if (verdict) events.push({ id: "final", label: verdict.status });
  }
  return events;
}

function describeCandidateChange(scenario: DemoScenario): ControlRoomState["candidateChange"] {
  if (scenario === "SEC-001") {
    return {
      ruleId: "SEC-001",
      resource: "aws_security_group.payments_sg",
      attribute: "cidr_blocks (TCP/22)",
      baselineValue: "10.0.0.0/8",
      candidateValue: "0.0.0.0/0",
    };
  }
  return {
    ruleId: "REL-001",
    resource: "aws_ecs_service.payments_api",
    attribute: "desired_count",
    baselineValue: "3",
    candidateValue: "1",
  };
}

/** Builds the normalized ControlRoomState from a live artifact bundle.
 * `scenario` is a display selector only (which rule set the candidate
 * targets) -- it never influences which artifacts are read or how they
 * are judged. */
export function buildLiveControlRoomState(
  bundle: LiveArtifactBundle,
  scenario: DemoScenario,
): ControlRoomState {
  const { artifacts } = bundle;

  const candidatePlanExists = artifacts["candidate-plan.json"]?.exists === true;

  // Prefer the post-remediation re-review result once it exists; otherwise
  // show the pre-remediation result. Both use the identical ReviewResult
  // shape -- this is display ordering only, never a judgment.
  const activeSecurity =
    parseReviewResult(artifacts["security-remediated-review-result.json"]) ??
    parseReviewResult(artifacts["security-review-result.json"]);
  const activeReliability =
    parseReviewResult(artifacts["reliability-remediated-review-result.json"]) ??
    parseReviewResult(artifacts["reliability-review-result.json"]);

  const changeBlocked = parseChangeBlockedResult(artifacts["change-blocked-result.json"]);
  const remediationStage = parseRemediationStageResult(artifacts["remediation-result.json"]);
  const finalVerdict = parseFinalVerdict(artifacts["final-verdict.json"]);

  // A change is blocked but the approvals API itself could not be
  // reached/authenticated -- this must render as a distinct AUTH_ERROR
  // state, never silently fall through to "no approval pending"
  // (NOT_REQUIRED) nor be misrepresented as a genuine PENDING approval
  // when the proxy has no actual evidence one way or the other.
  const approvalApiBroken =
    Boolean(changeBlocked) &&
    !remediationStage &&
    !bundle.pendingApprovalId &&
    bundle.approvalApiStatus !== undefined &&
    bundle.approvalApiStatus !== "ok";

  const approvalDecision: ControlRoomState["approval"]["decision"] = bundle.approvalRejected
    ? "REJECTED"
    : bundle.pendingApprovalId
      ? "PENDING"
      : remediationStage
        ? "APPROVED"
        : approvalApiBroken
          ? "AUTH_ERROR"
          : changeBlocked
            ? "PENDING"
            : "NOT_REQUIRED";

  const findings: Finding[] = changeBlocked?.findings ?? [];
  const remediatorResult = remediationStage?.results?.find((r) => r.rule_id === scenario);

  return {
    mode: "live",
    scenario,
    crewStatus: bundle.crewReachable ? "LIVE" : "OFFLINE",
    approvalRequired: Boolean(changeBlocked),
    candidateChange: describeCandidateChange(scenario),
    securityReviewer: {
      state: reviewerVisualState(activeSecurity?.status, candidatePlanExists),
      rules: ["SEC-001", "SEC-002"],
      findings: activeSecurity?.findings ?? [],
    },
    reliabilityReviewer: {
      state: reviewerVisualState(activeReliability?.status, candidatePlanExists),
      rules: ["REL-001", "BR-001"],
      findings: activeReliability?.findings ?? [],
    },
    changeBlocked: Boolean(changeBlocked),
    approval: {
      decision: approvalDecision,
      approvalId: bundle.pendingApprovalId,
    },
    remediator: {
      state: remediatorVisualState(approvalDecision, remediationStage?.status),
      result: remediatorResult,
    },
    findings,
    artifacts: PANEL_ARTIFACT_NAMES.map((name) => ({
      name,
      exists: artifacts[name]?.exists === true,
    })),
    activity: buildActivity(bundle),
    finalVerdict,
  };
}

function assertRawArtifact(value: RawArtifact | undefined, name: LiveArtifactName): RawArtifact {
  return value ?? { name, exists: false, json: null };
}

/** Convenience builder for callers (e.g. the live gateway adapter) that
 * only have a partial fetch map -- fills in missing entries as
 * "does not exist" rather than throwing, matching how the Python side
 * treats a missing artifact as "no result yet." */
export function normalizeArtifactMap(
  partial: Partial<Record<LiveArtifactName, RawArtifact>>,
): Record<LiveArtifactName, RawArtifact> {
  const allNames: LiveArtifactName[] = [
    ...PANEL_ARTIFACT_NAMES,
    "security-review-result.json",
    "reliability-review-result.json",
  ];
  const result = {} as Record<LiveArtifactName, RawArtifact>;
  for (const name of allNames) {
    result[name] = assertRawArtifact(partial[name], name);
  }
  return result;
}
