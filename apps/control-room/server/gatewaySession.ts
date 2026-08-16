/**
 * ChangeGuard Control Room — server-side Kiro Crew Gateway dashboard
 * session acquisition.
 *
 * WHY THIS MODULE EXISTS (read before changing anything below):
 *
 * The Gateway's `/api/approvals` and `/api/approvals/{id}/{action}`
 * endpoints are NOT part of the Gateway's `X-Internal-Secret` machine-to-
 * machine auth surface (confirmed by reading the installed `kirocrew`
 * package's `dashboard/server.py`: neither path appears in
 * `_STRICT_INTERNAL_API_PATHS` nor `_MIXED_INTERNAL_API_PATHS`). They only
 * accept the same browser-facing auth the real dashboard SPA uses:
 *
 *   1. A short-lived, signed link token, normally handed to a human via
 *      the dashboard URL the Gateway prints at startup
 *      (`http://localhost:<port>?token=<link-token>`), or minted on
 *      demand by the installed `kirocrew token --port <port>` CLI
 *      command.
 *   2. The Gateway's own auth middleware exchanges that one-time link
 *      token for a longer-lived SESSION token on first use, and returns
 *      it as an `httpOnly` cookie named `mc_token_<port>` (see
 *      `dashboard/token_auth.py`'s `token_auth_middleware`). Every
 *      subsequent request just needs to present that cookie.
 *
 * This module performs that exact two-step exchange ENTIRELY inside the
 * Vite dev-server's Node process: it shells out to the already-installed
 * `kirocrew token` CLI (fixed executable + argument array, never a shell
 * string) to mint the link token, then makes one server-side HTTP request
 * to the Gateway to trade it for the `mc_token_<port>` session cookie.
 * The resulting cookie VALUE is cached in memory here and is NEVER
 * returned in any response this app sends to the browser -- see
 * `controlRoomProxyPlugin.ts`'s own module docstring for the full
 * browser-isolation invariant this module exists to preserve.
 */

import { execFile as execFileCb } from "node:child_process";
import * as http from "node:http";
import * as https from "node:https";

/** How long a single `kirocrew token` invocation may run before this
 * module gives up. It is a local, near-instant CLI print command (no
 * network calls of its own beyond reaching the already-running gateway
 * process), so a generous-but-bounded ceiling is used rather than an
 * unbounded wait. */
export const MINT_TOKEN_TIMEOUT_MS = 8_000;

/** How long the server-side token->cookie exchange HTTP request may run.
 * Mirrors this project's existing "every probe has a bounded timeout"
 * convention (see scripts/changeguard_launch.py's own --timeout). */
export const EXCHANGE_TIMEOUT_MS = 5_000;

/** The exact CLI invocation this module uses to mint a fresh dashboard
 * link token. Fixed executable name, fixed flag names, only the port
 * value (derived from CONTROL_ROOM_GATEWAY_URL, never raw user/browser
 * input) is variable -- passed as a separate argv element, never
 * interpolated into a shell string. */
export function buildMintTokenArgs(port: string): string[] {
  return ["token", "--port", port];
}

/**
 * Parses the `?token=<link-token>` value out of `kirocrew token`'s
 * printed dashboard URL, e.g.
 * `http://localhost:8787?token=eyJhbGciOi...`.
 *
 * Parses ONLY that one output shape -- the CLI's own stdout format
 * confirmed by a live invocation against this project's smoke-test
 * gateway. Throws (fail closed) if the expected `?token=` marker is not
 * present anywhere in stdout, rather than guessing or falling back to
 * treating arbitrary stdout as a token.
 */
