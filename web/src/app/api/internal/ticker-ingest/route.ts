import { NextRequest, NextResponse } from "next/server";

import { DASHBOARD_FUTURES_WATCH_SYMBOLS } from "@/lib/dashboardFuturesSymbols";
import { fetchFutures24hrMap } from "@/lib/server/binanceFutures24hr";
import { isFuturesTickerCacheConfigured, writeFuturesTickerCache } from "@/lib/server/futuresTickerCache";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  const secret = process.env.TICKER_INGEST_SECRET?.trim();
  if (!secret) {
    return NextResponse.json({ error: "TICKER_INGEST_SECRET not set" }, { status: 503 });
  }
  if (req.headers.get("x-ticker-ingest-secret") !== secret) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!isFuturesTickerCacheConfigured()) {
    return NextResponse.json(
      { error: "Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING" },
      { status: 503 },
    );
  }

  try {
    const symbols = [...DASHBOARD_FUTURES_WATCH_SYMBOLS];
    const tickers = await fetchFutures24hrMap(symbols);
    const updatedAt = Date.now();
    await writeFuturesTickerCache({ updatedAt, tickers });
    return NextResponse.json({
      ok: true,
      count: Object.keys(tickers).length,
      updatedAt,
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Upstream error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
