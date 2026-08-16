interface ChangeBlockedBannerProps {
  visible: boolean;
}

/** Visually prominent CHANGE BLOCKED panel, shown only when
 * ControlRoomState.changeBlocked is true -- itself only ever true when a
 * real (or fixture) change-blocked-result.json exists. This component
 * invents no policy evaluation; it renders an existing decision. */
export function ChangeBlockedBanner({ visible }: ChangeBlockedBannerProps) {
  if (!visible) return null;
  return (
    <div className="cr-blocked-banner" role="alert">
      <span aria-hidden="true">⛔</span>
      CHANGE BLOCKED
    </div>
  );
}
