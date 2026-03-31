import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FAPI = "https://fapi.binance.com";
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
    await Promise.all(
      unique.map(async (symbol) => {
        const url = new URL("/fapi/v1/ticker/24hr", FAPI);
        url.searchParams.set("symbol", symbol);
        const res = await fetch(url.toString(), { cache: "no-store" });
        if (!res.ok) return;
        const j = (await res.json()) as Record<string, unknown>;
        const last = parseFloat(String(j.lastPrice ?? j.c ?? ""));
        const pct = parseFloat(String(j.priceChangePercent ?? j.P ?? ""));
        if (Number.isFinite(last)) {
          tickers[symbol] = {
            last,
            pct24h: Number.isFinite(pct) ? pct : 0,
          };
        }
      }),
    );
    return NextResponse.json(
      { tickers },
      { status: 200, headers: { "cache-control": "private, max-age=5" } },
    );
  } catch (e) {
    const message = e instanceof Error ? e.message : "Upstream error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
