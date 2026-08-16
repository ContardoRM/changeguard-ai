import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentCard } from "./AgentCard";

describe("AgentCard", () => {
  it("renders a PASS state with a data-state of PASS and an OK badge", () => {
    const { container } = render(
      <AgentCard kind="security" name="Security Reviewer" icon="🛡️" state="PASS" rules={["SEC-001", "SEC-002"]} findings={[]} />,
    );
    expect(container.querySelector('[data-state="PASS"]')).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
  });

  it("renders a FAIL state and shows the finding's rule_id tag", () => {
    const { container } = render(
      <AgentCard
        kind="reliability"
        name="Reliability Reviewer"
        icon="⚙️"
        state="FAIL"
        rules={["REL-001", "BR-001"]}
        findings={[{ rule_id: "REL-001" }]}
      />,
    );
    expect(container.querySelector('[data-state="FAIL"]')).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("REL-001")).toBeInTheDocument();
  });

  it("renders IDLE with no findings shown", () => {
    const { container } = render(
      <AgentCard kind="security" name="Security Reviewer" icon="🛡️" state="IDLE" rules={["SEC-001"]} findings={[]} />,
    );
    expect(container.querySelector('[data-state="IDLE"]')).toBeInTheDocument();
    expect(screen.getByText("IDLE")).toBeInTheDocument();
  });

  it("renders REVIEWING with the SCANNING status word", () => {
    render(<AgentCard kind="reliability" name="Reliability Reviewer" icon="⚙️" state="REVIEWING" rules={["REL-001"]} findings={[]} />);
    expect(screen.getByText("SCANNING")).toBeInTheDocument();
  });
});
