/**
 * ChangeGuard Control Room — local dev-server proxy plugin.
 *
 * SECURITY BOUNDARY (read this before changing anything below):
 *
 * This module runs ONLY inside Vite's Node.js dev-server process. It is
 * never bundled into the browser JavaScript the Control Room ships. Its
 * entire purpose is to let the browser UI observe ChangeGuard's real
 * artifact files and relay a human's genuine approve/reject decision to
 * the real Kiro Crew Gateway, WITHOUT the browser ever holding:
 *
 *   - the Gateway's `X-Internal-Secret` (~/.kiro/crew/.local_secret);
 *   - the Gateway dashboard link token, session token, or the
 *     `mc_token_<port>` session cookie value (see `gatewaySession.ts`);
 *   - a raw filesystem path outside this repository's `artifacts/` dir;
 *   - the ability to invoke `terraform`, the Remediator, or any script
 *     directly.
 *
 * This mirrors exactly the pattern `scripts/changeguard_launch.py`
 * already uses and this project's own live tests already proved safe:
 * a narrow, server-side script holds the credential and calls a small,
 * fixed set of Gateway REST endpoints on the browser's behalf. This
 * plugin adds NO new Gateway capability -- every endpoint it calls
 * (`GET /api/approvals`, `POST /api/approvals/{id}/{action}`) is one
 * `changeguard_launch.py`/design.md already documents as the sole
 * approval-resolution mechanism.
 *
 * AUTH NOTE (live-smoke-tested finding): the installed Gateway does NOT
 * accept `X-Internal-Secret` on `/api/approvals*` -- only the same
 * cookie-based dashboard session a browser would use. `gatewaySession.ts`
 * and `approvalsClient.ts` perform that dashboard session acquisition
 * (mint a link token via the installed `kirocrew token` CLI, exchange it
 * for the `mc_token_<port>` session cookie) ENTIRELY server-side, and
 * this plugin only ever attaches the resulting `Cookie` header on
 * outbound Gateway requests -- never on any response sent to the
 * browser. `X-Internal-Secret` is unaffected and remains this plugin's
 * only mechanism for any other Gateway-facing call it might add.
 *
 * This plugin NEVER:
 *   - calls `POST /api/taskrunner/plan` or `.../execute` (planning and
 *     executing Stage A/B remains the CLI's job via
 *     `scripts/changeguard_launch.py`; the Control Room only OBSERVES
 *     artifacts and resolves an already-pending approval);
 *   - writes to `terraform/main.tf` or any `artifacts/*.json` file;
 *   - runs `terraform`, `kiro-cli`, or any shell command;
 *   - exposes the internal secret, any token, any session cookie value,
 *     or any credential to a response body sent to the browser.
 *
 * Configuration is read from environment variables ONLY (never from a
 * browser request), matching `scripts/changeguard_launch.py`'s own
 * `--internal-secret`/`--gateway-url` flag conventions:
 *
 *   CONTROL_ROOM_GATEWAY_URL      e.g. http://127.0.0.1:8787 (required for
 *                                 the approval endpoints to function; if
 *                                 unset, approval calls fail closed with a
 *                                 clear error, never silently no-op)
 *   CONTROL_ROOM_INTERNAL_SECRET  contents of ~/.kiro/crew/.local_secret
 *                                 (optional; not used for /api/approvals*,
 *                                 kept for any future internal-secret-
 *                                 eligible Gateway call this plugin adds)
 *   CONTROL_ROOM_ARTIFACTS_DIR    defaults to "../../artifacts" (this
 *                                 repository's real artifacts/ directory,
 *                                 resolved relative to this app's own
 *                                 directory)
 */

import type { Plugin, ViteDevServer } from "vite";
import { promises as fs } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { GatewaySessionManager, defaultExecFile, defaultHttpRequest } from "./gatewaySession";
import {
  fetchPendingApprovalsWithSession,
  resolveApprovalWithSession,
  type ApprovalApiStatus,
} from "./approvalsClient";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const KNOWN_ARTIFACT_NAMES = [
  "baseline-plan.json",
  "candidate-plan.json",
  "change-blocked-result.json",
  "remediation-result.json",
  "remediated-plan.json",
  "security-remediated-review-result.json",
  "reliability-remediated-review-result.json",
  "final-verdict.json",
  "security-review-result.json",
  "reliability-review-result.json",
] as const;

function resolveArtifactsDir(): string {
  const configured = process.env.CONTROL_ROOM_ARTIFACTS_DIR;
  if (configured) return path.resolve(configured);
  // Default: this app lives at <repo>/apps/control-room, so the real
  // artifacts/ directory is two levels up.
  return path.resolve(__dirname, "..", "..", "..", "artifacts");
}

