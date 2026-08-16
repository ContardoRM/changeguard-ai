import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useControlRoomState } from "./useControlRoomState";

describe("useControlRoomState fixture/live isolation", () => {
  it("fixture mode never calls fetch, even when submitApproval is invoked", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const { result } = renderHook(() => useControlRoomState("WAITING_APPROVAL"));

    expect(result.current.isLive).toBe(false);

    await act(async () => {
      await result.current.submitApproval("approve");
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("exposes the selected fixture's state shape", () => {
    const { result } = renderHook(() => useControlRoomState("SAFE_TO_SHIP"));
    expect(result.current.state.finalVerdict?.status).toBe("SAFE_TO_SHIP");
  });

  it("setFixture switches the displayed fixture", () => {
    const { result } = renderHook(() => useControlRoomState("SAFE_BASELINE"));
    expect(result.current.state.changeBlocked).toBe(false);

    act(() => {
      result.current.setFixture("CHANGE_BLOCKED_REL001");
    });

    expect(result.current.fixtureName).toBe("CHANGE_BLOCKED_REL001");
  });
});
