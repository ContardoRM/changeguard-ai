import { describe, expect, it } from "vitest";
import { buildLiveControlRoomState, normalizeArtifactMap } from "./state";
import type { RawArtifact } from "./artifacts";

function artifact(name: string, json: unknown | null): RawArtifact {
  return { name: name as RawArtifact["name"], exists: json !== null, json };
}

describe("buildLiveControlRoomState", () => {
  it("reports SAFE_TO_SHIP only when final-verdict.json actually says so", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "final-verdict.json": artifact("final-verdict.json", {
          status: "SAFE_TO_SHIP",
          scope: ["SEC-001", "SEC-002", "REL-001", "BR-001"],
          scope_note: "note",
          findings: [],
        }),
      }),
      crewReachable: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.finalVerdict?.status).toBe("SAFE_TO_SHIP");
  });

  it("never reports SAFE_TO_SHIP when final-verdict.json is absent", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({}),
      crewReachable: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.finalVerdict).toBeNull();
  });

  it("never reports SAFE_TO_SHIP when final-verdict.json reports a different status", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "final-verdict.json": artifact("final-verdict.json", {
          status: "REMEDIATION_FAILED",
          findings: [{ rule_id: null, reviewer: "run_remediation_stage", status: "ERROR", reason: "x" }],
        }),
      }),
      crewReachable: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.finalVerdict?.status).toBe("REMEDIATION_FAILED");
    expect(state.finalVerdict?.status).not.toBe("SAFE_TO_SHIP");
  });

  it("maps a FAIL ReviewResult to the FAIL reviewer visual state with its finding", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "candidate-plan.json": artifact("candidate-plan.json", { ok: true }),
        "reliability-review-result.json": artifact("reliability-review-result.json", {
          agent: "reliability-reviewer",
          status: "FAIL",
          findings: [
            {
              rule_id: "REL-001",
              severity: "HIGH",
              resource: "aws_ecs_service.payments_api",
              baseline_value: 3,
              candidate_value: 1,
            },
          ],
        }),
        "security-review-result.json": artifact("security-review-result.json", {
          agent: "security-reviewer",
          status: "PASS",
          findings: [],
        }),
      }),
      crewReachable: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.reliabilityReviewer.state).toBe("FAIL");
    expect(state.reliabilityReviewer.findings).toHaveLength(1);
    expect(state.securityReviewer.state).toBe("PASS");
    expect(state.changeBlocked).toBe(false); // change-blocked-result.json not present in this bundle
  });

  it("marks changeBlocked true only when change-blocked-result.json exists", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "change-blocked-result.json": artifact("change-blocked-result.json", {
          status: "CHANGE_BLOCKED",
          findings: [{ rule_id: "REL-001" }],
        }),
      }),
      crewReachable: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.changeBlocked).toBe(true);
    expect(state.approvalRequired).toBe(true);
    expect(state.findings).toHaveLength(1);
  });

  it("reflects a genuine rejection distinctly from pending/approved", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "change-blocked-result.json": artifact("change-blocked-result.json", {
          status: "CHANGE_BLOCKED",
          findings: [],
        }),
      }),
      crewReachable: true,
      approvalRejected: true,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.approval.decision).toBe("REJECTED");
    expect(state.remediator.state).toBe("STANDBY");
    expect(state.finalVerdict).toBeNull();
  });

  it("reflects crewReachable=false as OFFLINE status", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({}),
      crewReachable: false,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.crewStatus).toBe("OFFLINE");
  });

  it("distinguishes an approvals-API auth failure from 'no approval pending'", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "change-blocked-result.json": artifact("change-blocked-result.json", {
          status: "CHANGE_BLOCKED",
          findings: [{ rule_id: "REL-001" }],
        }),
      }),
      crewReachable: true,
      approvalApiStatus: "unauthorized" as const,
      // No pendingApprovalId -- proving this state is reachable even
      // without one, and must NOT collapse into NOT_REQUIRED/PENDING.
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.approval.decision).toBe("AUTH_ERROR");
    expect(state.approval.approvalId).toBeUndefined();
  });

  it("never reports AUTH_ERROR when the approvals API genuinely has no pending approval", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({}),
      crewReachable: true,
      approvalApiStatus: "ok" as const,
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.approval.decision).toBe("NOT_REQUIRED");
  });

  it("prefers a genuine pendingApprovalId over reporting AUTH_ERROR", () => {
    const bundle = {
      artifacts: normalizeArtifactMap({
        "change-blocked-result.json": artifact("change-blocked-result.json", {
          status: "CHANGE_BLOCKED",
          findings: [],
        }),
      }),
      crewReachable: true,
      approvalApiStatus: "ok" as const,
      pendingApprovalId: "task-gate-1",
    };

    const state = buildLiveControlRoomState(bundle, "REL-001");

    expect(state.approval.decision).toBe("PENDING");
    expect(state.approval.approvalId).toBe("task-gate-1");
  });
});
