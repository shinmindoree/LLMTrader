"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteAllJobs, deleteJob, getBillingStatus, getBinanceKeysStatus, listJobs, listStrategies, stopAllJobs, stopJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { BillingStatus, BinanceKeysStatus, Job, JobStatus, StrategyInfo } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LatestJobResult } from "@/components/LatestJobResult";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { RunHistoryTable } from "@/components/RunHistoryTable";
import { jobDetailPath } from "@/lib/routes";
import { LiveForm } from "./new/LiveForm";

const MAX_SLOTS_FALLBACK = 5;
const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

export default function LiveJobsPage() {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [items, setItems] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latestJob, setLatestJob] = useState<Job | null>(null);
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [runPending, setRunPending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const data = await listJobs({ type: "LIVE", limit: 200 });
      setItems(data);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    getBinanceKeysStatus().then(setKeysStatus).catch(() => {});
    getBillingStatus().then(setBilling).catch(() => {});
  }, [refresh]);

  const activeJobs = useMemo(() => items.filter((j) => ACTIVE_STATUSES.has(j.status)), [items]);
  const activeCount = activeJobs.length;
  const maxSlots = billing?.limits?.max_live_jobs ?? MAX_SLOTS_FALLBACK;

  useEffect(() => {
    if (activeCount === 0) return;
    const ms = isVisible ? 2_500 : 12_000;
    const interval = setInterval(refresh, ms);
    return () => clearInterval(interval);
  }, [activeCount, refresh, isVisible]);

  useEffect(() => {
    listStrategies()
      .then(setStrategies)
      .catch((e) => setStrategyError(String(e)));
  }, []);

  const onCreated = (job: Job) => {
    setLatestJob(job);
    setNotice(t.live.runStarted);
    refresh();
  };

  const onStopJob = async (job: Job) => {
    if (busy) return;
    try {
      setBusy(true);
      setError(null);
      await stopJob(job.job_id);
      setNotice(t.live.stopRequested);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteJob = async (job: Job) => {
    if (busy) return;
    if (!FINISHED_STATUSES.has(job.status)) {
      setError(t.live.onlyFinishedDelete);
      return;
    }
    const ok = confirm(t.live.deleteConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      await deleteJob(job.job_id);
      setLatestJob((prev) => (prev?.job_id === job.job_id ? null : prev));
      setNotice(t.live.runDeleted);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteAll = async () => {
    if (busy) return;
    const ok = confirm(t.live.deleteAllConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await deleteAllJobs("LIVE");
      setLatestJob((prev) =>
        prev && FINISHED_STATUSES.has(prev.status) ? null : prev,
      );
      setNotice(`Done: deleted=${res.deleted}, skipped_active=${res.skipped_active}`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onStopAll = async () => {
    if (busy) return;
    const ok = confirm(t.live.stopAllConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await stopAllJobs("LIVE");
      setNotice(
        `Stop requested: stopped_queued=${res.stopped_queued}, stop_requested_running=${res.stop_requested_running}`,
      );
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const keysNotConfigured = keysStatus !== null && !keysStatus.configured;

  return (
    <main className="w-full px-4 py-3">
      {keysNotConfigured && (
        <div className="mb-3 rounded-lg border border-[#efb74d]/40 bg-[#2d2718] px-4 py-2.5 text-sm text-[#efb74d]">
          {t.live.keysNotConfigured}{" "}
          <a className="underline hover:text-[#d1d4dc] transition-colors" href="/settings">
            {t.live.goToSettings}
          </a>
        </div>
      )}
      <div className="flex items-center justify-between">
        <p className="text-xs text-[#868993]">
          {t.live.slotsInfo.replace("{maxSlots}", String(maxSlots))}
        </p>
        <div className="flex gap-2 text-sm">
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#252936] hover:border-[#2962ff] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={refresh}
            type="button"
          >
            {t.common.refresh}
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onDeleteAll}
            type="button"
          >
            {t.common.deleteAll}
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onStopAll}
            type="button"
          >
            {t.common.stopAll}
          </button>
        </div>
      </div>

      {activeCount > 0 && (
        <section className="mt-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-[#d1d4dc]">
            {t.live.activeStrategies}
            <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-xs text-[#2962ff]">
              {activeCount}/{maxSlots}
            </span>
          </div>
          <ul className="space-y-2">
            {activeJobs.map((j) => (
              <li
                key={j.job_id}
                className="rounded border border-[#2962ff]/30 bg-[#1a2340]/50 px-4 py-3"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Link
                      className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
                      href={jobDetailPath("LIVE", j.job_id)}
                    >
                      {strategyNameFromPath(j.strategy_path)}
                    </Link>
                    {j.config ? (
                      <span className="text-xs"><JobConfigInline type="LIVE" config={j.config} /></span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <JobStatusBadge status={j.status} />
                    {(j.status === "PENDING" || j.status === "RUNNING") && (
                      <button
                        className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
                        disabled={busy}
                        onClick={() => onStopJob(j)}
                        type="button"
                      >
                        {t.common.stop}
                      </button>
                    )}
                  </div>
                </div>
                <div className="mt-1 text-xs text-[#868993]">
                  {t.live.started} {new Date(j.created_at).toLocaleString()}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {notice ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 text-sm text-[#d1d4dc]">
          {notice}
          {latestJob ? (
            <>
              {" "}
              <Link
                className="text-[#2962ff] hover:underline"
                href={jobDetailPath("LIVE", latestJob.job_id)}
              >
                {t.live.openRunDetails}
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
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#d1d4dc]">
          {t.live.addStrategy}
          {activeCount > 0 && (
            <span className="text-xs font-normal text-[#868993]">
              {t.live.slotsUsed.replace("{activeCount}", String(activeCount)).replace("{maxSlots}", String(maxSlots))}
            </span>
          )}
        </div>
        <p className="mb-3 text-xs text-[#efb6b2]">
          {t.live.strategyIndependentInfo.replace("{maxSlots}", String(maxSlots))}
        </p>
        {strategies.length ? (
          <LiveForm
            strategies={strategies}
            onCreated={onCreated}
            onSubmittingChange={setRunPending}
            activeCount={activeCount}
            maxSlots={maxSlots}
          />
        ) : (
          <div className="text-sm text-[#868993]">{t.common.loading}</div>
        )}
      </section>

      <LatestJobResult
        jobType="LIVE"
        focusJobId={latestJob?.job_id ?? null}
        title={t.live.latestResult}
        showPendingSpinner={runPending}
      />

      <section className="mt-10">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">{t.live.runHistory}</div>
        {error ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {error}
          </p>
        ) : null}

        {items.filter((j) => !ACTIVE_STATUSES.has(j.status)).length === 0 && !error ? (
          <div className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
            {t.live.emptyState}
          </div>
        ) : (
          <RunHistoryTable
            items={items.filter((j) => !ACTIVE_STATUSES.has(j.status))}
            type="LIVE"
            onDeleteJob={onDeleteJob}
            busy={busy}
            canDeleteJob={(j) => FINISHED_STATUSES.has(j.status)}
          />
        )}
      </section>
    </main>
  );
}
