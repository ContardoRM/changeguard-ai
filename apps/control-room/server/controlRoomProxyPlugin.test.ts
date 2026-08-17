import { describe, expect, it } from "vitest";
import { buildApprovalSnapshotFields, parseApprovalRoute } from "./controlRoomProxyPlugin";
import { GatewaySessionManager } from "./gatewaySession";
import type { ExecFileFn, GatewayHttpResponse, HttpRequestFn } from "./gatewaySession";

describe("parseApprovalRoute", () => {
  it("matches the relative form '<id>/approve' (no leading slash, no mount prefix)", () => {
    // This is the ACTUAL req.url shape Connect passes into a handler
    // mounted at "/__control-room/approvals/" -- the mount prefix is
    // already stripped before this function ever sees the URL.
    expect(parseApprovalRoute("task-gate-1-69dfc516/approve")).toEqual({
      approvalId: "task-gate-1-69dfc516",
      action: "approve",
    });
  });

  it("matches the relative form '<id>/reject'", () => {
    expect(parseApprovalRoute("task-gate-1-69dfc516/reject")).toEqual({
      approvalId: "task-gate-1-69dfc516",
      action: "reject",
    });
  });

  it("also matches the leading-slash relative form '/<id>/approve', in case Connect ever normalizes that way", () => {
    expect(parseApprovalRoute("/task-gate-1-69dfc516/approve")).toEqual({
      approvalId: "task-gate-1-69dfc516",
      action: "approve",
    });
  });

  it("also matches the leading-slash relative form '/<id>/reject'", () => {
    expect(parseApprovalRoute("/task-gate-1-69dfc516/reject")).toEqual({
      approvalId: "task-gate-1-69dfc516",
      action: "reject",
    });
  });

  it("does NOT require the old full browser-facing path inside the mounted handler", () => {
    // The full path (what the BROWSER requests) is never what this
    // function receives once Connect has stripped the mount prefix --
    // proving the fix does not simply re-require the old, always-false
    // full-path shape.
    expect(parseApprovalRoute("/__control-room/approvals/task-gate-1-69dfc516/approve")).toBeNull();
  });

  it("fails closed on an empty path", () => {
    expect(parseApprovalRoute("")).toBeNull();
  });

  it("fails closed on an undefined url", () => {
    expect(parseApprovalRoute(undefined)).toBeNull();
  });

  it("fails closed on a bare slash", () => {
    expect(parseApprovalRoute("/")).toBeNull();
  });

  it("fails closed when the id segment is missing", () => {
    expect(parseApprovalRoute("/approve")).toBeNull();
  });

  it("fails closed when the action segment is missing", () => {
    expect(parseApprovalRoute("task-gate-1")).toBeNull();
  });

  it("fails closed on extra path segments after the action", () => {
    expect(parseApprovalRoute("task-gate-1/approve/extra")).toBeNull();
  });

  it("fails closed on extra path segments before the id (three segments total)", () => {
    expect(parseApprovalRoute("extra/task-gate-1/approve")).toBeNull();
  });

  it("fails closed on an unsupported action", () => {
    expect(parseApprovalRoute("task-gate-1/delete")).toBeNull();
  });

  it("fails closed on an empty action after a trailing slash", () => {
    expect(parseApprovalRoute("task-gate-1/")).toBeNull();
  });

  it("fails closed on an encoded slash hidden in the id segment (path-traversal-ambiguity class)", () => {
    expect(parseApprovalRoute("task%2Fgate-1/approve")).toBeNull();
  });

  it("fails closed on an encoded slash hidden in the action segment", () => {
    expect(parseApprovalRoute("task-gate-1/appr%2Fove")).toBeNull();
  });

  it("fails closed on a decoded id containing a literal slash", () => {
    // %2F is rejected pre-decode (see the dedicated test above); this
    // covers an id that somehow decodes to contain a slash via a
    // different escape, as an extra defense-in-depth check.
    expect(parseApprovalRoute("task..%2fgate-1/approve")).toBeNull();
  });

  it("fails closed on a decoded id of exactly '..' (two segments, no encoded slash, still traversal-like)", () => {
    expect(parseApprovalRoute("../approve")).toBeNull();
  });

  it("fails closed on a three-segment traversal attempt regardless of the '..' check", () => {
    expect(parseApprovalRoute("../etc/approve")).toBeNull();
  });

  it("fails closed on malformed percent-encoding", () => {
    expect(parseApprovalRoute("task-gate-1%/approve")).toBeNull();
  });

  it("ignores a query string when parsing the path", () => {
    expect(parseApprovalRoute("task-gate-1/approve?foo=bar")).toEqual({
      approvalId: "task-gate-1",
      action: "approve",
    });
  });

  it("passes a percent-encoded but otherwise valid id through decoded and unchanged", () => {
    expect(parseApprovalRoute("task%2Dgate%2D1/approve")).toEqual({
      approvalId: "task-gate-1",
      action: "approve",
    });
  });
});

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

  it("never includes a configured KIROCREW_HOME path anywhere in the returned fields", async () => {
    const configuredHome = "/tmp/changeguard-smoke-2gqyRN-kirocrew-home";
    const execFile: ExecFileFn = async (_file, _args, _timeoutMs, env) => {
      // Simulate a successful mint that used the configured home --
      // proves the value flowed through to the subprocess env, while the
      // assertions below prove it never flows back out to the browser.
      expect(env.KIROCREW_HOME).toBe(configuredHome);
      return `http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`;
    };
    const httpRequest: HttpRequestFn = async (url) => {
      if (url.endsWith("/api/approvals")) {
        return approvalsHttpResponse({ status: 200, body: JSON.stringify([{ id: "task-gate-1" }]) });
      }
      return approvalsHttpResponse({ status: 200, setCookieHeaders: ["mc_token_8787=session-abc"] });
    };
    const session = new GatewaySessionManager("http://127.0.0.1:8787", {
      execFile,
      httpRequest,
      kirocrewHome: configuredHome,
    });

    const fields = await buildApprovalSnapshotFields("http://127.0.0.1:8787", session, httpRequest);
    const serialized = JSON.stringify(fields);

    expect(serialized).not.toContain(configuredHome);
    expect(serialized).not.toContain("KIROCREW_HOME");
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
