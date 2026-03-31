const STABLE_QUOTES = new Set(["USDT", "USDC", "BUSD", "FDUSD", "TUSD"]);

/**
 * Map a spot/futures "currency" code (e.g. BTC) to a USD-M perpetual symbol when quoted in USDT.
 * Returns null for stables or unknown codes.
 */
export function currencyCodeToUsdtPerp(code: string | null | undefined): string | null {
  if (!code || typeof code !== "string") return null;
  const upper = code.trim().toUpperCase();
  if (!upper || STABLE_QUOTES.has(upper)) return null;
  if (!/^[A-Z0-9]{2,20}$/.test(upper)) return null;
  return `${upper}USDT`;
}

export function currencyCodesToUsdtPerps(codes: readonly string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const c of codes) {
    const sym = currencyCodeToUsdtPerp(c);
    if (sym && !seen.has(sym)) {
      seen.add(sym);
      out.push(sym);
    }
  }
  return out;
}
