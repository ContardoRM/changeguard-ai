import { describe, expect, it, vi } from "vitest";

describe("gateway module fixture/live isolation", () => {
  it("isLiveModeEnabled() is false by default (fixture mode)", async () => {
    // __CONTROL_ROOM_MODE__ is injected by vite.config.ts's `define`; the
    // test environment does not set VITE_CONTROL_ROOM_MODE=live, so this
    // must resolve to "fixture" -- confirming fixture mode is the default
    // and requires no explicit opt-out.
    const { isLiveModeEnabled } = await import("./gateway");
    expect(isLiveModeEnabled()).toBe(false);
  });

  it("never issues a network call when checking live-mode status", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { isLiveModeEnabled } = await import("./gateway");
    isLiveModeEnabled();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
