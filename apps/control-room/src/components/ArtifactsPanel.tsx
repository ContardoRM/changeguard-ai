import type { ArtifactAvailability } from "../types/changeguard";

interface ArtifactsPanelProps {
  artifacts: ArtifactAvailability[];
}

/** Displays the fixed set of ChangeGuard result artifacts, visually
 * distinguishing files that do not exist yet. This panel never fabricates
 * a file's existence -- it renders exactly what ControlRoomState.artifacts
 * reports (either from a real filesystem check in live mode, or a
 * fixture's own hand-authored existence list). */
export function ArtifactsPanel({ artifacts }: ArtifactsPanelProps) {
  return (
    <section className="cr-panel">
      <h2 className="cr-panel-title">Artifacts</h2>
      <div className="cr-list">
        {artifacts.map((artifact) => (
          <div
            key={artifact.name}
            className={`cr-artifact-row ${artifact.exists ? "cr-artifact-row--exists" : "cr-artifact-row--missing"}`}
          >
            <span className="cr-artifact-name" title={artifact.name}>
              <span className="cr-artifact-dot" />
              <span className="cr-artifact-name-text">{artifact.name}</span>
            </span>
            <span className="cr-artifact-status">{artifact.exists ? "present" : "not yet"}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
