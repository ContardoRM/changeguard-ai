import { RULE_SCOPE, SAFE_TO_SHIP_SCOPE_NOTE } from "../types/changeguard";

/** Always displays the exact, fixed four-rule ChangeGuard MVP scope.
 * This is static display data mirroring README.md's "Supported MVP
 * rules" table verbatim -- never computed, never editable, never a
 * source of policy logic. */
export function RuleScopePanel() {
  return (
    <section className="cr-panel">
      <h2 className="cr-panel-title">Rule Scope</h2>
      <div className="cr-list">
        {RULE_SCOPE.map((rule) => (
          <div key={rule.ruleId} className="cr-rule-row">
            <span>
              <strong>{rule.ruleId}</strong> — {rule.summary}
            </span>
            <span className={`cr-severity cr-severity--${rule.severity.toLowerCase()}`}>{rule.severity}</span>
          </div>
        ))}
      </div>
      <p className="cr-footer-note">{SAFE_TO_SHIP_SCOPE_NOTE}</p>
    </section>
  );
}
