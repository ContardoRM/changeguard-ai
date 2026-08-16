/**
 * Browser-side client for the Control Room's own local proxy endpoints
 * (server/controlRoomProxyPlugin.ts), NEVER for the Kiro Crew Gateway
 * directly. The browser holds no Gateway URL, no `X-Internal-Secret`, and
 * no approval token at any point -- see the proxy plugin's module
 * docstring for the full security rationale.
 *
 * In fixture mode (the default), every function here is unreachable --
 * `isLiveModeEnabled()` gates all call sites in src/hooks, and fixture
 * mode never imports or calls this module's network functions.
 */

import type { KnownArtifactName, RawArtifact } from "./artifacts";

/** True only when the app was started with `npm run dev:live`
 * (VITE_CONTROL_ROOM_MODE=live at build time). Fixture mode is always the
 * default; live mode is always an explicit, separate opt-in command. */
export function isLiveModeEnabled(): boolean {
  // __CONTROL_ROOM_MODE__ is injected by vite.config.ts's `define` at
  // build time -- it is a compile-time constant, not a runtime toggle a
  // user could flip from the browser console.
  // eslint-disable-next-line no-undef
  return typeof __CONTROL_ROOM_MODE__ !== "undefined" && __CONTROL_ROOM_MODE__ === "live";
}

/** Mirrors server/approvalsClient.ts#ApprovalApiStatus, plus
 * "not_configured" for when CONTROL_ROOM_GATEWAY_URL itself is unset.
 * Distinguishes "no approval is currently pending" ("ok" with no
 * `pendingApprovalId`) from "the approvals API could not be reached or
 * authenticated" ("unauthorized" | "unreachable" | "error") -- the proxy
 * never fabricates `pendingApprovalId` for any status other than "ok". */
export type ApprovalApiStatus = "ok" | "unauthorized" | "unreachable" | "error" | "not_configured";

export interface GatewaySnapshot {
  crewReachable: boolean;
  artifacts: Partial<Record<KnownArtifactName, RawArtifact>>;
  approvalApiStatus: ApprovalApiStatus;
  pendingApprovalId?: string;
  approvalRejected?: boolean;
}

/** Fetches the current snapshot (artifact existence/content + any genuine
 * pending approval) from the Control Room's local proxy. Only ever called
 * when isLiveModeEnabled() is true. */
export async function fetchGatewaySnapshot(): Promise<GatewaySnapshot> {
  const response = await fetch("/__control-room/snapshot");
  if (!response.ok) {
    throw new Error(`Control Room proxy snapshot request failed: HTTP ${response.status}`);
  }
  return (await response.json()) as GatewaySnapshot;
}

/**
 * Resolves a genuine, already-pending Gateway approval via the Control
 * Room's local proxy, which in turn calls the exact same
 * `POST /api/approvals/{id}/{action}` endpoint `changeguard_launch.py`'s
 * own design documents as the sole approval-resolution mechanism (see
 * design.md's "Kiro Crew 0.2.0 Orchestration Mapping" item 6). This
 * function NEVER simulates approval, never calls the Remediator directly,
 * and never mutates Terraform itself -- it only relays the human's
 * decision to the real Gateway, exactly as a person clicking the
 * dashboard's own approve/reject button would.
 */
export async function resolveApproval(
  approvalId: string,
  action: "approve" | "reject",
): Promise<void> {
  const response = await fetch(`/__control-room/approvals/${encodeURIComponent(approvalId)}/${action}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Control Room proxy approval ${action} failed: HTTP ${response.status}`);
  }
}
