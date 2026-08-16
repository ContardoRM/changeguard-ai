import type { ControlRoomState } from "../types/changeguard";

interface ApprovalGateProps {
  approval: ControlRoomState["approval"];
  approvalRequired: boolean;
  isLive: boolean;
  onApprove: () => void;
  onReject: () => void;
  approvalError: string | null;
}

/**
 * The central Human Approval Gate.
 *
 * This component NEVER resolves an approval itself -- `onApprove`/
 * `onReject` are wired (see App.tsx) to
 * src/hooks/useControlRoomState.ts#submitApproval, which in live mode
 * relays the decision to the real Kiro Crew Gateway via the local proxy
 * (server/controlRoomProxyPlugin.ts) and in fixture mode is a guaranteed
 * no-op. There is no code path in this component, or anywhere in this
 * app, that fabricates an approval outcome or calls the Remediator
 * directly.
 *
 * When `isLive` is false (fixture/demo mode, the default), the buttons
 * are rendered disabled with a clear "read-only demo" note, per Phase 3's
 * requirement to prefer a read-only gate over a fake one.
 */
export function ApprovalGate({
  approval,
  approvalRequired,
  isLive,
  onApprove,
  onReject,
  approvalError,
}: ApprovalGateProps) {
  if (!approvalRequired) return null;

  const pending = approval.decision === "PENDING";
  const canAct = isLive && pending && Boolean(approval.approvalId);
  const liveArmed = canAct; // buttons are visually "armed" (prominent) only when a real action is possible

  return (
    <section className={`cr-gate ${pending ? "cr-gate--pending" : ""}`} data-decision={approval.decision}>
      <div className="cr-gate-icon" aria-hidden="true">
        {approval.decision === "APPROVED" ? "🔓" : approval.decision === "REJECTED" ? "✕" : "🔒"}
      </div>
      <div className="cr-gate-title">
        {approval.decision === "PENDING" && "HUMAN APPROVAL GATE"}
        {approval.decision === "APPROVED" && "APPROVED"}
        {approval.decision === "REJECTED" && "REJECTED"}
        {approval.decision === "AUTH_ERROR" && "APPROVAL STATUS UNKNOWN"}
      </div>
      {approval.decision === "AUTH_ERROR" && (
        <p className="cr-gate-readonly-note">
          The change is blocked, but the Control Room could not authenticate
          with the Gateway&apos;s approvals API. This is NOT the same as "no
          approval pending" -- check the Gateway session/credentials. No
          approval action can be taken from here until this is resolved.
        </p>
      )}
      {pending && (
        <>
          <p className="cr-gate-subtitle">Checkpoint locked. Remediation cannot proceed without a human decision.</p>
          <div className="cr-gate-actions">
            <button
              type="button"
              className={`cr-btn cr-btn--approve ${liveArmed ? "cr-btn--armed" : ""}`}
              disabled={!canAct}
              onClick={onApprove}
            >
              APPROVE
            </button>
            <button
              type="button"
              className={`cr-btn cr-btn--reject ${liveArmed ? "cr-btn--armed" : ""}`}
              disabled={!canAct}
              onClick={onReject}
            >
              REJECT
            </button>
          </div>
          {!isLive && (
            <p className="cr-gate-readonly-note">
              Read-only demo mode. Approve/Reject here do nothing — the genuine
              approval gate lives in the real Kiro Crew Gateway dashboard. Run
              with <code>npm run dev:live</code> to relay a real decision.
            </p>
          )}
          {isLive && !approval.approvalId && (
            <p className="cr-gate-readonly-note">
              No genuine pending approval observed yet from the Gateway.
            </p>
          )}
        </>
      )}
      {approvalError && <p className="cr-gate-readonly-note">{approvalError}</p>}
    </section>
  );
}
