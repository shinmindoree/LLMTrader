import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";
import { fetchFutures24hrMap } from "@/lib/server/binanceFutures24hr";
import {
  FUTURES_TICKER_CACHE_MAX_AGE_MS,
  readFuturesTickerCache,
} from "@/lib/server/futuresTickerCache";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_SYMBOLS = 24;
const SYMBOL_RE = /^[A-Z0-9]{4,32}$/;

export async function GET(req: NextRequest): Promise<Response> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const raw = req.nextUrl.searchParams.get("symbols") ?? "";
  const parts = raw
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter((s) => SYMBOL_RE.test(s));

  const unique = [...new Set(parts)].slice(0, MAX_SYMBOLS);
  if (unique.length === 0) {
    return NextResponse.json({ error: "Missing or invalid symbols" }, { status: 400 });
  }

  try {
    const tickers: Record<string, { last: number; pct24h: number }> = {};
    const now = Date.now();

    const cached = await readFuturesTickerCache();
    const cacheFresh =
      cached && now - cached.updatedAt <= FUTURES_TICKER_CACHE_MAX_AGE_MS;

    if (cacheFresh && cached) {
      for (const sym of unique) {
        const row = cached.tickers[sym];
        if (row && Number.isFinite(row.last)) {
          tickers[sym] = { last: row.last, pct24h: row.pct24h };
        }
      }
    }

    const missing = unique.filter((s) => !tickers[s]);
    if (missing.length > 0) {
      const fresh = await fetchFutures24hrMap(missing);
      for (const [sym, row] of Object.entries(fresh)) {
        tickers[sym] = row;
      }
    }

    return NextResponse.json(
      { tickers },
      { status: 200, headers: { "cache-control": "private, max-age=5" } },
    );
  } catch (e) {
    const message = e instanceof Error ? e.message : "Upstream error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
