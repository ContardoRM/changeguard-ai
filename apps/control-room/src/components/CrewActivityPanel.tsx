import type { CrewActivityEvent } from "../types/changeguard";

interface CrewActivityPanelProps {
  activity: CrewActivityEvent[];
}

/** Renders a timeline built from real observable workflow state (see
 * src/lib/changeguard/state.ts#buildActivity). In live mode, no timestamp
 * or event here is fabricated -- every entry corresponds to an artifact
 * or approval state actually observed. Fixture-mode entries may include
 * example timestamps for visual development only (see
 * src/fixtures/controlRoomStates.ts). */
export function CrewActivityPanel({ activity }: CrewActivityPanelProps) {
  return (
    <section className="cr-panel">
      <h2 className="cr-panel-title">Crew Activity</h2>
      <div className="cr-list">
        {activity.length === 0 ? (
          <p>No activity yet.</p>
        ) : (
          activity.map((event) => (
            <div key={event.id} className="cr-activity-row">
              {event.timestamp && <time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString()}</time>}
              <span>{event.label}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
