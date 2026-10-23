/** Live data over a websocket, with automatic reconnect and a polling fallback.
 *
 * The connection state is exposed rather than hidden, because a dashboard that
 * silently keeps showing the last frame after the stream dies is actively
 * misleading: stale numbers look exactly like current ones.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { LiveSnapshot } from "../api/types";

export type ConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "polling"
  | "closed";

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 750;
/** After this many consecutive websocket failures, fall back to polling. */
const FALLBACK_AFTER_ATTEMPTS = 3;
const POLL_INTERVAL_MS = 2_000;

export interface LiveFeed {
  snapshot: LiveSnapshot | null;
  state: ConnectionState;
  lastUpdate: number | null;
  error: string | null;
  attempts: number;
  reconnectNow: () => void;
}

export function useLive(enabled = true): LiveFeed {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const closedRef = useRef(false);

  /** Cancel a pending websocket retry. Deliberately does NOT stop polling:
   *  a retry must not tear down the fallback that is currently the only thing
   *  delivering data. */
  const clearReconnectTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const clearPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const clearTimers = () => {
    clearReconnectTimer();
    clearPolling();
  };

  const startPolling = useCallback(() => {
    if (pollRef.current !== null) return;
    setState("polling");
    const tick = async () => {
      try {
        const payload = await api.snapshot();
        setSnapshot(payload);
        setLastUpdate(Date.now());
        setError(null);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, POLL_INTERVAL_MS);
  }, []);

  const connect = useCallback(() => {
    if (closedRef.current) return;
    clearReconnectTimer();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      startPolling();
      return;
    }
    socketRef.current = socket;
    // Once polling has taken over it is actually delivering data, so keep
    // reporting "polling" while websocket retries happen in the background.
    // Flipping to "reconnecting" would tell the user nothing is arriving when
    // in fact it is.
    setState(
      pollRef.current !== null
        ? "polling"
        : attemptsRef.current === 0
          ? "connecting"
          : "reconnecting",
    );

    socket.onopen = () => {
      attemptsRef.current = 0;
      setAttempts(0);
      setState("open");
      setError(null);
      // The websocket is healthy again, so the polling fallback can stop.
      clearPolling();
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data as string) as LiveSnapshot;
        if (payload.type === "error") {
          setError(payload.detail ?? "backend reported an error");
          return;
        }
        setSnapshot(payload);
        setLastUpdate(Date.now());
        setError(null);
      } catch {
        setError("received a malformed message from the backend");
      }
    };

    socket.onerror = () => {
      /* onclose always follows; retry logic lives there. */
    };

    socket.onclose = () => {
      socketRef.current = null;
      if (closedRef.current) return;
      attemptsRef.current += 1;
      setAttempts(attemptsRef.current);

      if (attemptsRef.current >= FALLBACK_AFTER_ATTEMPTS) {
        // The websocket may be blocked by a proxy; polling still works.
        startPolling();
      } else if (pollRef.current === null) {
        setState("reconnecting");
      }
      const backoff = Math.min(
        MAX_BACKOFF_MS,
        BASE_BACKOFF_MS * 2 ** (attemptsRef.current - 1),
      );
      timerRef.current = window.setTimeout(connect, backoff);
    };
  }, [startPolling]);

  const reconnectNow = useCallback(() => {
    attemptsRef.current = 0;
    setAttempts(0);
    if (socketRef.current) {
      socketRef.current.close();
      return;
    }
    connect();
  }, [connect]);

  useEffect(() => {
    if (!enabled) {
      setState("closed");
      return;
    }
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      clearTimers();
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [enabled, connect]);

  return { snapshot, state, lastUpdate, error, attempts, reconnectNow };
}

/** Simple fetch-on-mount hook with refresh, loading and error states. */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  intervalMs?: number,
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const refresh = useCallback(async () => {
    try {
      const value = await loaderRef.current();
      setData(value);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void refresh();
    if (intervalMs) {
      const id = window.setInterval(refresh, intervalMs);
      return () => window.clearInterval(id);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, refresh };
}
