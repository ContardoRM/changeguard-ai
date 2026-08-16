/**
 * ChangeGuard Control Room — server-side Gateway `/api/approvals*` client.
 *
 * Calls the Gateway's approval endpoints using ONLY the cookie-based
 * dashboard session `GatewaySessionManager` provides (see
 * `gatewaySession.ts`'s module docstring for why `X-Internal-Secret`
 * cannot be used here). `X-Internal-Secret` behavior for the
 * taskrunner/artifact endpoints elsewhere in
 * `controlRoomProxyPlugin.ts` is completely unaffected by this module.
 *
 * On a 401/403 (expired or revoked session cookie), this client discards
 * the cached cookie, mints/exchanges exactly ONE fresh session, and
 * retries the SAME call exactly once. A second auth failure is reported
 * to the caller as `"unauthorized"` -- this module never loops and never
 * fabricates a successful result.
 */

import { SessionAcquisitionError, type GatewaySessionManager, type HttpRequestFn } from "./gatewaySession";

/** Coarse-grained, browser-safe classification of the approvals API's
 * reachability/auth state. Deliberately excludes any credential value --
 * see `controlRoomProxyPlugin.ts`'s snapshot handler for how this is
 * surfaced to the browser.
 *
 * `"session_acquisition_failed"` is distinct from `"unreachable"`: the
 * Gateway itself may be perfectly reachable while the server-side
 * `kirocrew token` mint/exchange fails for some OTHER reason (most
 * commonly a `KIROCREW_HOME` mismatch against an isolated Gateway
 * instance -- see `gatewaySession.ts`'s `SessionAcquisitionError`).
 * Collapsing that into `"unreachable"` would misleadingly suggest a
 * network problem when the actual cause is a session/credential-
 * acquisition problem the Control Room proxy needs a human to fix
 * (e.g. by setting `CONTROL_ROOM_KIROCREW_HOME`). */
export type ApprovalApiStatus =
  | "ok"
  | "unauthorized"
  | "unreachable"
  | "session_acquisition_failed"
  | "error";

export interface PendingApproval {
  id: string;
}

export interface FetchApprovalsResult {
  status: ApprovalApiStatus;
  approvals: PendingApproval[];
}

export interface ResolveApprovalResult {
  status: ApprovalApiStatus;
  ok: boolean;
}

function isAuthFailure(httpStatus: number): boolean {
  return httpStatus === 401 || httpStatus === 403;
}

/**
 * GET /api/approvals via the cached (or freshly minted) dashboard session
 * cookie. Retries exactly once, with a freshly minted session, if the
 * first attempt is rejected as unauthorized.
 */
export async function fetchPendingApprovalsWithSession(
  gatewayUrl: string,
  session: GatewaySessionManager,
  httpRequest: HttpRequestFn,
  timeoutMs: number,
): Promise<FetchApprovalsResult> {
  const url = `${gatewayUrl.replace(/\/+$/, "")}/api/approvals`;

  const attempt = async (): Promise<
    { httpStatus: number; body: string } | { failure: "unreachable" | "session_acquisition_failed" }
  > => {
    try {
      const cookieHeader = await session.getSessionCookieHeader();
      const response = await httpRequest(url, "GET", { Cookie: cookieHeader }, timeoutMs);
      return { httpStatus: response.status, body: response.body };
    } catch (error) {
      return { failure: error instanceof SessionAcquisitionError ? "session_acquisition_failed" : "unreachable" };
    }
  };

  const first = await attempt();
  if ("failure" in first) {
    return { status: first.failure, approvals: [] };
  }

  if (isAuthFailure(first.httpStatus)) {
    session.invalidate();
    const second = await attempt();
    if ("failure" in second) {
      return { status: second.failure, approvals: [] };
    }
    if (isAuthFailure(second.httpStatus)) {
      return { status: "unauthorized", approvals: [] };
    }
    return finishFetchResult(second.httpStatus, second.body);
  }

  return finishFetchResult(first.httpStatus, first.body);
}

function finishFetchResult(httpStatus: number, body: string): FetchApprovalsResult {
  if (httpStatus < 200 || httpStatus >= 300) {
    return { status: "error", approvals: [] };
  }
  try {
    const parsed = JSON.parse(body) as unknown;
    if (Array.isArray(parsed)) {
      const approvals = parsed
        .filter((entry): entry is PendingApproval => typeof entry === "object" && entry !== null && typeof (entry as { id?: unknown }).id === "string")
        .map((entry) => ({ id: entry.id }));
      return { status: "ok", approvals };
    }
    return { status: "error", approvals: [] };
  } catch {
    return { status: "error", approvals: [] };
  }
}

/**
 * POST /api/approvals/{id}/{action} via the cached (or freshly minted)
 * dashboard session cookie, with the identical single-retry-on-401/403
 * behavior as `fetchPendingApprovalsWithSession`.
 */
export async function resolveApprovalWithSession(
  gatewayUrl: string,
  session: GatewaySessionManager,
  httpRequest: HttpRequestFn,
  timeoutMs: number,
  approvalId: string,
  action: "approve" | "reject",
): Promise<ResolveApprovalResult> {
  const url = `${gatewayUrl.replace(/\/+$/, "")}/api/approvals/${encodeURIComponent(approvalId)}/${action}`;

  const attempt = async (): Promise<
    { httpStatus: number } | { failure: "unreachable" | "session_acquisition_failed" }
  > => {
    try {
      const cookieHeader = await session.getSessionCookieHeader();
      const response = await httpRequest(url, "POST", { Cookie: cookieHeader }, timeoutMs);
      return { httpStatus: response.status };
    } catch (error) {
      return { failure: error instanceof SessionAcquisitionError ? "session_acquisition_failed" : "unreachable" };
    }
  };

  const first = await attempt();
  if ("failure" in first) {
    return { status: first.failure, ok: false };
  }

  if (isAuthFailure(first.httpStatus)) {
    session.invalidate();
    const second = await attempt();
    if ("failure" in second) {
      return { status: second.failure, ok: false };
    }
    if (isAuthFailure(second.httpStatus)) {
      return { status: "unauthorized", ok: false };
    }
    return finishResolveResult(second.httpStatus);
  }

  return finishResolveResult(first.httpStatus);
}

function finishResolveResult(httpStatus: number): ResolveApprovalResult {
  if (httpStatus >= 200 && httpStatus < 300) {
    return { status: "ok", ok: true };
  }
  return { status: "error", ok: false };
}
