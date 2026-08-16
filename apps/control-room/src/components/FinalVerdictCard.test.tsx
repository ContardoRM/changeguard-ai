import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FinalVerdictCard } from "./FinalVerdictCard";
import type { ControlRoomState } from "../types/changeguard";

const idleSecurity: ControlRoomState["securityReviewer"] = { state: "IDLE", rules: ["SEC-001", "SEC-002"], findings: [] };
const idleReliability: ControlRoomState["reliabilityReviewer"] = { state: "IDLE", rules: ["REL-001", "BR-001"], findings: [] };
const notRequiredApproval: ControlRoomState["approval"] = { decision: "NOT_REQUIRED" };
const standbyRemediator: ControlRoomState["remediator"] = { state: "STANDBY" };

function baseProps(overrides: Partial<Parameters<typeof FinalVerdictCard>[0]> = {}) {
  return {
    finalVerdict: null,
    approval: notRequiredApproval,
    remediator: standbyRemediator,
    securityReviewer: idleSecurity,
    reliabilityReviewer: idleReliability,
    changeBlocked: false,
    ...overrides,
  };
}

describe("FinalVerdictCard", () => {
  it("renders SAFE_TO_SHIP only when the verdict status is exactly SAFE_TO_SHIP", () => {
    render(
      <FinalVerdictCard
        {...baseProps({
          finalVerdict: {
            status: "SAFE_TO_SHIP",
            scope: ["SEC-001", "SEC-002", "REL-001", "BR-001"],
            scope_note: "scope note",
            findings: [],
          },
        })}
      />,
    );
    expect(screen.getByText("SAFE_TO_SHIP")).toBeInTheDocument();
    expect(screen.getByText("4 / 4 supported rules passed")).toBeInTheDocument();
  });

  it("never renders SAFE_TO_SHIP when finalVerdict is null and shows the READY phase label", () => {
    render(<FinalVerdictCard {...baseProps()} />);
    expect(screen.queryByText("SAFE_TO_SHIP")).not.toBeInTheDocument();
    expect(screen.getByText("READY FOR CHANGE REVIEW")).toBeInTheDocument();
  });

  it("never renders SAFE_TO_SHIP when the verdict is CHANGE_BLOCKED", () => {
    render(<FinalVerdictCard {...baseProps({ finalVerdict: { status: "CHANGE_BLOCKED", findings: [] } })} />);
    expect(screen.queryByText("SAFE_TO_SHIP")).not.toBeInTheDocument();
    expect(screen.getByText("CHANGE_BLOCKED")).toBeInTheDocument();
  });

  it("never renders SAFE_TO_SHIP when the verdict is REMEDIATION_FAILED", () => {
    render(<FinalVerdictCard {...baseProps({ finalVerdict: { status: "REMEDIATION_FAILED", findings: [] } })} />);
    expect(screen.queryByText("SAFE_TO_SHIP")).not.toBeInTheDocument();
    expect(screen.getByText("REMEDIATION_FAILED")).toBeInTheDocument();
  });

  it("always shows the scope-limitation note", () => {
    render(<FinalVerdictCard {...baseProps()} />);
    expect(screen.getByText(/covers only supported ChangeGuard MVP rules/)).toBeInTheDocument();
  });

  it("shows AI REVIEW IN PROGRESS while a reviewer is REVIEWING", () => {
    render(
      <FinalVerdictCard
        {...baseProps({
          securityReviewer: { state: "REVIEWING", rules: ["SEC-001", "SEC-002"], findings: [] },
        })}
      />,
    );
    expect(screen.getByText("AI REVIEW IN PROGRESS")).toBeInTheDocument();
  });

  it("shows CHANGE BLOCKED when changeBlocked is true with no pending approval yet", () => {
    render(<FinalVerdictCard {...baseProps({ changeBlocked: true })} />);
    expect(screen.getByText("CHANGE BLOCKED")).toBeInTheDocument();
  });

  it("shows HUMAN APPROVAL REQUIRED when approval is PENDING", () => {
    render(
      <FinalVerdictCard
        {...baseProps({ changeBlocked: true, approval: { decision: "PENDING", approvalId: "x" } })}
      />,
    );
    expect(screen.getByText("HUMAN APPROVAL REQUIRED")).toBeInTheDocument();
  });

  it("shows REMEDIATION IN PROGRESS while the Remediator is REMEDIATING", () => {
    render(
      <FinalVerdictCard
        {...baseProps({
          changeBlocked: true,
          approval: { decision: "APPROVED", approvalId: "x" },
          remediator: { state: "REMEDIATING" },
        })}
      />,
    );
    expect(screen.getByText("REMEDIATION IN PROGRESS")).toBeInTheDocument();
  });

  it("shows POST-REMEDIATION REVIEW during re-review after remediation completes", () => {
    render(
      <FinalVerdictCard
        {...baseProps({
          approval: { decision: "APPROVED", approvalId: "x" },
          remediator: { state: "COMPLETE" },
          securityReviewer: { state: "REVIEWING", rules: ["SEC-001", "SEC-002"], findings: [] },
        })}
      />,
    );
    expect(screen.getByText("POST-REMEDIATION REVIEW")).toBeInTheDocument();
  });

  it("shows REMEDIATION REJECTED when approval was rejected", () => {
    render(<FinalVerdictCard {...baseProps({ approval: { decision: "REJECTED", approvalId: "x" } })} />);
    expect(screen.getByText("REMEDIATION REJECTED")).toBeInTheDocument();
  });
});
