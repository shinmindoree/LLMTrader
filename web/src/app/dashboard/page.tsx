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

export default function DashboardPage() {
  const { t } = useI18n();
  const [strategyCount, setStrategyCount] = useState<number>(0);
  const [backtestCount, setBacktestCount] = useState<number>(0);
  const [runningLiveCount, setRunningLiveCount] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);

  useEffect(() => {
    Promise.all([
      listStrategies().then((s) => setStrategyCount(s.length)),
      listJobs({ type: "BACKTEST", limit: 200 }).then((j) => setBacktestCount(j.length)),
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

  return (
    <main className="w-full px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">{t.dashboard.title}</h1>
      <p className="mt-2 text-sm text-[#868993]">{t.dashboard.subtitle}</p>

      <div className="mt-6 flex flex-wrap gap-3">
        {exchangesWithStatus.map((ex) => (
          <span
            key={ex.id}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium ${
              ex.connected
                ? "bg-[#26a69a]/15 text-[#26a69a]"
                : "bg-[#ef5350]/15 text-[#ef5350]"
            }`}
          >
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                ex.connected ? "bg-[#26a69a]" : "bg-[#ef5350]"
              }`}
            />
            {ex.label}
            {ex.connected ? null : " (연결안됨)"}
          </span>
        ))}
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <Link
          href="/strategies"
          className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] transition-colors"
        >
          <div className="text-xs text-[#868993]">생성된 전략 수</div>
          <div className="mt-1 text-2xl font-semibold text-[#d1d4dc]">
            {loading ? "..." : strategyCount}
          </div>
        </Link>
        <Link
          href="/backtest"
          className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] transition-colors"
        >
          <div className="text-xs text-[#868993]">진행한 백테스트 수</div>
          <div className="mt-1 text-2xl font-semibold text-[#d1d4dc]">
            {loading ? "..." : backtestCount}
          </div>
        </Link>
        <Link
          href="/live"
          className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#26a69a] transition-colors"
        >
          <div className="text-xs text-[#868993]">실행 중인 Live</div>
          <div className="mt-1 text-2xl font-semibold text-[#26a69a]">
            {loading ? "..." : runningLiveCount}
          </div>
        </Link>
      </div>

      <AssetOverviewPanel keysStatus={keysStatus} />
    </main>
  );
}
