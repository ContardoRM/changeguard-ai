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
 *   CONTROL_ROOM_KIROCREW_HOME    optional. Overrides KIROCREW_HOME ONLY
 *                                 for the server-side `kirocrew token`
 *                                 subprocess this plugin shells out to
 *                                 (see gatewaySession.ts). Required
 *                                 whenever the target Gateway is running
 *                                 with a non-default KIROCREW_HOME (e.g.
 *                                 an isolated/disposable dev instance) --
 *                                 without it, the CLI resolves against
 *                                 THIS process's own default home, which
 *                                 may be the wrong credential store
 *                                 entirely. Read server-side only; never
 *                                 exposed via Vite's `define`/env
 *                                 injection, never serialized into any
 *                                 `/__control-room/*` response. Left
 *                                 unset preserves the CLI's normal
 *                                 default-home resolution.
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

export interface ApprovalRoute {
  approvalId: string;
  action: "approve" | "reject";
}

/**
 * Parses the RELATIVE URL Vite's Connect-based dev-server middleware
 * stack passes to a handler mounted via
 * `server.middlewares.use("/__control-room/approvals/", handler)`.
 *
 * BUG THIS FIXES (confirmed via a live smoke test): Connect strips the
 * matched mount-path prefix from `req.url` before invoking a mounted
 * handler -- standard Connect/Express middleware-mounting semantics, the
 * same behavior `app.use("/api", router)` relies on. So inside this
 * handler, `req.url` for a browser request to
 * `/__control-room/approvals/<id>/<action>` is the REMAINDER after that
 * prefix has already been consumed (e.g. `"<id>/<action>"`, possibly with
 * a leading slash depending on Connect's own normalization) -- never the
 * original full request path. The previous implementation anchored its
 * regex on the FULL mounted path (`^/__control-room/approvals/...`),
 * which can therefore never match the already-stripped `req.url`, so
 * every approve/reject call fell through to this handler's own 404
 * branch, unconditionally.
 *
 * This function accepts ONLY the relative forms -- `"<id>/<action>"` or
 * `"/<id>/<action>"` -- and deliberately does NOT require (or attempt to
 * also support) the full `/__control-room/approvals/...` prefix; the
 * browser-side caller (`src/lib/changeguard/gateway.ts#resolveApproval`)
 * is unchanged and keeps requesting the full path -- Connect's own mount
 * matching is what strips it before this function ever sees it.
 *
 * Fails closed (returns `null`) for anything that is not EXACTLY
 * `<approval-id>/<approve|reject>`:
 *   - an empty/absent URL;
 *   - a missing id or action segment;
 *   - more than two path segments (extra segments after the action, or
 *     the id itself containing an unencoded `/`);
 *   - an action other than exactly `"approve"` or `"reject"`;
 *   - a `%2F`-style encoded slash hidden inside either raw segment,
 *     checked BEFORE percent-decoding -- decoding first would let an
 *     encoded slash resurrect additional path structure and smuggle a
 *     value past the two-segment check (the classic encoded-slash /
 *     path-traversal-ambiguity class of bug);
 *   - a malformed percent-encoding `decodeURIComponent` itself rejects;
 *   - a decoded id containing `/` or `..`.
 *
 * A query string or fragment on `req.url`, if present, is ignored (this
 * handler only ever cares about the path).
 */
export function parseApprovalRoute(url: string | undefined): ApprovalRoute | null {
  if (!url) return null;

  const pathOnly = url.split(/[?#]/, 1)[0] ?? "";
  if (pathOnly.length === 0) return null;

  const withoutLeadingSlash = pathOnly.startsWith("/") ? pathOnly.slice(1) : pathOnly;
  if (withoutLeadingSlash.length === 0) return null;

  const segments = withoutLeadingSlash.split("/");
  if (segments.length !== 2) return null; // exactly <id>/<action>, never more or fewer

  const [rawId, rawAction] = segments;
  if (!rawId || !rawAction) return null;

  // Reject an encoded slash in either raw segment BEFORE decoding -- see
  // this function's own doc comment for why decode-then-check would be
  // unsafe here.
  if (/%2f/i.test(rawId) || /%2f/i.test(rawAction)) return null;

  let approvalId: string;
  let action: string;
  try {
    approvalId = decodeURIComponent(rawId);
    action = decodeURIComponent(rawAction);
  } catch {
    return null; // malformed percent-encoding
  }

  if (!approvalId || approvalId.includes("/") || approvalId.includes("..")) return null;
  if (action !== "approve" && action !== "reject") return null;

  return { approvalId, action };
}

export function createControlRoomProxyPlugin(): Plugin {
  return {
    name: "changeguard-control-room-proxy",
    configureServer(server: ViteDevServer) {
      const artifactsDir = resolveArtifactsDir();
      const gatewayUrl = process.env.CONTROL_ROOM_GATEWAY_URL ?? "";
      // Read server-side only. Never passed to Vite's `define`, never
      // included in any response this plugin sends to the browser -- see
      // gatewaySession.ts's GatewaySessionDeps#kirocrewHome doc comment.
      const kirocrewHome = process.env.CONTROL_ROOM_KIROCREW_HOME || undefined;

      // One GatewaySessionManager per dev-server process, reused across
      // every /__control-room/* request (never recreated per-request) so
      // the mint+exchange sequence only runs when no cached session
      // exists yet or after an explicit invalidate() on auth failure --
      // see gatewaySession.ts's module docstring.
      const session = gatewayUrl
        ? new GatewaySessionManager(gatewayUrl, {
            execFile: defaultExecFile,
            httpRequest: defaultHttpRequest,
            kirocrewHome,
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
        // req.url here is RELATIVE to this mount path (Connect strips the
        // "/__control-room/approvals/" prefix before invoking this
        // handler) -- see parseApprovalRoute()'s own doc comment for the
        // full explanation and the bug this fixes.
        const route = parseApprovalRoute(req.url);
        if (!route || req.method !== "POST") {
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
        const { approvalId, action } = route;
        try {
          const result = await resolveApprovalWithSession(
            gatewayUrl,
            session,
            defaultHttpRequest,
            APPROVALS_HTTP_TIMEOUT_MS,
            approvalId,
            action,
          );
          const httpStatus =
            result.status === "ok"
              ? 200
              : result.status === "unauthorized"
                ? 403
                : result.status === "session_acquisition_failed"
                  ? 500
                  : 502;
          sendJson(res, httpStatus, { ok: result.ok, approvalApiStatus: result.status });
        } catch (error) {
          sendJson(res, 502, { error: error instanceof Error ? error.message : "gateway request failed" });
        }
      });
    },
  };
}