async function readArtifact(artifactsDir: string, name: string) {
  const fullPath = path.join(artifactsDir, name);
  try {
    const raw = await fs.readFile(fullPath, "utf-8");
    return { name, exists: true, json: JSON.parse(raw) as unknown };
  } catch {
    return { name, exists: false, json: null };
  }
}

const APPROVALS_HTTP_TIMEOUT_MS = 5_000;

function sendJson(res: import("node:http").ServerResponse, statusCode: number, payload: unknown) {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(payload));
}

/**
 * Browser-safe snapshot shape for the approvals side of the response.
 * `approvalApiStatus` lets the frontend distinguish "no approval is
 * pending" from "the approvals API itself is unreachable/unauthorized" --
 * two states the previous implementation silently collapsed into an
 * identical `{ crewReachable: true, pendingApprovalId: undefined }`
 * response. `pendingApprovalId` is deliberately left `undefined` (never
 * fabricated) for every status other than `"ok"`.
 */
interface ApprovalSnapshotFields {
  crewReachable: boolean;
  approvalApiStatus: ApprovalApiStatus | "not_configured";
  pendingApprovalId?: string;
}

export async function buildApprovalSnapshotFields(
  gatewayUrl: string,
  session: GatewaySessionManager | null,
  httpRequest: Parameters<typeof fetchPendingApprovalsWithSession>[2] = defaultHttpRequest,
): Promise<ApprovalSnapshotFields> {
  if (!gatewayUrl || !session) {
    return { crewReachable: false, approvalApiStatus: "not_configured" };
  }
  const { status, approvals } = await fetchPendingApprovalsWithSession(
    gatewayUrl,
    session,
    httpRequest,
    APPROVALS_HTTP_TIMEOUT_MS,
  );
  const crewReachable = status !== "unreachable";
  if (status !== "ok") {
    return { crewReachable, approvalApiStatus: status };
  }
  return { crewReachable, approvalApiStatus: status, pendingApprovalId: approvals[0]?.id };
}

export function createControlRoomProxyPlugin(): Plugin {
  return {
    name: "changeguard-control-room-proxy",
    configureServer(server: ViteDevServer) {
      const artifactsDir = resolveArtifactsDir();
      const gatewayUrl = process.env.CONTROL_ROOM_GATEWAY_URL ?? "";

      // One GatewaySessionManager per dev-server process, reused across
      // every /__control-room/* request (never recreated per-request) so
      // the mint+exchange sequence only runs when no cached session
      // exists yet or after an explicit invalidate() on auth failure --
      // see gatewaySession.ts's module docstring.
      const session = gatewayUrl
        ? new GatewaySessionManager(gatewayUrl, {
            execFile: defaultExecFile,
            httpRequest: defaultHttpRequest,
          })
        : null;

      server.middlewares.use("/__control-room/snapshot", async (_req, res) => {
        try {
          const entries = await Promise.all(
            KNOWN_ARTIFACT_NAMES.map((name) => readArtifact(artifactsDir, name)),
          );
          const artifacts: Record<string, unknown> = {};
          for (const entry of entries) artifacts[entry.name] = entry;

          const approvalFields = await buildApprovalSnapshotFields(gatewayUrl, session);

          sendJson(res, 200, { artifacts, ...approvalFields });
        } catch (error) {
          sendJson(res, 500, { error: error instanceof Error ? error.message : "unknown error" });
        }
      });

      server.middlewares.use("/__control-room/approvals/", async (req, res) => {
        // Matches /__control-room/approvals/{id}/{approve|reject}
        const match = /^\/__control-room\/approvals\/([^/]+)\/(approve|reject)$/.exec(req.url ?? "");
        if (!match || req.method !== "POST") {
          sendJson(res, 404, { error: "not found" });
          return;
        }
        if (!gatewayUrl || !session) {
          sendJson(res, 503, {
            error:
              "CONTROL_ROOM_GATEWAY_URL is not configured; refusing to resolve approval (fail closed).",
          });
          return;
        }
        const [, approvalId, action] = match;
        try {
          const result = await resolveApprovalWithSession(
            gatewayUrl,
            session,
            defaultHttpRequest,
            APPROVALS_HTTP_TIMEOUT_MS,
            approvalId,
            action as "approve" | "reject",
          );
          const httpStatus = result.status === "ok" ? 200 : result.status === "unauthorized" ? 403 : 502;
          sendJson(res, httpStatus, { ok: result.ok, approvalApiStatus: result.status });
        } catch (error) {
          sendJson(res, 502, { error: error instanceof Error ? error.message : "gateway request failed" });
        }
      });
    },
  };
}
