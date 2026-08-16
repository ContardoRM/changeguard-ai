/**
 * The single hook every view uses to obtain the current ControlRoomState.
 *
 * Fixture mode (default): returns whichever fixture the caller has
 * selected, with no network activity at all.
 *
 * Live mode (npm run dev:live only): polls the Control Room's local proxy
 * (never the Gateway directly -- see server/controlRoomProxyPlugin.ts) on
 * an interval and normalizes the result via
 * src/lib/changeguard/state.ts#buildLiveControlRoomState. Approval actions
 * call src/lib/changeguard/gateway.ts#resolveApproval, which relays to the
 * real Gateway; this hook never fabricates an approval outcome itself.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ControlRoomState, DemoScenario } from "../types/changeguard";
import { FIXTURE_STATES, type FixtureStateName } from "../fixtures/controlRoomStates";
import { fetchGatewaySnapshot, isLiveModeEnabled, resolveApproval } from "../lib/changeguard/gateway";
import { buildLiveControlRoomState, normalizeArtifactMap } from "../lib/changeguard/state";

const LIVE_POLL_INTERVAL_MS = 4000;

export interface UseControlRoomStateResult {
  state: ControlRoomState;
  isLive: boolean;
  /** Fixture-mode only: switch which fixture is currently displayed. */
  setFixture: (name: FixtureStateName) => void;
  fixtureName: FixtureStateName;
  /** Live-mode only: relays a genuine approve/reject decision to the real
   * Gateway via the local proxy. In fixture mode this is a no-op that
   * never calls anything, per Phase 7's requirement that fixture mode
   * never touches the Gateway approval API. */
  submitApproval: (action: "approve" | "reject") => Promise<void>;
  approvalError: string | null;
}

export function useControlRoomState(
  initialFixture: FixtureStateName = "SAFE_BASELINE",
  scenario: DemoScenario = "REL-001",
): UseControlRoomStateResult {
  const isLive = isLiveModeEnabled();
  const [fixtureName, setFixtureName] = useState<FixtureStateName>(initialFixture);
  const [liveState, setLiveState] = useState<ControlRoomState | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshLiveSnapshot = useCallback(async () => {
    try {
      const snapshot = await fetchGatewaySnapshot();
      const normalized = buildLiveControlRoomState(
        {
          artifacts: normalizeArtifactMap(snapshot.artifacts),
          crewReachable: snapshot.crewReachable,
          approvalApiStatus: snapshot.approvalApiStatus,
          pendingApprovalId: snapshot.pendingApprovalId,
          approvalRejected: snapshot.approvalRejected,
        },
        scenario,
      );
      setLiveState(normalized);
    } catch (error) {
      // A snapshot failure surfaces as an OFFLINE crew status rather than
      // throwing -- the UI should degrade gracefully, not crash, if the
      // proxy or Gateway is temporarily unreachable.
      setLiveState((previous) =>
        previous ? { ...previous, crewStatus: "OFFLINE" } : previous,
      );
      // eslint-disable-next-line no-console
      console.error("Control Room live snapshot failed:", error);
    }
  }, [scenario]);

  useEffect(() => {
    if (!isLive) return;
    void refreshLiveSnapshot();
    pollTimer.current = setInterval(() => void refreshLiveSnapshot(), LIVE_POLL_INTERVAL_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [isLive, refreshLiveSnapshot]);

  const submitApproval = useCallback(
    async (action: "approve" | "reject") => {
      if (!isLive) return; // Fixture mode never calls the Gateway.
      const approvalId = liveState?.approval.approvalId;
      if (!approvalId) {
        setApprovalError("No genuine pending approval is currently observed.");
        return;
      }
      setApprovalError(null);
      try {
        await resolveApproval(approvalId, action);
        await refreshLiveSnapshot();
      } catch (error) {
        setApprovalError(error instanceof Error ? error.message : "Approval action failed.");
      }
    },
    [isLive, liveState, refreshLiveSnapshot],
  );

  const state: ControlRoomState =
    isLive && liveState ? liveState : { ...FIXTURE_STATES[fixtureName], scenario };

  return {
    state,
    isLive,
    setFixture: setFixtureName,
    fixtureName,
    submitApproval,
    approvalError,
  };
}
