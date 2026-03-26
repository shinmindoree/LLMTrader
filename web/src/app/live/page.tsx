"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import useSWR, { useSWRConfig } from "swr";
import { deleteAllJobs, deleteJob, getBillingStatus, getBinanceKeysStatus, listJobSummaries, listStrategies, stopAllJobs, stopJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { BillingStatus, BinanceKeysStatus, Job, JobStatus, JobSummary, StrategyInfo } from "@/lib/types";
import { ActiveJobCard } from "@/components/ActiveJobCard";
import { RunHistoryTable } from "@/components/RunHistoryTable";
import { jobDetailPath } from "@/lib/routes";
import { InlineLoadingIndicator } from "@/components/InlineLoadingIndicator";
import { LiveForm } from "./new/LiveForm";

const MAX_SLOTS_FALLBACK = 5;
const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

export default function LiveJobsPage() {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const { mutate } = useSWRConfig();
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latestJob, setLatestJob] = useState<JobSummary | null>(null);

  const { data: strategies = [], error: strategyError } = useSWR<StrategyInfo[]>(
    "strategies",
    () => listStrategies(),
  );

  const { data: keysStatus } = useSWR<BinanceKeysStatus>(
    "binanceKeysStatus",
    () => getBinanceKeysStatus(),
  );

  const { data: billing } = useSWR<BillingStatus>(
    "billingStatus",
    () => getBillingStatus(),
  );

  const hasActiveItems = (data: JobSummary[]) => data.some((j) => ACTIVE_STATUSES.has(j.status));

  const { data: items = [], error, mutate: refreshItems } = useSWR<JobSummary[]>(
    ["jobSummaries", "LIVE"],
    () => listJobSummaries({ type: "LIVE", limit: 50 }),
    {
      refreshInterval: (latestData: JobSummary[] | undefined) => {
        if (!latestData || !hasActiveItems(latestData)) return 0;
        return isVisible ? 5_000 : 15_000;
      },
      dedupingInterval: 2_000,
    },
  );

  const refresh = useCallback(() => refreshItems(), [refreshItems]);

  const activeJobs = useMemo(() => items.filter((j) => ACTIVE_STATUSES.has(j.status)), [items]);
  const activeCount = activeJobs.length;
  const maxSlots = billing?.limits?.max_live_jobs ?? MAX_SLOTS_FALLBACK;

  const onCreated = (job: Job) => {
    setLatestJob({
      job_id: job.job_id,
      type: job.type,
      status: job.status,
      strategy_path: job.strategy_path,
      config: job.config,
      result_summary: job.result,
      error: job.error,
      created_at: job.created_at,
      started_at: job.started_at,
      ended_at: job.ended_at,
    });
    setNotice(t.live.runStarted);
    refreshItems();
    void mutate((key: unknown) => Array.isArray(key) && key[0] === "latestJob");
  };

  const onStopJob = async (job: Job | JobSummary) => {
    if (busy) return;
    try {
      setBusy(true);
      setActionError(null);
      await stopJob(job.job_id);
      setNotice(t.live.stopRequested);
      await refreshItems();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteJob = async (job: Job | JobSummary) => {
    if (busy) return;
    if (!FINISHED_STATUSES.has(job.status)) {
      setActionError(t.live.onlyFinishedDelete);
      return;
    }
    const ok = confirm(t.live.deleteConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setActionError(null);
      setNotice(null);
      await deleteJob(job.job_id);
      setLatestJob((prev) => (prev?.job_id === job.job_id ? null : prev));
      setNotice(t.live.runDeleted);
      await refreshItems();
      void mutate((key: unknown) => Array.isArray(key) && key[0] === "latestJob");
    } catch (e) {
      setActionError(String(e));
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
      setActionError(null);
      setNotice(null);
      const res = await deleteAllJobs("LIVE");
      setLatestJob((prev) =>
        prev && FINISHED_STATUSES.has(prev.status) ? null : prev,
      );
      setNotice(`Done: deleted=${res.deleted}, skipped_active=${res.skipped_active}`);
      await refreshItems();
      void mutate((key: unknown) => Array.isArray(key) && key[0] === "latestJob");
    } catch (e) {
      setActionError(String(e));
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
      setActionError(null);
      setNotice(null);
      const res = await stopAllJobs("LIVE");
      setNotice(
        `Stop requested: stopped_queued=${res.stopped_queued}, stop_requested_running=${res.stop_requested_running}`,
      );
      await refreshItems();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const keysNotConfigured = keysStatus != null && !keysStatus.configured;

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
              <ActiveJobCard key={j.job_id} job={j} busy={busy} onStop={onStopJob} />
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
          {String(strategyError)}
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
            activeCount={activeCount}
            maxSlots={maxSlots}
          />
        ) : (
          <InlineLoadingIndicator message={t.common.loading} />
        )}
      </section>

      <section className="mt-10">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">{t.live.runHistory}</div>
        {(actionError || error) ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {actionError || String(error)}
          </p>
        ) : null}

        {items.filter((j) => !ACTIVE_STATUSES.has(j.status)).length === 0 && !error && !actionError ? (
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