export function parseLinkTokenFromCliOutput(stdout: string): string {
  const match = /\?token=([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)/.exec(stdout);
  if (!match) {
    throw new Error(
      "kirocrew token: no '?token=<link-token>' URL found in CLI output; " +
        "refusing to guess a token from unexpected output.",
    );
  }
  return match[1];
}

/**
 * Extracts the numeric port string from a Gateway base URL
 * (`http://127.0.0.1:8787` -> `"8787"`), matching the `mc_token_<port>`
 * cookie-naming scheme the installed Gateway uses
 * (`dashboard/token_auth.py`'s `_cookie_port_from_host`, keyed by the
 * SERVER's own listening port, not any Host header the client sent).
 * Falls back to the URL's implicit default port (80/443) only if none is
 * given explicitly, matching the Gateway's own port-resolution default.
 */
export function extractPortFromGatewayUrl(gatewayUrl: string): string {
  const url = new URL(gatewayUrl);
  if (url.port) return url.port;
  return url.protocol === "https:" ? "443" : "80";
}

/**
 * Finds the `mc_token_<port>` cookie's value among a response's
 * `Set-Cookie` header(s). Returns `null` (never throws) if the exchange
 * response did not include that cookie -- callers treat that as an
 * exchange failure and fail closed, never fabricate a session.
 */
export function parseSessionCookieFromSetCookie(
  setCookieHeaders: readonly string[] | undefined,
  port: string,
): string | null {
  if (!setCookieHeaders || setCookieHeaders.length === 0) return null;
  const cookieName = `mc_token_${port}`;
  for (const header of setCookieHeaders) {
    const firstPair = header.split(";")[0] ?? "";
    const separatorIndex = firstPair.indexOf("=");
    if (separatorIndex === -1) continue;
    const name = firstPair.slice(0, separatorIndex).trim();
    const value = firstPair.slice(separatorIndex + 1).trim();
    if (name === cookieName && value) return value;
  }
  return null;
}

/** One server-side HTTP round trip, abstracted so tests can inject a fake
 * implementation instead of making a real network call. Mirrors the
 * shape `controlRoomProxyPlugin.ts` already needs for the approvals
 * calls themselves. */
export interface GatewayHttpResponse {
  status: number;
  setCookieHeaders: string[];
  body: string;
}

export type HttpRequestFn = (
  url: string,
  method: string,
  headers: Record<string, string>,
  timeoutMs: number,
) => Promise<GatewayHttpResponse>;

/** Real implementation: a plain Node `http`/`https` request. No cookie
 * jar, no redirects followed, no shell involved -- a single request with
 * an explicit method/headers/timeout. */
export const defaultHttpRequest: HttpRequestFn = (url, method, headers, timeoutMs) =>
  new Promise((resolve, reject) => {
    const target = new URL(url);
    const client = target.protocol === "https:" ? https : http;
    const req = client.request(target, { method, headers }, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        const rawSetCookie = res.headers["set-cookie"];
        resolve({
          status: res.statusCode ?? 0,
          setCookieHeaders: rawSetCookie ?? [],
          body: data,
        });
      });
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request to ${url} timed out after ${timeoutMs}ms`));
    });
    req.on("error", reject);
    req.end();
  });

/**
 * Builds the environment object passed to the `kirocrew token` subprocess.
 * Starts from the CURRENT process's own environment (so PATH, HOME, etc.
 * all resolve normally) and overlays `KIROCREW_HOME` ONLY when an explicit
 * override is provided -- never inferred from the gateway URL, the
 * filesystem, or any running-process inspection (explicit configuration
 * only, per this module's own design constraint). When no override is
 * given, `kirocrew token` keeps using its own normal default home
 * resolution, identical to this module's pre-existing behavior.
 */
export function buildExecFileEnv(
  kirocrewHome: string | undefined,
  baseEnv: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  return {
    ...baseEnv,
    ...(kirocrewHome ? { KIROCREW_HOME: kirocrewHome } : {}),
  };
}

/** Runs `kirocrew token --port <port>` and resolves with its stdout.
 * Fixed executable name and a fixed, array-form argument list -- NEVER a
 * shell string -- so no value derived from configuration or a request can
 * be interpreted as additional shell syntax. Rejects (fail closed) on any
 * non-zero exit, spawn error, or timeout.
 *
 * `env` is passed straight through to `child_process.execFile`'s own
 * `env` option -- this is the ONLY mechanism used to target a
 * non-default `KIROCREW_HOME`; the executable name and argv are never
 * altered based on it. */
export type ExecFileFn = (
  file: string,
  args: string[],
  timeoutMs: number,
  env: NodeJS.ProcessEnv,
) => Promise<string>;

export const defaultExecFile: ExecFileFn = (file, args, timeoutMs, env) =>
  new Promise((resolve, reject) => {
    execFileCb(
      file,
      args,
      { timeout: timeoutMs, shell: false, env },
      (error, stdout, _stderr) => {
        if (error) {
          // Deliberately do NOT include the subprocess's raw stderr in
          // this error's message: `kirocrew token`'s own stderr can
          // reference the resolved KIROCREW_HOME path or other
          // credential-store details, and this error's message may
          // eventually be logged. `error.message` (the exec failure
          // reason/exit code) is diagnostic enough without echoing
          // subprocess output verbatim.
          reject(new Error(`${file} ${args.join(" ")} failed: ${error.message}`));
          return;
        }
        resolve(stdout);
      },
    );
  });

/**
 * Raised when the server-side mint (`kirocrew token`) or the
 * token->cookie exchange fails for a reason OTHER than the Gateway being
 * genuinely unreachable over the network (e.g. a `KIROCREW_HOME`/auth-
 * context mismatch, a malformed CLI response, or a missing session
 * cookie in the exchange response). Callers (see `approvalsClient.ts`)
 * use this distinct error type to report a dedicated
 * `"session_acquisition_failed"` status instead of folding it into the
 * generic `"unreachable"` classification used for actual network/
 * connection failures -- the Gateway may be perfectly reachable while
 * this module simply cannot obtain a valid dashboard session for it.
 */
export class SessionAcquisitionError extends Error {}

export interface GatewaySessionDeps {
  execFile: ExecFileFn;
  httpRequest: HttpRequestFn;
  mintTimeoutMs?: number;
  exchangeTimeoutMs?: number;
  /** Optional `KIROCREW_HOME` override for the `kirocrew token`
   * subprocess ONLY -- read server-side from `CONTROL_ROOM_KIROCREW_HOME`
   * by `controlRoomProxyPlugin.ts`, never exposed to browser code, never
   * serialized into any `/__control-room/*` response. Required whenever
   * the target Gateway is running with a non-default `KIROCREW_HOME`
   * (e.g. an isolated/disposable dev instance) -- without it, `kirocrew
   * token` resolves against the CALLING process's own default home,
   * which may be a completely different (or nonexistent, for that
   * gateway) credential store. Left undefined preserves this module's
   * original default behavior (no override at all). */
  kirocrewHome?: string;
}

/**
 * Caches exactly one Gateway dashboard session cookie value in server
 * memory for the lifetime of the Vite dev-server process (or until
 * `invalidate()` is called after an auth failure). Never persists the
 * cookie to disk, never logs it, and never exposes it through any method
 * other than `getSessionCookieHeader()`'s return value -- which callers
 * must only ever attach as an outbound `Cookie` header on a Gateway
 * request, never echo back to the browser.
 */
export class GatewaySessionManager {
  private cachedCookieValue: string | null = null;
  private readonly port: string;

  constructor(
    private readonly gatewayUrl: string,
    private readonly deps: GatewaySessionDeps,
  ) {
    this.port = extractPortFromGatewayUrl(gatewayUrl);
  }

  /** Performs the full mint -> exchange sequence and caches the result.
   * Never called directly by external callers -- routed through
   * `getSessionCookieHeader()` so caching stays centralized. */
  private async mintAndExchange(): Promise<string> {
    const env = buildExecFileEnv(this.deps.kirocrewHome);
    let stdout: string;
    try {
      stdout = await this.deps.execFile(
        "kirocrew",
        buildMintTokenArgs(this.port),
        this.deps.mintTimeoutMs ?? MINT_TOKEN_TIMEOUT_MS,
        env,
      );
    } catch (error) {
      // Distinguish "we could not mint/exchange a dashboard session at
      // all" (e.g. a KIROCREW_HOME/auth-context mismatch, or the CLI
      // itself failing) from a plain Gateway network-reachability
      // failure -- see SessionAcquisitionError's own doc comment and
      // approvalsClient.ts's status classification.
      throw new SessionAcquisitionError(
        error instanceof Error ? error.message : "kirocrew token invocation failed",
      );
    }

    let linkToken: string;
    try {
      linkToken = parseLinkTokenFromCliOutput(stdout);
    } catch (error) {
      // A malformed/unexpected CLI output shape is also a session-
      // acquisition problem, not a network-reachability one.
      throw new SessionAcquisitionError(
        error instanceof Error ? error.message : "could not parse kirocrew token output",
      );
    }

    const exchangeUrl = `${this.gatewayUrl.replace(/\/+$/, "")}/?token=${encodeURIComponent(linkToken)}`;
    // A failure HERE (connection refused, DNS failure, timeout) genuinely
    // means the Gateway itself is unreachable -- let it propagate as a
    // plain Error so callers classify it as "unreachable", not
    // "session_acquisition_failed".
    const response = await this.deps.httpRequest(
      exchangeUrl,
      "GET",
      {},
      this.deps.exchangeTimeoutMs ?? EXCHANGE_TIMEOUT_MS,
    );

    const cookieValue = parseSessionCookieFromSetCookie(response.setCookieHeaders, this.port);
    if (!cookieValue) {
      // The Gateway responded (it IS reachable) but did not hand back a
      // usable session cookie -- a session-acquisition problem, not a
      // reachability one.
      throw new SessionAcquisitionError(
        "gateway token->cookie exchange did not return an mc_token_" +
          this.port +
          " session cookie; refusing to proceed without a genuine session.",
      );
    }
    return cookieValue;
  }

  /** Returns a ready-to-send `Cookie` header value
   * (`mc_token_<port>=<value>`), minting + exchanging a fresh session
   * first if none is cached yet. */
  async getSessionCookieHeader(): Promise<string> {
    if (this.cachedCookieValue === null) {
      this.cachedCookieValue = await this.mintAndExchange();
    }
    return `mc_token_${this.port}=${this.cachedCookieValue}`;
  }

  /** Discards the cached session cookie. Callers invoke this after an
   * approvals call comes back 401/403 (the cookie expired or was
   * revoked), then call `getSessionCookieHeader()` again exactly once to
   * mint a fresh session -- see `approvalsClient.ts`'s retry logic. */
  invalidate(): void {
    this.cachedCookieValue = null;
  }
}
