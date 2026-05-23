import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FAPI = "https://fapi.binance.com";

// Binance allows up to 1500 klines per call. We paginate server-side up to
// MAX_PAGES so a single request can return enough bars to cover a multi-day
// live trading window (e.g. ~52 days of 15m bars at 5 pages).
const PAGE_LIMIT = 1500;
const MAX_PAGES = 6;

const SYMBOL_RE = /^[A-Z0-9]{4,32}$/;
const INTERVAL_SET = new Set([
  "1m", "3m", "5m", "15m", "30m",
  "1h", "2h", "4h", "6h", "8h", "12h",
  "1d", "3d", "1w", "1M",
]);
const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "3m": 3 * 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "30m": 30 * 60_000,
  "1h": 60 * 60_000,
  "2h": 2 * 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "6h": 6 * 60 * 60_000,
  "8h": 8 * 60 * 60_000,
  "12h": 12 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
  "3d": 3 * 24 * 60 * 60_000,
  "1w": 7 * 24 * 60 * 60_000,
  "1M": 30 * 24 * 60 * 60_000,
};

type Candle = {
  open_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  close_time: number;
};

function parseCandle(row: unknown): Candle | null {
  if (!Array.isArray(row) || row.length < 7) return null;
  const open_time = Number(row[0]);
  const open = parseFloat(String(row[1]));
  const high = parseFloat(String(row[2]));
  const low = parseFloat(String(row[3]));
  const close = parseFloat(String(row[4]));
  const volume = parseFloat(String(row[5]));
  const close_time = Number(row[6]);
  if (
    !Number.isFinite(open_time) ||
    !Number.isFinite(close_time) ||
    !Number.isFinite(open) ||
    !Number.isFinite(high) ||
    !Number.isFinite(low) ||
    !Number.isFinite(close)
  ) {
    return null;
  }
  return {
    open_time,
    open,
    high,
    low,
    close,
    volume: Number.isFinite(volume) ? volume : 0,
    close_time,
  };
}

async function fetchPage(
  symbol: string,
  interval: string,
  startTime: number,
  endTime: number,
): Promise<Candle[]> {
  const url =
    `${FAPI}/fapi/v1/klines?symbol=${symbol}` +
    `&interval=${interval}` +
    `&startTime=${startTime}` +
    `&endTime=${endTime}` +
    `&limit=${PAGE_LIMIT}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Binance fapi ${res.status}`);
  }
  const data = (await res.json()) as unknown;
  if (!Array.isArray(data)) {
    throw new Error("Binance fapi: expected array");
  }
  const out: Candle[] = [];
  for (const row of data) {
    const c = parseCandle(row);
    if (c) out.push(c);
  }
  return out;
}

export async function GET(req: NextRequest): Promise<Response> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const sp = req.nextUrl.searchParams;
  const symbol = (sp.get("symbol") ?? "").trim().toUpperCase();
  const interval = (sp.get("interval") ?? "").trim();
  const startTimeRaw = sp.get("startTime");
  const endTimeRaw = sp.get("endTime");

  if (!SYMBOL_RE.test(symbol)) {
    return NextResponse.json({ error: "Invalid symbol" }, { status: 400 });
  }
  if (!INTERVAL_SET.has(interval)) {
    return NextResponse.json({ error: "Invalid interval" }, { status: 400 });
  }
  const startTime = startTimeRaw != null ? Number(startTimeRaw) : NaN;
  const endTime = endTimeRaw != null ? Number(endTimeRaw) : Date.now();
  if (!Number.isFinite(startTime) || startTime <= 0) {
    return NextResponse.json({ error: "Invalid startTime" }, { status: 400 });
  }
  if (!Number.isFinite(endTime) || endTime <= startTime) {
    return NextResponse.json({ error: "Invalid endTime" }, { status: 400 });
  }

  const step = INTERVAL_MS[interval] ?? 60_000;

  try {
    const all: Candle[] = [];
    let cursor = startTime;
    for (let i = 0; i < MAX_PAGES; i++) {
      if (cursor >= endTime) break;
      const page = await fetchPage(symbol, interval, cursor, endTime);
      if (page.length === 0) break;
      // Avoid re-appending an overlap on the page boundary.
      const startIdx = all.length > 0 && page[0].open_time === all[all.length - 1].open_time ? 1 : 0;
      for (let j = startIdx; j < page.length; j++) {
        all.push(page[j]);
      }
      const lastOpen = page[page.length - 1].open_time;
      // Next page starts one step after the last bar we have.
      const next = lastOpen + step;
      if (next <= cursor) break; // safety against infinite loop
      cursor = next;
      if (page.length < PAGE_LIMIT) break;
    }
    return NextResponse.json(
      { symbol, interval, candles: all },
      { status: 200, headers: { "cache-control": "private, max-age=10" } },
    );
  } catch (e) {
    const message = e instanceof Error ? e.message : "Upstream error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
