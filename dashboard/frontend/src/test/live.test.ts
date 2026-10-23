/** Tests for the live feed and API client.
 *
 * Reconnection and fallback are tested because a dashboard that silently stops
 * updating is worse than one that reports being disconnected: stale numbers
 * are indistinguishable from current ones.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLive } from "../hooks/useLive";
import { ApiError, api } from "../api/client";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
  fail() {
    this.readyState = 3;
    this.onclose?.();
  }
  close() {
    this.closed = true;
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("useLive", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports connecting, then open, and delivers snapshots", async () => {
    const { result } = renderHook(() => useLive(true));
    expect(result.current.state).toBe("connecting");

    act(() => FakeSocket.instances[0].open());
    expect(result.current.state).toBe("open");

    act(() =>
      FakeSocket.instances[0].emit({ type: "snapshot", t: 1, mock_mode: false }),
    );
    expect(result.current.snapshot?.type).toBe("snapshot");
    expect(result.current.lastUpdate).not.toBeNull();
  });

  it("surfaces backend errors without discarding the last good snapshot", () => {
    const { result } = renderHook(() => useLive(true));
    act(() => FakeSocket.instances[0].open());
    act(() => FakeSocket.instances[0].emit({ type: "snapshot", t: 1 }));
    act(() =>
      FakeSocket.instances[0].emit({ type: "error", t: 2, detail: "read failed" }),
    );
    expect(result.current.error).toBe("read failed");
    expect(result.current.snapshot?.t).toBe(1);
  });

  it("reconnects with backoff after an unexpected close", () => {
    const { result } = renderHook(() => useLive(true));
    act(() => FakeSocket.instances[0].open());
    act(() => FakeSocket.instances[0].fail());

    expect(result.current.state).toBe("reconnecting");
    expect(result.current.attempts).toBe(1);

    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(FakeSocket.instances.length).toBe(2);
  });

  it("falls back to polling after repeated websocket failures", async () => {
    const snapshotSpy = vi
      .spyOn(api, "snapshot")
      .mockResolvedValue({ type: "snapshot", t: 42 } as never);

    const { result } = renderHook(() => useLive(true));
    // Three consecutive failures trip the fallback.
    for (let i = 0; i < 3; i += 1) {
      act(() => FakeSocket.instances[FakeSocket.instances.length - 1].fail());
      act(() => {
        vi.advanceTimersByTime(16_000);
      });
    }
    expect(result.current.state).toBe("polling");
    expect(snapshotSpy).toHaveBeenCalled();
  });

  it("stops cleanly when disabled", () => {
    const { result } = renderHook(() => useLive(false));
    expect(result.current.state).toBe("closed");
    expect(FakeSocket.instances.length).toBe(0);
  });
});

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("raises a clear error when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    await expect(api.health()).rejects.toBeInstanceOf(ApiError);
    await expect(api.health()).rejects.toThrow(/Is the backend running/);
  });

  it("propagates the backend's detail message and status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({ detail: "An experiment is already running." }),
      }),
    );
    try {
      await api.experimentStart({
        variant: "A", workload: "light", seed: 1, duration_s: 30,
      });
      throw new Error("expected the call to reject");
    } catch (cause) {
      expect(cause).toBeInstanceOf(ApiError);
      expect((cause as ApiError).status).toBe(409);
      expect((cause as ApiError).message).toMatch(/already running/);
    }
  });

  it("returns parsed JSON on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({ ok: true, mock_mode: false }),
      }),
    );
    await expect(api.health()).resolves.toMatchObject({ ok: true });
  });
});
