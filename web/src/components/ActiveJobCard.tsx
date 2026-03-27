"use client";

import Link from "next/link";
import { useMemo } from "react";

import useSWR from "swr";
import { getBinanceAccountSummary, listTrades } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { BinanceAccountSummary, BinancePositionSummary, JobSummary, JobStatus, Trade } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { jobDetailPath } from "@/lib/routes";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function extractSymbols(config: Record<string, unknown>): string[] {
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const syms: string[] = [];
  for (const raw of streams) {
    if (!isRecord(raw)) continue;
    const sym = typeof raw.symbol === "string" ? raw.symbol.trim().toUpperCase() : "";
    if (sym) syms.push(sym);
  }
  if (syms.length === 0 && typeof config.symbol === "string") {
    const s = config.symbol.trim().toUpperCase();
    if (s) syms.push(s);
  }
  return syms;
}

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

function PositionRow({ position }: { position: BinancePositionSummary }) {
  const pnlColor = position.unrealized_pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
  const sideColor = position.side === "LONG" ? "text-[#26a69a]" : "text-[#ef5350]";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs">
      <span className="text-[#d1d4dc] font-medium">{position.symbol}</span>
      <span className={sideColor}>{position.side}</span>
      <span className="text-[#868993]">
        Qty <span className="text-[#d1d4dc]">{formatNumber(Math.abs(position.position_amt), 5)}</span>
      </span>
      <span className="text-[#868993]">
        Entry <span className="text-[#d1d4dc]">{formatNumber(position.entry_price, 2)}</span>
      </span>
      <span className="text-[#868993]">
        Notional <span className="text-[#d1d4dc]">{formatNumber(Math.abs(position.notional), 2)}</span>
      </span>
      <span className="text-[#868993]">
        Lev <span className="text-[#d1d4dc]">{position.leverage}x</span>
      </span>
      <span className="text-[#868993]">
        uPnL <span className={pnlColor}>{formatSigned(position.unrealized_pnl, "USDT")}</span>
      </span>
    </div>
  );
}

export function ActiveJobCard({
  job,
  busy,
  onStop,
}: {
  job: JobSummary;
  busy: boolean;
  onStop: (job: JobSummary) => void;
}) {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const isActive = !FINISHED_STATUSES.has(job.status);

  const { data: trades = [] } = useSWR<Trade[]>(
    isActive ? ["trades", job.job_id] : null,
    () => listTrades(job.job_id),
    {
      refreshInterval: isVisible ? 10_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const { data: snapshot } = useSWR<BinanceAccountSummary>(
    isActive ? "binanceAccountSummary" : null,
    () => getBinanceAccountSummary(),
    {
      refreshInterval: isVisible ? 15_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const symbols = useMemo(
    () => (job.config ? extractSymbols(job.config) : []),
    [job.config],
  );

  const positions = snapshot?.positions;
  const matchedPositions = useMemo(() => {
    if (!positions || symbols.length === 0) return [];
    return positions.filter((p) => symbols.includes(p.symbol.toUpperCase()));
  }, [positions, symbols]);

  const netPnl = trades.length > 0
    ? trades.reduce((s, tr) => s + (tr.realized_pnl ?? 0), 0)
    : null;
  const closedPnls = trades
    .map((tr) => tr.realized_pnl)
    .filter((p): p is number => p !== null && p !== undefined && Number.isFinite(p) && p !== 0);
  const winCount = closedPnls.filter((p) => p > 0).length;
  const totalClosed = closedPnls.length;
  const winRate = totalClosed > 0 ? (winCount / totalClosed) * 100 : null;

  return (
    <li className="rounded-lg border border-[#2962ff]/30 bg-[#1a2340]/50 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
            href={jobDetailPath("LIVE", job.job_id)}
          >
            {strategyNameFromPath(job.strategy_path)}
          </Link>
          {job.config ? (
            <span className="text-xs"><JobConfigInline type="LIVE" config={job.config} /></span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <JobStatusBadge status={job.status} />
          {(job.status === "PENDING" || job.status === "RUNNING") && (
            <button
              className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
              disabled={busy}
              onClick={() => onStop(job)}
              type="button"
            >
              {t.common.stop}
            </button>
          )}
        </div>
      </div>
      <div className="mt-1 text-xs text-[#868993]">
        {t.live.started} {new Date(job.created_at).toLocaleString()}
      </div>

      {trades.length > 0 ? (
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.netProfit}</div>
            <div className={`text-sm font-semibold ${netPnl !== null && netPnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
              {netPnl !== null ? formatSigned(netPnl, "USDT") : "-"}
            </div>
          </div>
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.totalTrades}</div>
            <div className="text-sm font-semibold text-[#d1d4dc]">{trades.length}</div>
          </div>
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.winRate}</div>
            <div className="text-sm font-semibold text-[#d1d4dc]">
              {winRate !== null ? `${formatNumber(winRate, 1)}%` : "-"}
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-2 text-xs text-[#868993] italic">
          {t.latestResult.runInProgress}
        </div>
      )}

      {matchedPositions.length > 0 && (
        <div className="mt-2 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
          <div className="mb-1 text-[10px] font-medium text-[#868993]">{t.live.openPosition}</div>
          <div className="space-y-1">
            {matchedPositions.map((p) => (
              <PositionRow key={`${p.symbol}-${p.side}`} position={p} />
            ))}
          </div>
        </div>
      )}
    </li>
  );
}
