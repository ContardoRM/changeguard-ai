import type { Finding } from "../types/changeguard";

interface AgentCardProps {
  kind: "security" | "reliability" | "remediator";
  name: string;
  icon: string;
  state: string;
  rules?: string[];
  findings?: Finding[];
  subtitle?: string;
}

/** Human-readable label + short status word, derived only from the
 * already-decided `state` string ControlRoomState hands this component --
 * never a new judgment. Kept separate from the raw state string so the
 * badge can carry a short word ("OK"/"FAIL"/"SCANNING"/etc.) while
 * `data-state` (unchanged) keeps driving the CSS state-color mapping. */
function statusWord(state: string): string {
  switch (state) {
    case "PASS":
    case "COMPLETE":
      return "OK";
    case "FAIL":
    case "FAILED":
      return "FAIL";
    case "REVIEWING":
      return "SCANNING";
    case "REMEDIATING":
      return "WORKING";
    case "WAITING_FOR_APPROVAL":
      return "STANDBY";
    case "INCOMPLETE":
      return "PARTIAL";
    default:
      return state.replace(/_/g, " ");
  }
}

/** Shared visual agent card for the Security Reviewer, Reliability
 * Reviewer, and Remediator. Each agent "feels like a specialized unit"
 * via a distinct icon/HUD-ring identity applied through the
 * `cr-agent-card--<kind>` class and the `data-state` attribute, which
 * theme.css maps to color/glow per state. The status light + status word
 * are meant to read clearly from a screen-share at a glance. This
 * component never computes a state -- `state` is always passed in from
 * ControlRoomState, which is itself derived only from real ChangeGuard
 * results (or a fixture). */
export function AgentCard({ kind, name, icon, state, rules, findings, subtitle }: AgentCardProps) {
  return (
    <div className={`cr-panel cr-agent-card cr-agent-card--${kind}`} data-state={state}>
      <div className="cr-agent-ring" aria-hidden="true">
        <div className="cr-agent-icon">{icon}</div>
      </div>
      <div className="cr-agent-name">{name}</div>
      {rules && <div className="cr-agent-rules">{rules.join(" · ")}</div>}
      {subtitle && <div className="cr-agent-rules">{subtitle}</div>}
      <span className="cr-state-badge">
        <span className="cr-status-light" aria-hidden="true" />
        {statusWord(state)}
      </span>
      {findings && findings.length > 0 && (
        <div className="cr-agent-findings">
          {findings.map((finding, index) => (
            <span key={`${finding.rule_id ?? "finding"}-${index}`} className="cr-finding-tag">
              {finding.rule_id ?? "finding"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
