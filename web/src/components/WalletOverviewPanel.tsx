"use client";

import { useState } from "react";
import useSWR from "swr";
import { getWalletOverview } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { WalletOverview, WalletBalance } from "@/lib/types";
import { TimeCell } from "@/components/TimeCell";

const REFRESH_MS = 30_000;

const WALLET_COLORS: Record<string, string> = {
  futures: "#2962ff",
  spot: "#26a69a",
  earn: "#f0b90b",
};

const WALLET_BG: Record<string, string> = {
  futures: "bg-[#2962ff]/10",
  spot: "bg-[#26a69a]/10",
  earn: "bg-[#f0b90b]/10",
};

const WALLET_TEXT: Record<string, string> = {
  futures: "text-[#2962ff]",
  spot: "text-[#26a69a]",
  earn: "text-[#f0b90b]",
};

const fmt = (v: number) =>
  v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtSigned = (v: number) => `${v >= 0 ? "+" : "-"}$${fmt(Math.abs(v))}`;

function BarSegment({ pct, color }: { pct: number; color: string }) {
  return (
    <div
      style={{ width: `${Math.max(pct, 0)}%`, backgroundColor: color, transition: "width 0.4s ease" }}
      className="h-full first:rounded-l-full last:rounded-r-full"
    />
  );
}

function WalletRow({ item, unrealizedLabel }: { item: WalletBalance; unrealizedLabel: string }) {
  const color = WALLET_COLORS[item.wallet] ?? "#868993";
  const bgClass = WALLET_BG[item.wallet] ?? "bg-[#2a2e39]";
  const textClass = WALLET_TEXT[item.wallet] ?? "text-[#868993]";
  const hasUnrealized = Math.abs(item.unrealized_pnl) >= 0.01;
  const pnlClass = item.unrealized_pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";

  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-[#2a2e39] last:border-0">
      <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${bgClass}`}>
        <div className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
      </div>
      <div className="flex-1 min-w-0">
        <div className={`text-sm font-medium ${textClass}`}>{item.label}</div>
        <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-[#2a2e39]">
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.max(item.pct, 0)}%`, backgroundColor: color, transition: "width 0.4s ease" }}
          />
        </div>
      </div>
      <div className="text-right shrink-0">
        <div className="text-sm font-semibold text-[#d1d4dc]">${fmt(item.balance_usdt)}</div>
        {hasUnrealized ? (
          <div className="text-xs text-[#868993]">
            {item.pct.toFixed(1)}% · <span className={pnlClass}>{unrealizedLabel} {fmtSigned(item.unrealized_pnl)}</span>
          </div>
        ) : (
          <div className="text-xs text-[#868993]">{item.pct.toFixed(1)}%</div>
        )}
      </div>
    </div>
  );
}

export function WalletOverviewPanel() {
  const { t } = useI18n();
  const [refreshing, setRefreshing] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<WalletOverview>(
    "walletOverview",
    () => getWalletOverview(),
    { refreshInterval: REFRESH_MS, dedupingInterval: 10_000, shouldRetryOnError: false },
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await mutate();
    } finally {
      setRefreshing(false);
    }
  };

  const wo = t.walletOverview;

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-[#d1d4dc]">{wo.title}</h3>
          <p className="mt-0.5 text-xs text-[#868993]">{wo.subtitle}</p>
        </div>
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={refreshing || isLoading}
          className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-1.5 text-xs text-[#d1d4dc] hover:border-[#2962ff] hover:bg-[#252936] transition-colors disabled:opacity-60"
        >
          {refreshing ? wo.refreshing : wo.refresh}
        </button>
      </div>

      {data?.as_of && (
        <div className="mt-1 text-xs text-[#868993]">
          {wo.lastUpdated} <TimeCell value={data.as_of} />
        </div>
      )}

      {isLoading && !data && (
        <div className="mt-4 h-32 animate-pulse rounded bg-[#131722]" />
      )}

      {error && (
        <div className="mt-4 rounded border border-[#ef5350]/40 bg-[#2d1f1f] px-4 py-3 text-sm text-[#ef5350]">
          {String(error)}
        </div>
      )}

      {data?.error && (
        <div className="mt-4 rounded border border-[#efb74d]/40 bg-[#2d2718] px-4 py-3 text-sm text-[#efb74d]">
          {data.error}
        </div>
      )}

      {data && !data.error && (
        <>
          {/* Total AUM bar */}
          <div className="mt-4">
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="text-xs text-[#868993]">{wo.totalLabel}</span>
              <span className="text-xl font-bold text-[#d1d4dc]">${fmt(data.total_usdt)}</span>
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[#131722]">
              {data.wallets.map((w) => (
                <BarSegment key={w.wallet} pct={w.pct} color={WALLET_COLORS[w.wallet] ?? "#555"} />
              ))}
            </div>
          </div>

          {/* Wallet rows */}
          <div className="mt-3">
            {data.wallets.map((w) => (
              <WalletRow key={w.wallet} item={w} unrealizedLabel={wo.unrealizedLabel} />
            ))}
          </div>
          <p className="mt-2 text-[11px] text-[#5d6573]">{wo.includesPositions}</p>
        </>
      )}
    </div>
  );
}
