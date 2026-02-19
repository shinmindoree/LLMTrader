"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listJobs, getBillingStatus, getBinanceKeysStatus } from "@/lib/api";
import { BinanceAccountPanel } from "@/components/BinanceAccountPanel";
import type { Job, BillingStatus, BinanceKeysStatus } from "@/lib/types";

export default function DashboardPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);

  useEffect(() => {
    listJobs()
      .then(setJobs)
      .catch(() => {})
      .finally(() => setLoading(false));
    getBillingStatus().then(setBilling).catch(() => {});
    getBinanceKeysStatus().then(setKeysStatus).catch(() => {});
  }, []);

  const stats = {
    total: jobs.length,
    running: jobs.filter((j) => j.status === "RUNNING").length,
    succeeded: jobs.filter((j) => j.status === "SUCCEEDED").length,
    failed: jobs.filter((j) => j.status === "FAILED").length,
  };

  return (
    <main className="w-full px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">LLMTrader Dashboard</h1>
      <p className="mt-2 text-sm text-[#868993]">Control center for backtesting and live trading</p>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] transition-colors">
          <div className="text-xs text-[#868993]">Total Jobs</div>
          <div className="mt-1 text-2xl font-semibold text-[#d1d4dc]">
            {loading ? "..." : stats.total}
          </div>
        </div>
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#26a69a] transition-colors">
          <div className="text-xs text-[#868993]">Running</div>
          <div className="mt-1 text-2xl font-semibold text-[#26a69a]">
            {loading ? "..." : stats.running}
          </div>
        </div>
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] transition-colors">
          <div className="text-xs text-[#868993]">Succeeded</div>
          <div className="mt-1 text-2xl font-semibold text-[#2962ff]">
            {loading ? "..." : stats.succeeded}
          </div>
        </div>
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#ef5350] transition-colors">
          <div className="text-xs text-[#868993]">Failed</div>
          <div className="mt-1 text-2xl font-semibold text-[#ef5350]">
            {loading ? "..." : stats.failed}
          </div>
        </div>
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {billing && (
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] transition-colors"
            href="/billing"
          >
            <div className="flex items-center justify-between">
              <div className="text-xs text-[#868993]">Current Plan</div>
              <span className={`text-xs font-semibold uppercase ${
                billing.plan === "enterprise" ? "text-[#ff9800]" :
                billing.plan === "pro" ? "text-[#2962ff]" :
                "text-[#868993]"
              }`}>
                {billing.plan}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-[#868993]">Backtests: </span>
                <span className="text-[#d1d4dc]">{billing.usage.backtest_this_month}/{billing.limits.max_backtest_per_month >= 9999 ? "∞" : billing.limits.max_backtest_per_month}</span>
              </div>
              <div>
                <span className="text-[#868993]">LLM Gen: </span>
                <span className="text-[#d1d4dc]">{billing.usage.llm_generate_this_month}/{billing.limits.max_llm_generate_per_month >= 9999 ? "∞" : billing.limits.max_llm_generate_per_month}</span>
              </div>
            </div>
          </Link>
        )}
        <Link
          className={`rounded-lg border bg-[#1e222d] p-5 transition-colors ${
            keysStatus?.configured
              ? "border-[#2a2e39] hover:border-[#26a69a]"
              : "border-[#ef5350]/30 hover:border-[#ef5350]"
          }`}
          href="/settings"
        >
          <div className="flex items-center justify-between">
            <div className="text-xs text-[#868993]">Binance API Keys</div>
            {keysStatus?.configured ? (
              <span className="flex items-center gap-1 text-xs text-[#26a69a]">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                Connected
              </span>
            ) : (
              <span className="text-xs text-[#ef5350]">Not configured</span>
            )}
          </div>
          <div className="mt-2 text-sm text-[#868993]">
            {keysStatus?.configured
              ? `Key: ${keysStatus.api_key_masked || "****"}`
              : "Set up your Binance API keys to start trading"
            }
          </div>
        </Link>
      </div>

      <BinanceAccountPanel />

      <div className="mt-8">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-4">Quick Actions</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/strategies"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Strategies</div>
            <div className="mt-1 text-sm text-[#868993]">Create and manage strategies</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/live"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Live Runs</div>
            <div className="mt-1 text-sm text-[#868993]">Monitor active and recent live runs</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/backtest"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Backtest Runs</div>
            <div className="mt-1 text-sm text-[#868993]">Review backtest history and outcomes</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#ef5350] hover:bg-[#2d1f1f] transition-colors"
            href="/live"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">New Live Run</div>
            <div className="mt-1 text-sm text-[#868993]">Start live trading on testnet</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/backtest"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">New Backtest</div>
            <div className="mt-1 text-sm text-[#868993]">Run a new backtest</div>
          </Link>
        </div>
      </div>
    </main>
  );
}
