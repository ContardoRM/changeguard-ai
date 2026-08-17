// @vitest-environment node
//
// This file spins up a REAL vite.createServer() instance, which relies on
// esbuild internals that are incompatible with the project's default
// jsdom test environment (jsdom's TextEncoder polyfill breaks an esbuild
// invariant check). Only this integration test file needs Node's own
// globals -- every other test file keeps the project-wide jsdom default.
/**
 * Integration test for controlRoomProxyPlugin.ts's approve/reject route.
 *
 * WHY THIS FILE EXISTS: the bug this fixes (`req.url` inside a mounted
 * Connect/Vite middleware handler is RELATIVE to the mount path, not the
 * full request path) can only be proven wrong or right by exercising the
 * REAL Vite dev-server middleware stack -- a unit test that calls
 * `parseApprovalRoute()` directly with a hand-constructed string cannot,
 * by itself, prove that Vite/Connect actually strips the mount prefix
 * the way this fix assumes. This test starts a real `vite.createServer()`
 * instance with `createControlRoomProxyPlugin()` mounted, sends a genuine
 * HTTP `POST /__control-room/approvals/<id>/<action>` request over a real
 * socket, and asserts the handler reaches the (mocked) Gateway resolver
 * instead of returning its own local 404 -- the exact failure mode
 * confirmed in the live smoke test this fix responds to.
 *
 * The Gateway itself is NOT contacted: `resolveApprovalWithSession` is
 * mocked at the module boundary so this test never makes a real network
 * call and never depends on any running `kirocrew` gateway.
 */

import http from "node:http";
import { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createServer as createViteServer, type ViteDevServer } from "vite";

const resolveApprovalWithSessionMock = vi.fn();

vi.mock("./approvalsClient", async () => {
  const actual = await vi.importActual<typeof import("./approvalsClient")>("./approvalsClient");
  return {
    ...actual,
    resolveApprovalWithSession: (...args: unknown[]) => resolveApprovalWithSessionMock(...args),
    fetchPendingApprovalsWithSession: vi.fn(async () => ({ status: "ok" as const, approvals: [] })),
  };
});

// Import AFTER the mock is registered so the plugin module picks up the
// mocked approvalsClient functions.
const { createControlRoomProxyPlugin } = await import("./controlRoomProxyPlugin");

function httpRequest(
  port: number,
  method: string,
  path: string,
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port, method, path },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => resolve({ status: res.statusCode ?? 0, body: data }));
      },
    );
    req.on("error", reject);
    req.end();
  });
}

describe("controlRoomProxyPlugin approvals route -- real Vite middleware integration", () => {
  let server: ViteDevServer;
  let port: number;
  const previousEnv = { ...process.env };

  beforeEach(async () => {
    resolveApprovalWithSessionMock.mockReset();
    resolveApprovalWithSessionMock.mockResolvedValue({ status: "ok", ok: true });

    process.env.CONTROL_ROOM_GATEWAY_URL = "http://127.0.0.1:8787";
    process.env.CONTROL_ROOM_ARTIFACTS_DIR = "/tmp/changeguard-control-room-proxy-integration-test";

    server = await createViteServer({
      configFile: false,
      root: import.meta.dirname,
      logLevel: "silent",
      server: { port: 0, strictPort: false },
      plugins: [createControlRoomProxyPlugin()],
    });
    await server.listen();
    const address = server.httpServer?.address() as AddressInfo;
    port = address.port;
  });

  afterEach(async () => {
    await server.close();
    process.env = { ...previousEnv };
  });

  it("reaches resolveApprovalWithSession with the parsed id/action for a genuine mounted POST (proves the 404 bug is fixed)", async () => {
    const response = await httpRequest(port, "POST", "/__control-room/approvals/task-gate-1-69dfc516/approve");

    // Before the fix, this was ALWAYS 404 {"error":"not found"} because
    // req.url inside the mounted handler never matched the full-path
    // regex. Reaching the resolver (and getting its mocked "ok" result)
    // proves the real Connect/Vite mount-stripping behavior is now
    // handled correctly.
    expect(response.status).toBe(200);
    const body = JSON.parse(response.body);
    expect(body.ok).toBe(true);
    expect(resolveApprovalWithSessionMock).toHaveBeenCalledTimes(1);
    const call = resolveApprovalWithSessionMock.mock.calls[0];
    // (gatewayUrl, session, httpRequest, timeoutMs, approvalId, action)
    expect(call[4]).toBe("task-gate-1-69dfc516");
    expect(call[5]).toBe("approve");
  });

  it("reaches resolveApprovalWithSession with action='reject' for a genuine mounted POST", async () => {
    const response = await httpRequest(port, "POST", "/__control-room/approvals/task-gate-1-69dfc516/reject");

    expect(response.status).toBe(200);
    expect(resolveApprovalWithSessionMock).toHaveBeenCalledTimes(1);
    const call = resolveApprovalWithSessionMock.mock.calls[0];
    expect(call[4]).toBe("task-gate-1-69dfc516");
    expect(call[5]).toBe("reject");
  });

  it("still returns 404 (fails closed) for a malformed mounted path, via the real middleware stack", async () => {
    const response = await httpRequest(port, "POST", "/__control-room/approvals/task-gate-1/approve/extra");

    expect(response.status).toBe(404);
    expect(resolveApprovalWithSessionMock).not.toHaveBeenCalled();
  });

  it("still returns 404 for an unsupported action, via the real middleware stack", async () => {
    const response = await httpRequest(port, "POST", "/__control-room/approvals/task-gate-1/delete");

    expect(response.status).toBe(404);
    expect(resolveApprovalWithSessionMock).not.toHaveBeenCalled();
  });

  it("never exposes CONTROL_ROOM_GATEWAY_URL, a token, or a cookie value in the real HTTP response body", async () => {
    const response = await httpRequest(port, "POST", "/__control-room/approvals/task-gate-1-69dfc516/approve");

    expect(response.body).not.toContain("8787");
    expect(response.body).not.toContain("mc_token_");
    expect(response.body).not.toContain("CONTROL_ROOM_INTERNAL_SECRET");
    expect(response.body).not.toContain("KIROCREW_HOME");
  });
});
