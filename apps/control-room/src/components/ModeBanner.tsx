import type { FixtureStateName } from "../fixtures/controlRoomStates";
import { FIXTURE_STATE_ORDER } from "../fixtures/controlRoomStates";

interface ModeBannerProps {
  isLive: boolean;
  fixtureName: FixtureStateName;
  onFixtureChange: (name: FixtureStateName) => void;
}

/** Clearly distinguishes fixture/demo mode from LIVE mode, per Phase 7's
 * requirement. Fixture mode is the default (npm run dev); live mode is
 * only ever active when started via npm run dev:live. */
export function ModeBanner({ isLive, fixtureName, onFixtureChange }: ModeBannerProps) {
  if (isLive) {
    return (
      <div className="cr-mode-banner">
        <span>
          <strong>LIVE MODE</strong> — reading real artifacts and Gateway state via the local Control
          Room proxy. Never simulated.
        </span>
      </div>
    );
  }

  return (
    <div className="cr-mode-banner">
      <span>
        <strong>FIXTURE MODE</strong> — visual development only. No Gateway calls are made.
      </span>
      <select
        className="cr-fixture-select"
        value={fixtureName}
        onChange={(event) => onFixtureChange(event.target.value as FixtureStateName)}
        aria-label="Fixture state"
      >
        {FIXTURE_STATE_ORDER.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
