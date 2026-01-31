"use client";

import { useEffect, useMemo, useState } from "react";

import type { JobEvent, JobStatus, JobType } from "@/lib/types";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

const clampPct = (value: number): number => Math.max(0, Math.min(100, value));

function formatPct(value: number | null): string {
  if (value === null) return "-";
  return `${value.toFixed(1)}%`;
}

export function JobProgressGauge({
  jobId,
  jobType,
  status,
}: {
  jobId: string;
  jobType: JobType;
  status: JobStatus;
}) {
  const [dataFetchPct, setDataFetchPct] = useState<number | null>(null);
  const [backtestPct, setBacktestPct] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const url = useMemo(() => `/api/backend/api/jobs/${jobId}/events/stream?after_event_id=0`, [jobId]);
  const finished = FINISHED_STATUSES.has(status);

  useEffect(() => {
    if (jobType !== "BACKTEST") return;
    const es = new EventSource(url);
    es.onopen = () => {
      setConnected(true);
      setError(null);
    };
    es.onerror = () => {
      setConnected(false);
      setError("SSE disconnected");
    };
    es.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as JobEvent;
        if (ev.kind !== "PROGRESS") return;
        const pctRaw = (ev.payload as { pct?: number } | null)?.pct;
        if (typeof pctRaw !== "number" || !Number.isFinite(pctRaw)) return;
        const pct = clampPct(pctRaw);
        if (ev.message === "DATA_FETCH") {
          setDataFetchPct((prev) => (prev === null ? pct : Math.max(prev, pct)));
        } else if (ev.message === "BACKTEST_PROGRESS") {
          setBacktestPct((prev) => (prev === null ? pct : Math.max(prev, pct)));
        } else {
          setBacktestPct((prev) => (prev === null ? pct : Math.max(prev, pct)));
        }
      } catch {
        // ignore
      }
    };
    return () => es.close();
  }, [jobType, url]);

  if (jobType !== "BACKTEST") return null;

  if (finished && dataFetchPct === null && backtestPct === null) {
    return null;
  }

  const showFetch = dataFetchPct !== null || (!finished && backtestPct === null);
  const showBacktest = backtestPct !== null || !finished;
  const displayFetchPct = dataFetchPct !== null ? (finished ? 100 : dataFetchPct) : null;
  const displayBacktestPct = backtestPct !== null ? (finished ? 100 : backtestPct) : null;

  return (
    <section className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
      <div className="flex items-center justify-between text-xs text-[#868993]">
        <span>Progress</span>
        <span>
          {connected ? "live" : "offline"}
          {error ? ` • ${error}` : ""}
        </span>
      </div>

      <div className="mt-3 space-y-3">
        {showFetch ? (
          <div>
            <div className="mb-1 flex items-center justify-between text-xs text-[#d1d4dc]">
              <span>Data Fetch</span>
              <span className="text-[#868993]">{formatPct(displayFetchPct)}</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[#0f141f]">
              <div
                className="h-full rounded-full bg-[#2962ff] transition-[width] duration-300"
                style={{ width: `${displayFetchPct ?? 0}%` }}
              />
            </div>
          </div>
        ) : null}

        {showBacktest ? (
          <div>
            <div className="mb-1 flex items-center justify-between text-xs text-[#d1d4dc]">
              <span>Backtest Run</span>
              <span className="text-[#868993]">{formatPct(displayBacktestPct)}</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[#0f141f]">
              <div
                className="h-full rounded-full bg-[#26a69a] transition-[width] duration-300"
                style={{ width: `${displayBacktestPct ?? 0}%` }}
              />
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
