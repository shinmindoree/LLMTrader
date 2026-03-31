"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const WS_BASE = "wss://fstream.binance.com/stream";
const FALLBACK_AFTER_MS = 12_000;
const REST_POLL_MS = 15_000;

export type FuturesTickerRow = {
  last: number;
  pct24h: number;
  updatedAt: number;
};

export type FuturesTickerStreamStatus = "connecting" | "live" | "fallback" | "error";

export type UseBinanceFuturesTickerStreamResult = {
  bySymbol: Record<string, FuturesTickerRow>;
  status: FuturesTickerStreamStatus;
  refetchRest: () => void;
};

function parseCombinedPayload(msg: unknown): { symbol: string; last: number; pct24h: number } | null {
  if (!msg || typeof msg !== "object") return null;
  const wrap = msg as Record<string, unknown>;
  const data = wrap.data;
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.e !== "24hrTicker") return null;
  const symbol = typeof d.s === "string" ? d.s : "";
  const last = parseFloat(String(d.c ?? ""));
  const pct = parseFloat(String(d.P ?? ""));
  if (!symbol || !Number.isFinite(last)) return null;
  return { symbol, last, pct24h: Number.isFinite(pct) ? pct : 0 };
}

function buildStreamUrl(symbols: readonly string[]): string {
  const streams = symbols.map((s) => `${s.toLowerCase()}@ticker`).join("/");
  return `${WS_BASE}?streams=${streams}`;
}

async function fetchRestTickers(symbols: readonly string[]): Promise<Record<string, FuturesTickerRow>> {
  if (symbols.length === 0) return {};
  const q = symbols.join(",");
  const res = await fetch(`/api/binance/futures-tickers?symbols=${encodeURIComponent(q)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`REST ${res.status}`);
  const data = (await res.json()) as { tickers?: Record<string, { last: number; pct24h: number }> };
  const raw = data.tickers ?? {};
  const now = Date.now();
  const out: Record<string, FuturesTickerRow> = {};
  for (const [sym, row] of Object.entries(raw)) {
    if (row && typeof row.last === "number" && Number.isFinite(row.last)) {
      out[sym] = {
        last: row.last,
        pct24h: typeof row.pct24h === "number" && Number.isFinite(row.pct24h) ? row.pct24h : 0,
        updatedAt: now,
      };
    }
  }
  return out;
}

export function useBinanceFuturesTickerStream(symbols: readonly string[]): UseBinanceFuturesTickerStreamResult {
  const [bySymbol, setBySymbol] = useState<Record<string, FuturesTickerRow>>({});
  const [status, setStatus] = useState<FuturesTickerStreamStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const symbolsKey = symbols.join(",");

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (syms: readonly string[]) => {
      stopPolling();
      const run = () => {
        void fetchRestTickers(syms)
          .then((next) => {
            setBySymbol((prev) => ({ ...prev, ...next }));
            setStatus((s) => (s === "error" ? "fallback" : s));
          })
          .catch(() => setStatus("error"));
      };
      run();
      pollRef.current = setInterval(run, REST_POLL_MS);
    },
    [stopPolling],
  );

  const refetchRest = useCallback(() => {
    void fetchRestTickers(symbols)
      .then((next) => {
        setBySymbol((prev) => ({ ...prev, ...next }));
        setStatus("fallback");
      })
      .catch(() => setStatus("error"));
  }, [symbolsKey]);

  useEffect(() => {
    const syms = symbols.length > 0 ? symbols : [];
    if (syms.length === 0) {
      setStatus("error");
      return;
    }

    setStatus("connecting");
    let gotWsMessage = false;

    if (fallbackTimerRef.current) {
      clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }

    fallbackTimerRef.current = setTimeout(() => {
      if (!gotWsMessage) {
        setStatus("fallback");
        void fetchRestTickers(syms)
          .then((next) => setBySymbol(next))
          .catch(() => setStatus("error"));
        startPolling(syms);
      }
    }, FALLBACK_AFTER_MS);

    const url = buildStreamUrl(syms);
    let cleanedUp = false;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data as string) as unknown;
        const row = parseCombinedPayload(parsed);
        if (!row) return;
        gotWsMessage = true;
        setStatus("live");
        if (fallbackTimerRef.current) {
          clearTimeout(fallbackTimerRef.current);
          fallbackTimerRef.current = null;
        }
        stopPolling();
        const now = Date.now();
        setBySymbol((prev) => ({
          ...prev,
          [row.symbol]: { last: row.last, pct24h: row.pct24h, updatedAt: now },
        }));
      } catch {
        /* ignore */
      }
    };

    ws.onerror = () => {
      if (!cleanedUp) setStatus((s) => (s === "live" ? s : "fallback"));
    };

    ws.onclose = () => {
      if (cleanedUp) return;
      if (!gotWsMessage) {
        setStatus("fallback");
        void fetchRestTickers(syms)
          .then((next) => setBySymbol(next))
          .catch(() => setStatus("error"));
        startPolling(syms);
      } else {
        setStatus("fallback");
        void fetchRestTickers(syms)
          .then((next) => setBySymbol((prev) => ({ ...prev, ...next })))
          .catch(() => setStatus("error"));
        startPolling(syms);
      }
    };

    return () => {
      cleanedUp = true;
      if (fallbackTimerRef.current) {
        clearTimeout(fallbackTimerRef.current);
        fallbackTimerRef.current = null;
      }
      stopPolling();
      ws.close();
      wsRef.current = null;
    };
  }, [symbolsKey, startPolling, stopPolling]);

  return { bySymbol, status, refetchRest };
}
