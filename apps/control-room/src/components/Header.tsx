import type { ControlRoomState } from "../types/changeguard";

interface HeaderProps {
  state: ControlRoomState;
}

export function Header({ state }: HeaderProps) {
  // "Required" only while a decision is genuinely still pending.
  // `approvalRequired` alone (change-blocked-result.json exists) stays
  // true even after the human has already rejected the gate -- the
  // decision itself (state.approval.decision, an existing field) is what
  // tells us whether the gate is still open or has already been
  // resolved. This does not alter the rejection workflow itself, only
  // which existing field the header chip reads.
  const approvalStillPending = state.approvalRequired && state.approval.decision === "PENDING";

  return (
    <header className="cr-header">
      <div className="cr-header-title">
        <h1>ChangeGuard AI</h1>
        <p>Mission: Terraform Change Review</p>
      </div>
      <div className="cr-chips">
        <span className={`cr-chip ${state.crewStatus === "LIVE" ? "cr-chip--live" : "cr-chip--offline"}`}>
          <span className="cr-chip-dot" />
          Crew {state.crewStatus === "LIVE" ? "Live" : "Offline"}
        </span>
        <span className={`cr-chip ${approvalStillPending ? "cr-chip--amber" : "cr-chip--muted"}`}>
          <span className="cr-chip-dot" />
          Human Approval {approvalStillPending ? "Required" : "Not Required"}
        </span>
        <span className="cr-chip cr-chip--purple">
          <span className="cr-chip-dot" />
          Scenario: {state.scenario}
        </span>
      </div>
    </header>
  );
}
