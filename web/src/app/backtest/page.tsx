"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listJobs, listStrategies, stopAllJobs } from "@/lib/api";
import type { Job, StrategyInfo } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LatestJobResult } from "@/components/LatestJobResult";
import { jobDetailPath } from "@/lib/routes";
import { BacktestForm } from "./new/BacktestForm";

export default function BacktestJobsPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [items, setItems] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latestJob, setLatestJob] = useState<Job | null>(null);

  const refresh = async () => {
    try {
      setError(null);
      const data = await listJobs({ type: "BACKTEST", limit: 200 });
      setItems(data);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    listStrategies()
      .then(setStrategies)
      .catch((e) => setStrategyError(String(e)));
  }, []);

  const onCreated = (job: Job) => {
    setLatestJob(job);
    setNotice("Backtest job started.");
    refresh();
  };

  const onStopAll = async () => {
    if (busy) return;
    const ok = confirm(
      "BACKTEST 잡 전체 Stop All 을 실행할까요?\n\n- PENDING 잡은 즉시 STOPPED로 처리됩니다.\n- RUNNING 잡은 STOP_REQUESTED로 변경됩니다.",
    );
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await stopAllJobs("BACKTEST");
      setNotice(
        `Stop All 완료: stopped_queued=${res.stopped_queued}, stop_requested_running=${res.stop_requested_running}`,
      );
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">Backtest</h1>
          <p className="mt-1 text-xs text-[#868993]">
            Backtest job은 Live와 별도 큐로 동작합니다.
          </p>
        </div>
        <div className="flex gap-2 text-sm">
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#252936] hover:border-[#2962ff] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={refresh}
            type="button"
          >
            Refresh
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onStopAll}
            type="button"
          >
            Stop All
          </button>
        </div>
      </div>

      {notice ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 text-sm text-[#d1d4dc]">
          {notice}
          {latestJob ? (
            <>
              {" "}
              <Link
                className="text-[#2962ff] hover:underline"
                href={jobDetailPath("BACKTEST", latestJob.job_id)}
              >
                Open run details
              </Link>
            </>
          ) : null}
        </div>
      ) : null}

      {strategyError ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {strategyError}
        </p>
      ) : null}

      <section className="mt-6">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">New Backtest</div>
        <p className="mb-3 text-xs text-[#868993]">DB(Postgres) 기반으로 백테스트 Job을 생성합니다.</p>
        {strategies.length ? (
          <BacktestForm strategies={strategies} onCreated={onCreated} />
        ) : (
          <div className="text-sm text-[#868993]">Loading…</div>
        )}
      </section>

      <LatestJobResult
        jobType="BACKTEST"
        focusJobId={latestJob?.job_id ?? null}
        title="Latest Backtest Result"
      />

      <section className="mt-10">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">Job List</div>
        {error ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {error}
          </p>
        ) : null}

        {items.length === 0 && !error ? (
          <div className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
            No backtest jobs found. Create a new backtest to get started.
          </div>
        ) : (
          <ul className="space-y-2">
            {items.map((j) => (
              <li
                key={j.job_id}
                className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
              >
                <div className="flex items-center justify-between">
                  <Link
                    className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
                    href={jobDetailPath("BACKTEST", j.job_id)}
                  >
                    {j.strategy_path}
                  </Link>
                  <JobStatusBadge status={j.status} />
                </div>
                <div className="mt-1 text-xs text-[#868993]">
                  {new Date(j.created_at).toLocaleString()}
                  {j.error ? ` • error: ${j.error}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
