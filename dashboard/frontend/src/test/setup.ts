import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver; Recharts' ResponsiveContainer needs one.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
  ResizeObserverStub;

// jsdom has no WebSocket. Tests that exercise the live feed install their own
// fake; this stub keeps components that merely construct one from throwing.
class WebSocketStub {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = WebSocketStub.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  close() {
    this.readyState = WebSocketStub.CLOSED;
    this.onclose?.();
  }
  send() {}
}
if (!("WebSocket" in globalThis)) {
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = WebSocketStub;
}
