import { describe, expect, it, vi } from "vitest";
import { fetchPendingApprovalsWithSession, resolveApprovalWithSession } from "./approvalsClient";
import type { GatewaySessionManager } from "./gatewaySession";
import type { GatewayHttpResponse, HttpRequestFn } from "./gatewaySession";

/** Minimal fake standing in for GatewaySessionManager -- exposes only the
 * two methods approvalsClient.ts actually calls, so these tests exercise
 * the client's own retry/status logic without depending on the real
 * mint+exchange sequence (covered separately in gatewaySession.test.ts). */
function fakeSession(cookieHeaderSequence: string[]): {
  session: GatewaySessionManager;
  invalidateCalls: number;
  getCalls: number;
} {
  let getCalls = 0;
  let invalidateCalls = 0;
  const session = {
    getSessionCookieHeader: vi.fn(async () => {
      const header = cookieHeaderSequence[Math.min(getCalls, cookieHeaderSequence.length - 1)];
      getCalls += 1;
      return header;
    }),
    invalidate: vi.fn(() => {
      invalidateCalls += 1;
    }),
  } as unknown as GatewaySessionManager;
  return {
    session,
    get invalidateCalls() {
      return invalidateCalls;
    },
    get getCalls() {
      return getCalls;
    },
  };
}

function httpSequence(responses: GatewayHttpResponse[]): { fn: HttpRequestFn; calls: Array<{ url: string; method: string; headers: Record<string, string> }> } {
  const calls: Array<{ url: string; method: string; headers: Record<string, string> }> = [];
  let index = 0;
  const fn: HttpRequestFn = async (url, method, headers) => {
    calls.push({ url, method, headers });
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return response;
  };
  return { fn, calls };
}

describe("fetchPendingApprovalsWithSession", () => {
  it("sends the session cookie as the Cookie header, never X-Internal-Secret", async () => {
    const { session } = fakeSession(["mc_token_8787=abc"]);
    const { fn, calls } = httpSequence([
      { status: 200, setCookieHeaders: [], body: JSON.stringify([{ id: "task-gate-1" }]) },
    ]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, fn, 5000);

    expect(calls).toHaveLength(1);
    expect(calls[0].headers).toEqual({ Cookie: "mc_token_8787=abc" });
    expect(calls[0].headers).not.toHaveProperty("X-Internal-Secret");
    expect(result.status).toBe("ok");
    expect(result.approvals).toEqual([{ id: "task-gate-1" }]);
  });

  it("hits GET /api/approvals on the configured gateway URL", async () => {
    const { session } = fakeSession(["mc_token_8787=abc"]);
    const { fn, calls } = httpSequence([{ status: 200, setCookieHeaders: [], body: "[]" }]);

    await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, fn, 5000);

    expect(calls[0].url).toBe("http://127.0.0.1:8787/api/approvals");
    expect(calls[0].method).toBe("GET");
  });

  it("on a 403, discards the session and retries exactly once with a fresh cookie", async () => {
    const fake = fakeSession(["mc_token_8787=stale", "mc_token_8787=fresh"]);
    const { fn, calls } = httpSequence([
      { status: 403, setCookieHeaders: [], body: '{"error":"Token required"}' },
      { status: 200, setCookieHeaders: [], body: "[]" },
    ]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", fake.session, fn, 5000);

    expect(calls).toHaveLength(2);
    expect(calls[0].headers.Cookie).toBe("mc_token_8787=stale");
    expect(calls[1].headers.Cookie).toBe("mc_token_8787=fresh");
    expect(fake.invalidateCalls).toBe(1);
    expect(result.status).toBe("ok");
  });

  it("on a 401, discards the session and retries exactly once", async () => {
    const fake = fakeSession(["mc_token_8787=stale", "mc_token_8787=fresh"]);
    const { fn } = httpSequence([
      { status: 401, setCookieHeaders: [], body: "" },
      { status: 200, setCookieHeaders: [], body: "[]" },
    ]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", fake.session, fn, 5000);

    expect(fake.invalidateCalls).toBe(1);
    expect(result.status).toBe("ok");
  });

  it("fails closed as 'unauthorized' when the retry ALSO returns 403 -- no infinite loop", async () => {
    const { session } = fakeSession(["mc_token_8787=a", "mc_token_8787=b"]);
    const { fn, calls } = httpSequence([
      { status: 403, setCookieHeaders: [], body: "" },
      { status: 403, setCookieHeaders: [], body: "" },
    ]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, fn, 5000);

    expect(calls).toHaveLength(2); // exactly one retry, never more
    expect(result.status).toBe("unauthorized");
    expect(result.approvals).toEqual([]);
  });

  it("never fabricates a pending approval when unauthorized", async () => {
    const { session } = fakeSession(["mc_token_8787=a", "mc_token_8787=b"]);
    const { fn } = httpSequence([
      { status: 403, setCookieHeaders: [], body: "" },
      { status: 403, setCookieHeaders: [], body: "" },
    ]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, fn, 5000);

    expect(result.approvals).toHaveLength(0);
  });

  it("reports 'unreachable' when the HTTP call throws (network failure)", async () => {
    const { session } = fakeSession(["mc_token_8787=a"]);
    const throwing: HttpRequestFn = async () => {
      throw new Error("ECONNREFUSED");
    };

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, throwing, 5000);

    expect(result.status).toBe("unreachable");
  });

  it("reports 'error' for a non-2xx, non-401/403 response", async () => {
    const { session } = fakeSession(["mc_token_8787=a"]);
    const { fn } = httpSequence([{ status: 500, setCookieHeaders: [], body: "" }]);

    const result = await fetchPendingApprovalsWithSession("http://127.0.0.1:8787", session, fn, 5000);

    expect(result.status).toBe("error");
  });
});

