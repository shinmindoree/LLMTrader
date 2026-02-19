"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { JobEvent, JobStatus, JobType } from "@/lib/types";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const LERP_FACTOR = 0.06;
const SNAP_THRESHOLD = 0.3;

const clampPct = (value: number): number => Math.max(0, Math.min(100, value));

function formatPct(value: number): string {
  return `${Math.round(value * 10) / 10}%`;
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
  const [smoothPct, setSmoothPct] = useState(0);
  const targetRef = useRef(0);
  const rafRef = useRef<number | null>(null);

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
        } else {
          setBacktestPct((prev) => (prev === null ? pct : Math.max(prev, pct)));
        }
      } catch {
        // ignore
      }
    };
    return () => es.close();
  }, [jobType, url]);

  const dataFetchDone = (dataFetchPct ?? 0) >= 100 || (backtestPct !== null && dataFetchPct === null) || finished;
  const targetPct = useMemo(() => {
    if (finished) return 100;
    if (dataFetchDone) {
      return clampPct(50 + (50 * (backtestPct ?? 0)) / 100);
    }
    return clampPct((50 * (dataFetchPct ?? 0)) / 100);
  }, [finished, dataFetchDone, dataFetchPct, backtestPct]);

  useEffect(() => {
    targetRef.current = targetPct;
  }, [targetPct]);

  useEffect(() => {
    const animate = () => {
      const target = targetRef.current;
      setSmoothPct((prev) => {
        const diff = target - prev;
        if (Math.abs(diff) < SNAP_THRESHOLD) return target;
        return prev + diff * LERP_FACTOR;
      });
      rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  if (jobType !== "BACKTEST") return null;

  if (finished && dataFetchPct === null && backtestPct === null) {
    return null;
  }

  const displayPct = finished ? 100 : smoothPct;

  return (
    <section className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
      <div className="flex items-center justify-between text-xs text-[#868993]">
        <span>Progress</span>
        <span>
          {connected ? "live" : "offline"}
          {error ? ` • ${error}` : ""}
        </span>
      </div>

      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between text-xs text-[#d1d4dc]">
          <span>Progress</span>
          <span className="text-[#868993]">{formatPct(displayPct)}</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-[#0f141f]">
          <div
            className="h-full rounded-full bg-[#2962ff]"
            style={{ width: `${displayPct}%` }}
          />
        </div>
      </div>
    </section>
  );
}
