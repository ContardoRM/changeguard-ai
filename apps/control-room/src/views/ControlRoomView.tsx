import { Header } from "../components/Header";
import { ModeBanner } from "../components/ModeBanner";
import { CandidateChangeDiff } from "../components/CandidateChangeDiff";
import { AgentCard } from "../components/AgentCard";
import { ChangeBlockedBanner } from "../components/ChangeBlockedBanner";
import { ApprovalGate } from "../components/ApprovalGate";
import { FinalVerdictCard } from "../components/FinalVerdictCard";
import { FindingsPanel } from "../components/FindingsPanel";
import { ArtifactsPanel } from "../components/ArtifactsPanel";
import { CrewActivityPanel } from "../components/CrewActivityPanel";
import { RuleScopePanel } from "../components/RuleScopePanel";
import { useControlRoomState } from "../hooks/useControlRoomState";

/**
 * The single main Control Room screen (Phase 3: exactly one screen — no
 * auth, settings, or unrelated navigation). Visually represents the real
 * ChangeGuard workflow:
 *
 *   Candidate Change -> parallel Security + Reliability reviewers ->
 *   CHANGE_BLOCKED -> Human Approval Gate -> Remediator ->
 *   post-remediation parallel re-review -> SAFE_TO_SHIP
 *
 * All data displayed comes from ControlRoomState (fixture or live) via
 * useControlRoomState — this component makes no ChangeGuard policy
 * decisions and calls no Gateway endpoint directly.
 */
export function ControlRoomView() {
  const { state, isLive, setFixture, fixtureName, submitApproval, approvalError } =
    useControlRoomState();

  return (
    <div className="cr-app">
      <Header state={state} />
      <ModeBanner isLive={isLive} fixtureName={fixtureName} onFixtureChange={setFixture} />

      <div className="cr-dag-row">
        <CandidateChangeDiff
          candidateChange={state.candidateChange}
          hasCandidate={state.artifacts.some((artifact) => artifact.name === "candidate-plan.json" && artifact.exists)}
        />
        <div className={`cr-connector ${state.securityReviewer.state !== "IDLE" ? "cr-connector--active" : ""}`} />
        <AgentCard
          kind="security"
          name="Security Reviewer"
          icon="🛡️"
          state={state.securityReviewer.state}
          rules={state.securityReviewer.rules}
          findings={state.securityReviewer.findings}
        />
        <div className={`cr-connector ${state.changeBlocked || state.reliabilityReviewer.state === "PASS" ? "cr-connector--active" : ""}`} />
        <AgentCard
          kind="reliability"
          name="Reliability Reviewer"
          icon="⚙️"
          state={state.reliabilityReviewer.state}
          rules={state.reliabilityReviewer.rules}
          findings={state.reliabilityReviewer.findings}
        />
        <div className={`cr-connector ${state.approval.decision !== "NOT_REQUIRED" ? "cr-connector--active" : ""}`} />
        <AgentCard
          kind="remediator"
          name="Remediator"
          icon="🔧"
          state={state.remediator.state}
          subtitle={
            state.remediator.result
              ? `${state.remediator.result.rule_id ?? ""} → ${state.remediator.result.status}`
              : undefined
          }
        />
      </div>

      <ChangeBlockedBanner visible={state.changeBlocked} />

      <ApprovalGate
        approval={state.approval}
        approvalRequired={state.approvalRequired}
        isLive={isLive}
        onApprove={() => void submitApproval("approve")}
        onReject={() => void submitApproval("reject")}
        approvalError={approvalError}
      />

      <FinalVerdictCard
        finalVerdict={state.finalVerdict}
        approval={state.approval}
        remediator={state.remediator}
        securityReviewer={state.securityReviewer}
        reliabilityReviewer={state.reliabilityReviewer}
        changeBlocked={state.changeBlocked}
      />

      <div className="cr-bottom-grid">
        <FindingsPanel findings={state.findings} />
        <ArtifactsPanel artifacts={state.artifacts} />
        <CrewActivityPanel activity={state.activity} />
        <RuleScopePanel />
      </div>
    </div>
  );
}
