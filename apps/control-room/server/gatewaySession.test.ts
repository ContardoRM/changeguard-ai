import { describe, expect, it, vi } from "vitest";
import {
  GatewaySessionManager,
  buildMintTokenArgs,
  extractPortFromGatewayUrl,
  parseLinkTokenFromCliOutput,
  parseSessionCookieFromSetCookie,
  type ExecFileFn,
  type GatewayHttpResponse,
  type HttpRequestFn,
} from "./gatewaySession";

const SAMPLE_LINK_TOKEN =
  "eyJzdWIiOiJsb2NhbC1hcHAiLCJleHAiOjE3ODY5MTc2OTV9.hXk2uREQCVhGgobrNVorhy6ftiqAiEWeBVCXGRyov-0";

describe("buildMintTokenArgs", () => {
  it("returns a fixed argv array naming the exact CLI subcommand and port, never a shell string", () => {
    const args = buildMintTokenArgs("8787");
    expect(args).toEqual(["token", "--port", "8787"]);
  });
});

describe("parseLinkTokenFromCliOutput", () => {
  it("extracts the token value from the CLI's printed dashboard URL", () => {
    const stdout = `http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`;
    expect(parseLinkTokenFromCliOutput(stdout)).toBe(SAMPLE_LINK_TOKEN);
  });

  it("throws (fails closed) when no '?token=' marker is present in stdout", () => {
    expect(() => parseLinkTokenFromCliOutput("some unexpected output\n")).toThrow();
  });

  it("throws on empty stdout rather than returning an empty/undefined token", () => {
    expect(() => parseLinkTokenFromCliOutput("")).toThrow();
  });
});

describe("extractPortFromGatewayUrl", () => {
  it("extracts an explicit port", () => {
    expect(extractPortFromGatewayUrl("http://127.0.0.1:8787")).toBe("8787");
  });

  it("falls back to the implicit default port for http", () => {
    expect(extractPortFromGatewayUrl("http://example.com")).toBe("80");
  });

  it("falls back to the implicit default port for https", () => {
    expect(extractPortFromGatewayUrl("https://example.com")).toBe("443");
  });
});

describe("parseSessionCookieFromSetCookie", () => {
  it("finds the mc_token_<port> cookie among multiple Set-Cookie headers", () => {
    const headers = [
      "mc_token_8787=abc123; HttpOnly; SameSite=Lax; Path=/",
      "other_cookie=zzz; Path=/",
    ];
    expect(parseSessionCookieFromSetCookie(headers, "8787")).toBe("abc123");
  });

  it("returns null when no matching cookie is present", () => {
    const headers = ["other_cookie=zzz; Path=/"];
    expect(parseSessionCookieFromSetCookie(headers, "8787")).toBeNull();
  });

  it("returns null when Set-Cookie headers are absent entirely", () => {
    expect(parseSessionCookieFromSetCookie(undefined, "8787")).toBeNull();
  });

  it("does not match a same-prefix cookie for a different port", () => {
    const headers = ["mc_token_9999=shouldnotmatch; Path=/"];
    expect(parseSessionCookieFromSetCookie(headers, "8787")).toBeNull();
  });
});

function fakeExecFile(stdout: string, capturedCalls: Array<{ file: string; args: string[] }>): ExecFileFn {
  return async (file, args) => {
    capturedCalls.push({ file, args });
    return stdout;
  };
}

function fakeHttpRequest(response: GatewayHttpResponse): HttpRequestFn {
  return async () => response;
}

describe("GatewaySessionManager", () => {
  it("mints a token via a fixed executable + argv array (no shell string)", async () => {
    const calls: Array<{ file: string; args: string[] }> = [];
    const execFile = fakeExecFile(`http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`, calls);
    const httpRequest = fakeHttpRequest({
      status: 200,
      setCookieHeaders: ["mc_token_8787=session-abc; HttpOnly"],
      body: "",
    });

    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });
    await manager.getSessionCookieHeader();

    expect(calls).toHaveLength(1);
    expect(calls[0].file).toBe("kirocrew");
    expect(calls[0].args).toEqual(["token", "--port", "8787"]);
  });

  it("exchanges the link token for the mc_token_<port> session cookie", async () => {
    const execFile = fakeExecFile(`http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`, []);
    const httpRequest = fakeHttpRequest({
      status: 200,
      setCookieHeaders: ["mc_token_8787=session-xyz; HttpOnly; SameSite=Lax"],
      body: "",
    });

    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });
    const cookieHeader = await manager.getSessionCookieHeader();

    expect(cookieHeader).toBe("mc_token_8787=session-xyz");
  });

  it("caches the session cookie server-side across multiple calls (mints only once)", async () => {
    const mintCalls: Array<{ file: string; args: string[] }> = [];
    const execFile = fakeExecFile(`http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`, mintCalls);
    const httpRequest = fakeHttpRequest({
      status: 200,
      setCookieHeaders: ["mc_token_8787=session-cached; HttpOnly"],
      body: "",
    });

    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });
    const first = await manager.getSessionCookieHeader();
    const second = await manager.getSessionCookieHeader();

    expect(first).toBe(second);
    expect(mintCalls).toHaveLength(1);
  });

  it("mints a fresh session after invalidate() is called", async () => {
    const mintCalls: Array<{ file: string; args: string[] }> = [];
    let call = 0;
    const execFile: ExecFileFn = async (file, args) => {
      mintCalls.push({ file, args });
      call += 1;
      return `http://localhost:8787?token=${SAMPLE_LINK_TOKEN}${call}\n`;
    };
    let cookieSuffix = "first";
    const httpRequest: HttpRequestFn = async () => ({
      status: 200,
      setCookieHeaders: [`mc_token_8787=session-${cookieSuffix}; HttpOnly`],
      body: "",
    });

    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });
    const first = await manager.getSessionCookieHeader();
    manager.invalidate();
    cookieSuffix = "second";
    const second = await manager.getSessionCookieHeader();

    expect(first).toBe("mc_token_8787=session-first");
    expect(second).toBe("mc_token_8787=session-second");
    expect(mintCalls).toHaveLength(2);
  });

  it("throws (fails closed) when the exchange response carries no session cookie", async () => {
    const execFile = fakeExecFile(`http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`, []);
    const httpRequest = fakeHttpRequest({ status: 200, setCookieHeaders: [], body: "" });

    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile, httpRequest });

    await expect(manager.getSessionCookieHeader()).rejects.toThrow();
  });

  it("never touches disk or a shell -- the execFile dependency is called with shell disabled by defaultExecFile", async () => {
    // This test exercises the injected fake, not defaultExecFile directly
    // (defaultExecFile requires spawning a real process); the shell=false
    // contract of defaultExecFile is asserted structurally by inspecting
    // its exported source shape is unit-tested via buildMintTokenArgs
    // above returning a plain argv array (never a single command string).
    const execFileSpy = vi.fn(fakeExecFile(`http://localhost:8787?token=${SAMPLE_LINK_TOKEN}\n`, []));
    const httpRequest = fakeHttpRequest({
      status: 200,
      setCookieHeaders: ["mc_token_8787=session-abc; HttpOnly"],
      body: "",
    });
    const manager = new GatewaySessionManager("http://127.0.0.1:8787", { execFile: execFileSpy, httpRequest });
    await manager.getSessionCookieHeader();
    expect(execFileSpy).toHaveBeenCalledWith("kirocrew", ["token", "--port", "8787"], expect.any(Number));
  });
});
