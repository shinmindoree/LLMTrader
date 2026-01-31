"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listJobs } from "@/lib/api";
import type { Job } from "@/lib/types";

export default function Home() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listJobs()
      .then(setJobs)
      .catch(() => {})
      .finally(() => setLoading(false));
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
      <p className="mt-2 text-sm text-[#868993]">Backtest + Live Trading 운영 콘솔</p>

      {/* 통계 카드 */}
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

      {/* 빠른 액션 카드 */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-4">Quick Actions</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/strategies"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Strategies</div>
            <div className="mt-1 text-sm text-[#868993]">전략 목록 확인</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/live"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Live Runs</div>
            <div className="mt-1 text-sm text-[#868993]">라이브 실행 목록/상태</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/backtest"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">Backtest Runs</div>
            <div className="mt-1 text-sm text-[#868993]">백테스트 실행 목록</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#ef5350] hover:bg-[#2d1f1f] transition-colors"
            href="/live"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">New Live Run</div>
            <div className="mt-1 text-sm text-[#868993]">라이브 트레이딩 실행</div>
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            href="/backtest"
          >
            <div className="text-sm font-medium text-[#d1d4dc]">New Backtest</div>
            <div className="mt-1 text-sm text-[#868993]">백테스트 실행</div>
          </Link>
        </div>
      </div>
    </main>
  );
}
