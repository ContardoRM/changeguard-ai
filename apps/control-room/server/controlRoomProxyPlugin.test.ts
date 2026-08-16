import { describe, expect, it } from "vitest";
import { buildApprovalSnapshotFields } from "./controlRoomProxyPlugin";
import { GatewaySessionManager } from "./gatewaySession";
import type { ExecFileFn, GatewayHttpResponse, HttpRequestFn } from "./gatewaySession";

const SAMPLE_LINK_TOKEN = "eyJzdWIiOiJsb2NhbC1hcHAifQ.sig";

function makeSession(overrides?: { execFile?: ExecFileFn; httpRequest?: HttpRequestFn }): GatewaySessionManager {
  const execFile: ExecFileFn =
    overrides?.execFile ?? (async () => `http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`);
  const httpRequest: HttpRequestFn =
    overrides?.httpRequest ??
    (async () => ({ status: 200, setCookieHeaders: ["mc_token_8787=session-abc"], body: "" }));
  return new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });
}

function approvalsHttpResponse(overrides: Partial<GatewayHttpResponse>): GatewayHttpResponse {
  return { status: 200, setCookieHeaders: [], body: "[]", ...overrides };
}

describe("buildApprovalSnapshotFields", () => {
  it("reports not_configured (and crewReachable=false) when no gateway URL/session exists", async () => {
    const fields = await buildApprovalSnapshotFields("", null);
    expect(fields).toEqual({ crewReachable: false, approvalApiStatus: "not_configured" });
  });

  it("reports ok + the genuine pending approval id when the approvals call succeeds", async () => {
    let callCount = 0;
    const httpRequest: HttpRequestFn = async (url) => {
      callCount += 1;
      if (url.endsWith("/api/approvals")) {
        return approvalsHttpResponse({ status: 200, body: JSON.stringify([{ id: "task-gate-1" }]) });
      }
      return approvalsHttpResponse({ status: 200, setCookieHeaders: ["mc_token_8787=session-abc"] });
    };
    const session = makeSession({ httpRequest });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);

    expect(fields.approvalApiStatus).toBe("ok");
    expect(fields.pendingApprovalId).toBe("task-gate-1");
    expect(fields.crewReachable).toBe(true);
    expect(callCount).toBeGreaterThan(0);
  });

  it("reports ok with pendingApprovalId undefined when nothing is pending -- never fabricated", async () => {
    const httpRequest: HttpRequestFn = async (url) => {
      if (url.endsWith("/api/approvals")) {
        return approvalsHttpResponse({ status: 200, body: "[]" });
      }
      return approvalsHttpResponse({ status: 200, setCookieHeaders: ["mc_token_8787=session-abc"] });
    };
    const session = makeSession({ httpRequest });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);

    expect(fields.approvalApiStatus).toBe("ok");
    expect(fields.pendingApprovalId).toBeUndefined();
  });

  it("distinguishes 'unauthorized' from 'ok with nothing pending' -- never fabricates pendingApprovalId here either", async () => {
    const httpRequest: HttpRequestFn = async (url) => {
      if (url.endsWith("/api/approvals")) {
        return approvalsHttpResponse({ status: 403, body: '{"error":"Token required"}' });
      }
      // Token exchange itself succeeds; only /api/approvals rejects.
      return approvalsHttpResponse({ status: 200, setCookieHeaders: ["mc_token_8787=session-abc"] });
    };
    const session = makeSession({ httpRequest });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);

    expect(fields.approvalApiStatus).toBe("unauthorized");
    expect(fields.pendingApprovalId).toBeUndefined();
    // Gateway itself is reachable even though the approvals call is unauthorized.
    expect(fields.crewReachable).toBe(true);
  });

  it("reports crewReachable=false when the approvals call cannot reach the gateway at all", async () => {
    const httpRequest: HttpRequestFn = async () => {
      throw new Error("ECONNREFUSED");
    };
    const session = makeSession({ httpRequest });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);

    expect(fields.approvalApiStatus).toBe("unreachable");
    expect(fields.crewReachable).toBe(false);
    expect(fields.pendingApprovalId).toBeUndefined();
  });

  it("never includes a token, cookie, or secret value anywhere in the returned fields", async () => {
    const httpRequest: HttpRequestFn = async (url) => {
      if (url.endsWith("/api/approvals")) {
        return approvalsHttpResponse({ status: 200, body: JSON.stringify([{ id: "task-gate-1" }]) });
      }
      return approvalsHttpResponse({ status: 200, setCookieHeaders: ["mc_token_8787=super-secret-session"] });
    };
    const session = makeSession({ httpRequest });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);
    const serialized = JSON.stringify(fields);

    expect(serialized).not.toContain("super-secret-session");
    expect(serialized).not.toContain("mc_token_");
    expect(serialized).not.toContain(SAMPLE_LINK_TOKEN);
    // Only the sanitized fields are present.
    expect(Object.keys(fields).sort()).toEqual(["approvalApiStatus", "crewReachable", "pendingApprovalId"].sort());
  });
});
