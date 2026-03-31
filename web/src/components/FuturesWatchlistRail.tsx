"use client";

import { useI18n } from "@/lib/i18n";
import type { FuturesTickerRow, FuturesTickerStreamStatus } from "@/lib/useBinanceFuturesTickerStream";

const linkFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2962ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#131722]";

function formatLast(price: number): string {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (price >= 1) return price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  return price.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 8 });
}

function formatPct(pct: number): string {
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function FuturesWatchlistRail({
  symbols,
  bySymbol,
  status,
  onRetryRest,
}: {
  symbols: readonly string[];
  bySymbol: Record<string, FuturesTickerRow>;
  status: FuturesTickerStreamStatus;
  onRetryRest: () => void;
}) {
  const { t } = useI18n();

  const statusLabel = (() => {
    if (status === "connecting") return t.dashboard.futuresRailConnecting;
    if (status === "live") return t.dashboard.futuresRailLive;
    if (status === "fallback") return t.dashboard.futuresRailDelayed;
    return t.dashboard.futuresRailError;
  })();

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-[#2a2e39] bg-[#131722]/80 pl-3">
      <div className="sticky top-0 z-10 border-b border-[#2a2e39] bg-[#131722] pb-2 pt-1">
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
          {t.dashboard.futuresRailTitle}
        </h2>
        <p className="mt-0.5 text-[10px] text-[#555]">{statusLabel}</p>
        {status === "error" ? (
          <button
            type="button"
            onClick={onRetryRest}
            className={`mt-2 rounded border border-[#2a2e39] px-2 py-1 text-[10px] font-medium text-[#d1d4dc] hover:border-[#2962ff] ${linkFocusRing}`}
          >
            {t.dashboard.futuresRailRetry}
          </button>
        ) : null}
      </div>
      <div className="mt-2 min-h-0 flex-1 space-y-0 overflow-y-auto pr-1" role="list">
        <div className="grid grid-cols-[1fr_auto_auto] gap-x-2 border-b border-[#2a2e39]/80 pb-1 text-[9px] font-medium uppercase tracking-wider text-[#555]">
          <span>{t.dashboard.futuresRailColSymbol}</span>
          <span className="text-right">{t.dashboard.futuresRailColLast}</span>
          <span className="text-right">{t.dashboard.futuresRailColChg}</span>
        </div>
        {symbols.map((sym) => {
          const row = bySymbol[sym];
          const pct = row?.pct24h ?? 0;
          const pctClass = pct > 0 ? "text-[#26a69a]" : pct < 0 ? "text-[#ef5350]" : "text-[#868993]";
          return (
            <div
              key={sym}
              role="listitem"
              className="grid grid-cols-[1fr_auto_auto] gap-x-2 border-b border-[#2a2e39]/50 py-2 text-xs"
            >
              <span className="truncate font-medium text-[#d1d4dc]" title={sym}>
                {sym.replace("USDT", "")}
              </span>
              <span className="text-right tabular-nums text-[#b2b5be]">
                {row ? formatLast(row.last) : "—"}
              </span>
              <span className={`text-right tabular-nums text-[11px] font-medium ${pctClass}`}>
                {row ? formatPct(pct) : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
