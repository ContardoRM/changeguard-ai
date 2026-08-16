import type { Finding } from "../types/changeguard";

interface FindingsPanelProps {
  findings: Finding[];
}

function formatValue(value: Finding["baseline_value"]): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value === undefined) return "—";
  return String(value);
}

/** Reads ChangeGuard findings from existing result artifacts (via
 * ControlRoomState.findings) and displays them. No policy evaluation
 * happens here -- every field is read verbatim from the Finding record
 * design.md documents. */
export function FindingsPanel({ findings }: FindingsPanelProps) {
  return (
    <section className="cr-panel">
      <h2 className="cr-panel-title">Findings</h2>
      {findings.length === 0 ? (
        <p className="cr-list">No findings.</p>
      ) : (
        <div className="cr-list">
          {findings.map((finding, index) => (
            <div key={`${finding.rule_id ?? "finding"}-${index}`} className="cr-finding-row">
              <div className="cr-finding-row-head">
                <span>{finding.rule_id ?? "UNKNOWN"}</span>
                {finding.severity && <span className={`cr-severity cr-severity--${finding.severity.toLowerCase()}`}>{finding.severity}</span>}
              </div>
              {finding.resource && (
                <div className="cr-finding-row-reason" title={finding.resource}>
                  {finding.resource}
                </div>
              )}
              <div>
                {formatValue(finding.baseline_value)} → {formatValue(finding.candidate_value)}
              </div>
              {finding.reason && (
                <div className="cr-finding-row-reason" title={finding.reason}>
                  {finding.reason}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
