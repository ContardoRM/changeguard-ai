import type { ControlRoomState } from "../types/changeguard";
import { SAFE_TO_SHIP_SCOPE_NOTE } from "../types/changeguard";

interface FinalVerdictCardProps {
  finalVerdict: ControlRoomState["finalVerdict"];
  approval: ControlRoomState["approval"];
  remediator: ControlRoomState["remediator"];
  securityReviewer: ControlRoomState["securityReviewer"];
  reliabilityReviewer: ControlRoomState["reliabilityReviewer"];
  changeBlocked: boolean;
}

/** Pure, presentation-only derivation of a center-stage phase label from
 * fields ControlRoomState ALREADY exposes -- this invents no new
 * ChangeGuard state or policy, it only picks which already-true fact to
 * headline. Checked most-specific-first so a later, more advanced phase
 * (e.g. a real SAFE_TO_SHIP verdict) always wins over an earlier one
 * (e.g. "reviewing") whose fields may still technically be present. */
function derivePhaseLabel(props: FinalVerdictCardProps): string {
  const { finalVerdict, approval, remediator, securityReviewer, reliabilityReviewer, changeBlocked } = props;

  if (finalVerdict?.status === "SAFE_TO_SHIP") return "SAFE_TO_SHIP";
  if (finalVerdict) return finalVerdict.status; // e.g. REMEDIATION_FAILED / CHANGE_BLOCKED post-remediation
  if (approval.decision === "REJECTED") return "REMEDIATION REJECTED";
  if (approval.decision === "PENDING") return "HUMAN APPROVAL REQUIRED";
  if (remediator.state === "REMEDIATING") return "REMEDIATION IN PROGRESS";
  const reviewing = securityReviewer.state === "REVIEWING" || reliabilityReviewer.state === "REVIEWING";
  if (remediator.state === "COMPLETE" && reviewing) return "POST-REMEDIATION REVIEW";
  if (reviewing) return "AI REVIEW IN PROGRESS";
  if (changeBlocked) return "CHANGE BLOCKED";
  return "READY FOR CHANGE REVIEW";
}

/**
 * Center-stage card. Renders one meaningful, ControlRoomState-driven
 * phase label instead of one generic box -- see derivePhaseLabel above.
 * Illuminates the green SAFE_TO_SHIP visual treatment ONLY when
 * `finalVerdict?.status === "SAFE_TO_SHIP"` -- that value is read
 * verbatim from artifacts/final-verdict.json (or a fixture built to the
 * identical shape). This component performs no SAFE_TO_SHIP computation
 * of its own; see src/lib/changeguard/state.ts's module docstring for
 * the "no policy in React" boundary.
 */
export function FinalVerdictCard(props: FinalVerdictCardProps) {
  const { finalVerdict } = props;
  const isSafe = finalVerdict?.status === "SAFE_TO_SHIP";
  const isBlocked =
    finalVerdict?.status === "CHANGE_BLOCKED" ||
    finalVerdict?.status === "REMEDIATION_FAILED" ||
    props.approval.decision === "REJECTED" ||
    props.changeBlocked;

  const cardClass = isSafe
    ? "cr-verdict cr-verdict--safe"
    : isBlocked
      ? "cr-verdict cr-verdict--blocked"
      : "cr-verdict";

  const label = derivePhaseLabel(props);

  return (
    <section className={cardClass} data-testid="final-verdict-card" aria-live="polite">
      <div className="cr-verdict-title">{label}</div>
      <p className="cr-verdict-note">{finalVerdict?.scope_note ?? SAFE_TO_SHIP_SCOPE_NOTE}</p>
      {isSafe && finalVerdict?.scope && (
        <p className="cr-verdict-note">
          {finalVerdict.scope.length} / {finalVerdict.scope.length} supported rules passed
        </p>
      )}
    </section>
  );
}
