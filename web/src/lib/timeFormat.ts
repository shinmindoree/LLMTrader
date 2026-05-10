/**
 * Unified time formatting for the trading UI.
 *
 * Binance-style display: `YYYY-MM-DD HH:mm:ss` (24h). The Asia/Seoul zone
 * is used by default, matching the user's locale and Binance's KR client
 * default. Click-to-toggle is implemented via the `useTimezone` hook so
 * every TimeCell instance flips together.
 *
 * Source data is always UTC ms (epoch milliseconds), matching Binance's
 * REST/WebSocket payloads. Never pass a localized string in here — that
 * would be re-localized and produce double-conversion bugs.
 */

import { useCallback, useEffect, useState } from "react";

export type Timezone = "KST" | "UTC";

const STORAGE_KEY = "llmtrader.tz";
const EVENT_NAME = "llmtrader.tz.change";

const DEFAULT_TZ: Timezone = "KST";

const FORMATTERS: Record<Timezone, Intl.DateTimeFormat> = {
  KST: new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }),
  UTC: new Intl.DateTimeFormat("en-CA", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }),
};

/** Format an epoch-ms timestamp as `YYYY-MM-DD HH:mm:ss` in the given zone. */
export function formatBinanceTime(
  ms: number | null | undefined,
  tz: Timezone = DEFAULT_TZ,
): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "-";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "-";
  // en-CA gives `YYYY-MM-DD, HH:mm:ss`; we replace the comma with a space
  // to match Binance's `YYYY-MM-DD HH:mm:ss` exactly.
  return FORMATTERS[tz].format(d).replace(",", "");
}

/** Convenience: format an ISO/string/number timestamp. Strings are parsed as UTC. */
export function formatBinanceTimeAny(
  value: number | string | null | undefined,
  tz: Timezone = DEFAULT_TZ,
): string {
  if (value === null || value === undefined) return "-";
  const ms = typeof value === "number" ? value : Date.parse(value);
  if (!Number.isFinite(ms)) return "-";
  return formatBinanceTime(ms, tz);
}

/** Get the persisted timezone preference (server-safe). */
export function readStoredTimezone(): Timezone {
  if (typeof window === "undefined") return DEFAULT_TZ;
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "UTC" || v === "KST" ? v : DEFAULT_TZ;
  } catch {
    return DEFAULT_TZ;
  }
}

/** Set the preference and broadcast to all TimeCell instances on the page. */
export function setStoredTimezone(tz: Timezone): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, tz);
  } catch {
    // ignore quota / disabled storage
  }
  window.dispatchEvent(new CustomEvent<Timezone>(EVENT_NAME, { detail: tz }));
}

/**
 * React hook: returns `[tz, toggleTz, setTz]` that stays in sync across
 * components on the same page (custom event) and across tabs (storage event).
 */
export function useTimezone(): {
  tz: Timezone;
  toggle: () => void;
  setTz: (tz: Timezone) => void;
} {
  const [tz, setTzState] = useState<Timezone>(DEFAULT_TZ);

  // Hydrate from localStorage on mount (avoids SSR mismatch).
  useEffect(() => {
    setTzState(readStoredTimezone());
    const onCustom = (ev: Event) => {
      const detail = (ev as CustomEvent<Timezone>).detail;
      if (detail === "KST" || detail === "UTC") setTzState(detail);
    };
    const onStorage = (ev: StorageEvent) => {
      if (ev.key === STORAGE_KEY) {
        setTzState(ev.newValue === "UTC" ? "UTC" : "KST");
      }
    };
    window.addEventListener(EVENT_NAME, onCustom);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVENT_NAME, onCustom);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const setTz = useCallback((next: Timezone) => {
    setStoredTimezone(next);
  }, []);

  const toggle = useCallback(() => {
    setStoredTimezone(tz === "KST" ? "UTC" : "KST");
  }, [tz]);

  return { tz, toggle, setTz };
}
