const FAPI = "https://fapi.binance.com";

export type FuturesTickerFields = { last: number; pct24h: number };

function parseRow(j: Record<string, unknown>): { symbol: string; last: number; pct24h: number } | null {
  const symbol = typeof j.symbol === "string" ? j.symbol : "";
  const last = parseFloat(String(j.lastPrice ?? j.c ?? ""));
  const pct = parseFloat(String(j.priceChangePercent ?? j.P ?? ""));
  if (!symbol || !Number.isFinite(last)) return null;
  return { symbol, last, pct24h: Number.isFinite(pct) ? pct : 0 };
}

/** One HTTP call; filters to requested symbols (USD-M 24h ticker list). */
export async function fetchFutures24hrMap(symbols: string[]): Promise<Record<string, FuturesTickerFields>> {
  if (symbols.length === 0) return {};
  const want = new Set(symbols.map((s) => s.toUpperCase()));
  const res = await fetch(`${FAPI}/fapi/v1/ticker/24hr`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Binance fapi ${res.status}`);
  const data = (await res.json()) as unknown;
  if (!Array.isArray(data)) throw new Error("Binance fapi: expected array");
  const out: Record<string, FuturesTickerFields> = {};
  for (const item of data) {
    if (!item || typeof item !== "object") continue;
    const row = parseRow(item as Record<string, unknown>);
    if (row && want.has(row.symbol)) {
      out[row.symbol] = { last: row.last, pct24h: row.pct24h };
    }
  }
  return out;
}
