import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApprovalGate } from "./ApprovalGate";

describe("ApprovalGate", () => {
  it("is not rendered when approval is not required", () => {
    render(
      <ApprovalGate
        approval={{ decision: "NOT_REQUIRED" }}
        approvalRequired={false}
        isLive={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        approvalError={null}
      />,
    );
    expect(screen.queryByText("HUMAN APPROVAL GATE")).not.toBeInTheDocument();
  });

  it("is rendered and shows disabled buttons in fixture/demo mode", () => {
    render(
      <ApprovalGate
        approval={{ decision: "PENDING", approvalId: "task-gate-1" }}
        approvalRequired={true}
        isLive={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        approvalError={null}
      />,
    );
    expect(screen.getByText("HUMAN APPROVAL GATE")).toBeInTheDocument();
    expect(screen.getByText("APPROVE")).toBeDisabled();
    expect(screen.getByText("REJECT")).toBeDisabled();
    expect(screen.getByText(/Read-only demo mode/)).toBeInTheDocument();
  });

  it("enables buttons only in live mode with a genuine pending approval id", () => {
    render(
      <ApprovalGate
        approval={{ decision: "PENDING", approvalId: "task-gate-1" }}
        approvalRequired={true}
        isLive={true}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        approvalError={null}
      />,
    );
    expect(screen.getByText("APPROVE")).not.toBeDisabled();
    expect(screen.getByText("REJECT")).not.toBeDisabled();
  });

  it("does not enable buttons in live mode without a genuine approval id", () => {
    render(
      <ApprovalGate
        approval={{ decision: "PENDING" }}
        approvalRequired={true}
        isLive={true}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        approvalError={null}
      />,
    );
    expect(screen.getByText("APPROVE")).toBeDisabled();
    expect(screen.getByText(/No genuine pending approval/)).toBeInTheDocument();
  });

  it("renders REJECTED decision distinctly", () => {
    render(
      <ApprovalGate
        approval={{ decision: "REJECTED", approvalId: "task-gate-1" }}
        approvalRequired={true}
        isLive={true}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        approvalError={null}
      />,
    );
    expect(screen.getByText("REJECTED")).toBeInTheDocument();
    expect(screen.queryByText("APPROVE")).not.toBeInTheDocument();
  });
});
