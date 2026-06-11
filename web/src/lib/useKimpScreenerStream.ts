"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getKimpScreener } from "@/lib/api";
import type { KimpScreenerResponse } from "@/lib/types";

const STREAM_PATH = "/api/backend/api/kimp-arb/stream";
const REST_POLL_MS = 10_000;
const RECONNECT_MAX_MS = 30_000;

export type KimpScreenerStreamStatus = "connecting" | "live" | "fallback" | "error";

type Result = {
  data: KimpScreenerResponse | null;
  error: Error | null;
  isLoading: boolean;
  isValidating: boolean;
  status: KimpScreenerStreamStatus;
  refetch: () => void;
};

function parseSymbols(symbolsKey: string): string[] | undefined {
  if (!symbolsKey) return undefined;
  const symbols = symbolsKey.split(",").map((s) => s.trim()).filter(Boolean);
  return symbols.length > 0 ? symbols : undefined;
}

function buildStreamUrl(symbolsKey: string): string {
  const symbols = parseSymbols(symbolsKey);
  if (!symbols) return STREAM_PATH;
  const params = new URLSearchParams();
  params.set("symbols", symbols.join(","));
  return `${STREAM_PATH}?${params.toString()}`;
}

export function useKimpScreenerStream(symbols?: readonly string[]): Result {
  const [data, setData] = useState<KimpScreenerResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [status, setStatus] = useState<KimpScreenerStreamStatus>("connecting");
  const esRef = useRef<EventSource | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryDelayRef = useRef(1000);
  const symbolsKey = symbols?.join(",") ?? "";

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchSnapshot = useCallback(() => {
    setIsValidating(true);
    void getKimpScreener(parseSymbols(symbolsKey))
      .then((next) => {
        setData(next);
        setError(null);
        setStatus((current) => (current === "live" ? current : "fallback"));
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err : new Error(String(err)));
        setStatus("error");
      })
      .finally(() => setIsValidating(false));
  }, [symbolsKey]);

  const startPolling = useCallback(() => {
    stopPolling();
    fetchSnapshot();
    pollTimerRef.current = setInterval(fetchSnapshot, REST_POLL_MS);
  }, [fetchSnapshot, stopPolling]);

  useEffect(() => {
    let disposed = false;
    let initialTimer: ReturnType<typeof setTimeout> | null = null;

    function scheduleReconnect(connect: () => void) {
      if (disposed) return;
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
      retryTimerRef.current = setTimeout(connect, delay);
    }

    function connect() {
      if (disposed) return;
      setStatus((current) => (current === "live" ? current : "connecting"));
      const es = new EventSource(buildStreamUrl(symbolsKey));
      esRef.current = es;

      es.onopen = () => {
        retryDelayRef.current = 1000;
        setStatus("live");
        stopPolling();
      };

      es.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as KimpScreenerResponse;
          setData(payload);
          setError(null);
          setStatus("live");
          stopPolling();
        } catch (err) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      };

      es.onerror = () => {
        es.close();
        if (esRef.current === es) esRef.current = null;
        if (disposed) return;
        setStatus("fallback");
        startPolling();
        scheduleReconnect(connect);
      };
    }

    initialTimer = setTimeout(() => {
      fetchSnapshot();
      connect();
    }, 0);

    return () => {
      disposed = true;
      if (initialTimer) {
        clearTimeout(initialTimer);
        initialTimer = null;
      }
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      stopPolling();
      esRef.current?.close();
      esRef.current = null;
    };
  }, [fetchSnapshot, startPolling, stopPolling, symbolsKey]);

  return {
    data,
    error,
    isLoading: !data && (isValidating || status === "connecting"),
    isValidating,
    status,
    refetch: fetchSnapshot,
  };
}