describe("resolveApprovalWithSession", () => {
  it("sends the session cookie on POST /api/approvals/{id}/approve", async () => {
    const { session } = fakeSession(["mc_token_8787=abc"]);
    const { fn, calls } = httpSequence([{ status: 200, setCookieHeaders: [], body: '{"ok":true}' }]);

    const result = await resolveApprovalWithSession(
      "http://127.0.0.1:8787",
      session,
      fn,
      5000,
      "task-gate-1",
      "approve",
    );

    expect(calls[0].url).toBe("http://127.0.0.1:8787/api/approvals/task-gate-1/approve");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].headers).toEqual({ Cookie: "mc_token_8787=abc" });
    expect(result.ok).toBe(true);
    expect(result.status).toBe("ok");
  });

  it("sends the session cookie on POST /api/approvals/{id}/reject", async () => {
    const { session } = fakeSession(["mc_token_8787=abc"]);
    const { fn, calls } = httpSequence([{ status: 200, setCookieHeaders: [], body: '{"ok":true}' }]);

    await resolveApprovalWithSession("http://127.0.0.1:8787", session, fn, 5000, "task-gate-1", "reject");

    expect(calls[0].url).toBe("http://127.0.0.1:8787/api/approvals/task-gate-1/reject");
    expect(calls[0].method).toBe("POST");
  });

  it("on 403, refreshes the session once and retries the same action exactly once", async () => {
    const fake = fakeSession(["mc_token_8787=stale", "mc_token_8787=fresh"]);
    const { fn, calls } = httpSequence([
      { status: 403, setCookieHeaders: [], body: "" },
      { status: 200, setCookieHeaders: [], body: '{"ok":true}' },
    ]);

    const result = await resolveApprovalWithSession(
      "http://127.0.0.1:8787",
      fake.session,
      fn,
      5000,
      "task-gate-1",
      "approve",
    );

    expect(calls).toHaveLength(2);
    expect(fake.invalidateCalls).toBe(1);
    expect(result.ok).toBe(true);
  });

  it("fails closed as 'unauthorized' with ok=false when the retry also fails -- no infinite loop", async () => {
    const { session } = fakeSession(["mc_token_8787=a", "mc_token_8787=b"]);
    const { fn, calls } = httpSequence([
      { status: 403, setCookieHeaders: [], body: "" },
      { status: 403, setCookieHeaders: [], body: "" },
    ]);

    const result = await resolveApprovalWithSession(
      "http://127.0.0.1:8787",
      session,
      fn,
      5000,
      "task-gate-1",
      "approve",
    );

    expect(calls).toHaveLength(2);
    expect(result.ok).toBe(false);
    expect(result.status).toBe("unauthorized");
  });

  it("reports 'unreachable' with ok=false on a network failure", async () => {
    const { session } = fakeSession(["mc_token_8787=a"]);
    const throwing: HttpRequestFn = async () => {
      throw new Error("ECONNREFUSED");
    };

    const result = await resolveApprovalWithSession(
      "http://127.0.0.1:8787",
      session,
      throwing,
      5000,
      "task-gate-1",
      "reject",
    );

    expect(result.ok).toBe(false);
    expect(result.status).toBe("unreachable");
  });
});
