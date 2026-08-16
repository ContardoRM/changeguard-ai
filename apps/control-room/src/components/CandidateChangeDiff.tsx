import type { ControlRoomState } from "../types/changeguard";

interface CandidateChangeDiffProps {
  candidateChange: ControlRoomState["candidateChange"];
  /** True once a candidate plan has actually been generated (i.e.
   * artifacts/candidate-plan.json exists) -- an existing, already-tracked
   * signal (see ControlRoomState.artifacts), not a new policy concept.
   * Before that, there is no real candidate to diff against yet, so this
   * component must not render the candidate/baseline diff -- doing so
   * would contradict a "no review has started" phase like
   * READY FOR CHANGE REVIEW. */
  hasCandidate: boolean;
}

interface DiffRow {
  lineNo: number;
  marker: " " | "-" | "+";
  code: string;
  kind: "context" | "removed" | "added";
}

/** Renders the currently-selected demo change as a compact code-review
 * style Terraform diff (line numbers, a per-line +/- marker gutter, and
 * the resource address shown separately from the diff body). Purely
 * presentational -- the values come from ControlRoomState, which itself
 * only ever reflects what the existing ChangeGuard artifacts (or, in
 * fixture mode, a hand-authored fixture) already contain.
 *
 * When `hasCandidate` is false (no candidate-plan.json yet), this renders
 * only the known-safe baseline value and an explicit "no candidate change
 * yet" note instead of a misleading baseline->candidate diff. */
export function CandidateChangeDiff({ candidateChange, hasCandidate }: CandidateChangeDiffProps) {
  const rows: DiffRow[] = hasCandidate
    ? [
        { lineNo: 1, marker: " ", kind: "context", code: `resource "${candidateChange.resource.split(".")[0]}" {` },
        { lineNo: 2, marker: "-", kind: "removed", code: `${candidateChange.attribute} = ${candidateChange.baselineValue}` },
        { lineNo: 2, marker: "+", kind: "added", code: `${candidateChange.attribute} = ${candidateChange.candidateValue}` },
        { lineNo: 3, marker: " ", kind: "context", code: "}" },
      ]
    : [
        { lineNo: 1, marker: " ", kind: "context", code: `resource "${candidateChange.resource.split(".")[0]}" {` },
        { lineNo: 2, marker: " ", kind: "context", code: `${candidateChange.attribute} = ${candidateChange.baselineValue}` },
        { lineNo: 3, marker: " ", kind: "context", code: "}" },
      ];

  return (
    <section className="cr-panel cr-diff-panel">
      <div className="cr-diff-panel-head">
        <h2 className="cr-panel-title">Candidate Change</h2>
        <span className="cr-scenario-badge">{candidateChange.ruleId}</span>
      </div>
      <div className="cr-diff-resource" title={candidateChange.resource}>
        {candidateChange.resource}
      </div>
      <div className="cr-diff">
        {rows.map((row, index) => (
          <div key={index} className={`cr-diff-line cr-diff-line--${row.kind}`}>
            <span className="cr-diff-lineno">{row.lineNo}</span>
            <span className="cr-diff-marker">{row.marker}</span>
            <span className="cr-diff-code">{row.code}</span>
          </div>
        ))}
      </div>
      {!hasCandidate && <p className="cr-diff-resource">No candidate change yet.</p>}
    </section>
  );
}
