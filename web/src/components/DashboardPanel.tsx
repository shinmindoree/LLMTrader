"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { listStrategies, listJobs, getBinanceKeysStatus } from "@/lib/api";
import { AssetOverviewPanel } from "@/components/AssetOverviewPanel";
import type { BinanceKeysStatus } from "@/lib/types";

const EXCHANGES = [
  { id: "binance", label: "Binance", connected: true },
  { id: "bybit", label: "Bybit", connected: false },
  { id: "okx", label: "OKX", connected: false },
  { id: "kraken", label: "Kraken", connected: false },
] as const;

export function DashboardPanel() {
  const { t } = useI18n();
  const [strategyCount, setStrategyCount] = useState(0);
  const [backtestCount, setBacktestCount] = useState(0);
  const [runningLiveCount, setRunningLiveCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);

  useEffect(() => {
    Promise.all([
      listStrategies().then((s) => setStrategyCount(s.length)),
      listJobs({ type: "BACKTEST", limit: 200 }).then((j) =>
        setBacktestCount(j.length),
      ),
      listJobs({ type: "LIVE", limit: 200 }).then((j) =>
        setRunningLiveCount(j.filter((x) => x.status === "RUNNING").length),
      ),
      getBinanceKeysStatus().then(setKeysStatus),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const exchangesWithStatus = EXCHANGES.map((ex) =>
    ex.id === "binance"
      ? { ...ex, connected: !!keysStatus?.configured }
      : ex,
  );

  const stats = [
    {
      label: t.dashboard.strategyCount,
      value: strategyCount,
      href: "/strategies",
      color: "text-[#d1d4dc]",
      hoverBorder: "hover:border-[#2962ff]",
    },
    {
      label: t.dashboard.backtestCount,
      value: backtestCount,
      href: "/backtest",
      color: "text-[#d1d4dc]",
      hoverBorder: "hover:border-[#2962ff]",
    },
    {
      label: t.dashboard.runningLive,
      value: runningLiveCount,
      href: "/live",
      color: "text-[#26a69a]",
      hoverBorder: "hover:border-[#26a69a]",
    },
  ];

  return (
    <aside className="flex h-full flex-col border-l border-[#2a2e39] bg-[#1e222d]">
      <div className="border-b border-[#2a2e39] px-4 py-3">
        <h2 className="text-sm font-semibold text-[#d1d4dc]">Dashboard</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        <div className="flex flex-wrap gap-1.5">
          {exchangesWithStatus.map((ex) => (
            <span
              key={ex.id}
              className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                ex.connected
                  ? "bg-[#26a69a]/15 text-[#26a69a]"
                  : "bg-[#ef5350]/15 text-[#ef5350]"
              }`}
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  ex.connected ? "bg-[#26a69a]" : "bg-[#ef5350]"
                }`}
              />
              {ex.label}
            </span>
          ))}
        </div>

        <div className="space-y-2">
          {stats.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              className={`block rounded-lg border border-[#2a2e39] bg-[#131722] p-3 transition-colors ${s.hoverBorder}`}
            >
              <div className="text-xs text-[#868993]">{s.label}</div>
              <div className={`mt-0.5 text-xl font-semibold ${s.color}`}>
                {loading ? "..." : s.value}
              </div>
            </Link>
          ))}
        </div>

        <AssetOverviewPanel keysStatus={keysStatus} />
      </div>
    </aside>
  );
}
