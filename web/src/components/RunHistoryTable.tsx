"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useI18n } from "@/lib/i18n";
import type { Job, JobSummary, JobStatus, JobType } from "@/lib/types";

type AnyJob = Job | JobSummary;
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { jobDetailPath } from "@/lib/routes";
import { isRecord } from "@/components/JobResultSummary";

const asNumber = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const asString = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v : null;

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

function pnlsFromTrades(trades: unknown): number[] {
  if (!Array.isArray(trades)) return [];
  return trades
    .map((t) => (isRecord(t) ? asNumber(t.pnl) : null))
    .filter((p): p is number => p !== null && p !== undefined);
}

function computeWinRate(pnls: number[]): number | null {
  if (pnls.length === 0) return null;
  const wins = pnls.filter((p) => p > 0).length;
  return (wins / pnls.length) * 100;
}

export type RunHistoryRow = {
  job: AnyJob;
  date: string;
  dateMs: number;
  startedAt: string;
  endedAt: string;
  strategy: string;
  symbol: string;
  interval: string;
  status: JobStatus;
  totalTrades: number | null;
  netProfit: number | null;
  returnPct: number | null;
  winRate: number | null;
  configLabel: string;
};

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

function extractSymbol(type: JobType, config: Record<string, unknown>): string {
  if (type === "BACKTEST") {
    return asString(config.symbol) ?? "-";
  }
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const first = streams[0];
  if (isRecord(first)) return asString(first.symbol) ?? "-";
  return "-";
}

function extractInterval(type: JobType, config: Record<string, unknown>): string {
  if (type === "BACKTEST") {
    return asString(config.interval) ?? "-";
  }
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const first = streams[0];
  if (isRecord(first)) return asString(first.interval) ?? "-";
  return "-";
}

function getResult(job: AnyJob): Record<string, unknown> | null {
  if ("result" in job) return job.result;
  if ("result_summary" in job) return job.result_summary;
  return null;
}

function buildRow(job: AnyJob, type: JobType): RunHistoryRow {
  const config = (job.config ?? {}) as Record<string, unknown>;
  const result = getResult(job);
  const isResultRecord = result && isRecord(result);

  let totalTrades: number | null = null;
  let netProfit: number | null = null;
  let returnPct: number | null = null;
  let winRate: number | null = null;

  if (type === "BACKTEST" && isResultRecord) {
    const r = result as Record<string, unknown>;
    const initial = asNumber(r.initial_balance);
    const final = asNumber(r.final_balance);
    totalTrades = asNumber(r.total_trades) ?? (Array.isArray(r.trades) ? r.trades.filter((t: unknown) => isRecord(t) && (t as Record<string, unknown>).side === "SELL").length : null);
    netProfit = asNumber(r.net_profit) ?? (initial !== null && final !== null ? final - initial : null);
    returnPct = asNumber(r.total_return_pct) ?? (initial != null && initial > 0 && final !== null ? ((final - initial) / initial) * 100 : null);
    const pnls = pnlsFromTrades(r.trades);
    winRate = computeWinRate(pnls);
  } else if (type === "LIVE" && isResultRecord) {
    const r = result as Record<string, unknown>;
    const summary = isRecord(r.summary) ? (r.summary as Record<string, unknown>) : r;
    const initial = asNumber(summary.initial_equity) ?? asNumber(summary.initial_balance);
    const final_ = asNumber(summary.final_equity) ?? asNumber(summary.final_balance);
    totalTrades = asNumber(summary.num_trades) ?? asNumber(summary.total_trades);
    netProfit = asNumber(summary.net_profit) ?? (initial !== null && final_ !== null ? final_ - initial : null);
    returnPct = asNumber(summary.total_return_pct) ?? (initial != null && initial > 0 && final_ !== null ? ((final_ - initial) / initial) * 100 : null);
    winRate = asNumber(summary.win_rate);
  }

  const createdAt = job.created_at ? new Date(job.created_at).getTime() : 0;

  return {
    job,
    date: job.created_at ? new Date(job.created_at).toLocaleString() : "-",
    dateMs: createdAt,
    startedAt: job.started_at ? new Date(job.started_at).toLocaleString() : "-",
    endedAt: job.ended_at ? new Date(job.ended_at).toLocaleString() : "-",
    strategy: strategyNameFromPath(job.strategy_path),
    symbol: extractSymbol(type, config),
    interval: extractInterval(type, config),
    status: job.status,
    totalTrades,
    netProfit,
    returnPct,
    winRate,
    configLabel: "",
  };
}

type SortKey = "date" | "strategy" | "symbol" | "interval" | "status" | "totalTrades" | "netProfit" | "returnPct" | "winRate";
type SortOrder = "asc" | "desc";

function SortIcon({ active, order }: { active: boolean; order: SortOrder }) {
  if (!active) {
    return <span className="ml-1 inline-block text-[#868993] opacity-50">↕</span>;
  }
  return <span className="ml-1 text-[#2962ff]">{order === "asc" ? "↑" : "↓"}</span>;
}

export function RunHistoryTable({
  items,
  type,
  onDeleteJob,
  busy,
  canDeleteJob,
}: {
  items: AnyJob[];
  type: JobType;
  onDeleteJob: (job: AnyJob) => void;
  busy: boolean;
  canDeleteJob?: (job: AnyJob) => boolean;
}) {
  const { t } = useI18n();
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");

  const rows = useMemo(() => items.map((j) => buildRow(j, type)), [items, type]);

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "date":
          cmp = a.dateMs - b.dateMs;
          break;
        case "strategy":
          cmp = a.strategy.localeCompare(b.strategy);
          break;
        case "symbol":
          cmp = a.symbol.localeCompare(b.symbol);
          break;
        case "interval":
          cmp = a.interval.localeCompare(b.interval);
          break;
        case "status":
          cmp = String(a.status).localeCompare(String(b.status));
          break;
        case "totalTrades": {
          const va = a.totalTrades ?? -1;
          const vb = b.totalTrades ?? -1;
          cmp = va - vb;
          break;
        }
        case "netProfit": {
          const va = a.netProfit ?? -Infinity;
          const vb = b.netProfit ?? -Infinity;
          cmp = va - vb;
          break;
        }
        case "returnPct": {
          const va = a.returnPct ?? -Infinity;
          const vb = b.returnPct ?? -Infinity;
          cmp = va - vb;
          break;
        }
        case "winRate": {
          const va = a.winRate ?? -1;
          const vb = b.winRate ?? -1;
          cmp = va - vb;
          break;
        }
        default:
          cmp = 0;
      }
      if (cmp === 0) cmp = a.dateMs - b.dateMs;
      return sortOrder === "asc" ? cmp : -cmp;
    });
  }, [rows, sortKey, sortOrder]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortOrder("asc");
    }
  };

  const thClass = "px-3 py-2.5 text-left text-xs font-medium text-[#868993] cursor-pointer hover:text-[#d1d4dc] select-none whitespace-nowrap";
  const tdClass = "px-3 py-2.5 text-xs text-[#d1d4dc]";

  return (
    <div className="overflow-x-auto rounded border border-[#2a2e39] bg-[#1e222d]">
      <table className="w-full min-w-[900px]">
        <thead>
          <tr className="border-b border-[#2a2e39] bg-[#131722]">
            <th className={thClass} onClick={() => handleSort("date")}>
              {t.runHistory.startedAt}
              <SortIcon active={sortKey === "date"} order={sortOrder} />
            </th>
            <th className="px-3 py-2.5 text-left text-xs font-medium text-[#868993] whitespace-nowrap">
              {t.runHistory.endedAt}
            </th>
            <th className={thClass} onClick={() => handleSort("strategy")}>
              {t.runHistory.strategy}
              <SortIcon active={sortKey === "strategy"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("symbol")}>
              {t.runHistory.symbol}
              <SortIcon active={sortKey === "symbol"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("interval")}>
              {t.runHistory.interval}
              <SortIcon active={sortKey === "interval"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("status")}>
              {t.runHistory.status}
              <SortIcon active={sortKey === "status"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("totalTrades")}>
              {t.runHistory.totalTrades}
              <SortIcon active={sortKey === "totalTrades"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("netProfit")}>
              {t.runHistory.netProfit}
              <SortIcon active={sortKey === "netProfit"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("returnPct")}>
              {t.runHistory.returnPct}
              <SortIcon active={sortKey === "returnPct"} order={sortOrder} />
            </th>
            <th className={thClass} onClick={() => handleSort("winRate")}>
              {t.runHistory.winRate}
              <SortIcon active={sortKey === "winRate"} order={sortOrder} />
            </th>
            <th className="px-3 py-2.5 text-left text-xs font-medium text-[#868993] whitespace-nowrap">
              {t.runHistory.config}
            </th>
            <th className="px-3 py-2.5 text-right text-xs font-medium text-[#868993] w-20">
              {t.runHistory.actions}
            </th>
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => (
            <tr
              key={row.job.job_id}
              className="border-b border-[#2a2e39] hover:bg-[#252a37] transition-colors"
            >
              <td className={tdClass}>{row.startedAt}</td>
              <td className={`${tdClass} text-[#868993]`}>{row.endedAt}</td>
              <td className={tdClass}>
                <Link
                  className="font-medium text-[#2962ff] hover:underline"
                  href={jobDetailPath(type, row.job.job_id)}
                >
                  {row.strategy}
                </Link>
              </td>
              <td className={tdClass}>{row.symbol}</td>
              <td className={tdClass}>{row.interval}</td>
              <td className={tdClass}>
                <JobStatusBadge status={row.status} />
              </td>
              <td className={tdClass}>
                {row.totalTrades !== null ? formatNumber(row.totalTrades, 0) : "-"}
              </td>
              <td
                className={`${tdClass} font-medium ${
                  row.netProfit !== null
                    ? row.netProfit >= 0
                      ? "text-[#26a69a]"
                      : "text-[#ef5350]"
                    : ""
                }`}
              >
                {row.netProfit !== null ? formatSigned(row.netProfit, "USDT") : "-"}
              </td>
              <td
                className={`${tdClass} font-medium ${
                  row.returnPct !== null
                    ? row.returnPct >= 0
                      ? "text-[#26a69a]"
                      : "text-[#ef5350]"
                    : ""
                }`}
              >
                {row.returnPct !== null ? `${formatNumber(row.returnPct)}%` : "-"}
              </td>
              <td className={tdClass}>
                {row.winRate !== null ? `${formatNumber(row.winRate, 1)}%` : "-"}
              </td>
              <td className={`${tdClass} max-w-[200px] truncate text-[#868993]`}>
                {row.job.config ? (
                  <JobConfigInline type={type} config={row.job.config} />
                ) : (
                  "-"
                )}
              </td>
              <td className="px-3 py-2.5 text-right">
                <button
                  className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
                  disabled={busy || (canDeleteJob ? !canDeleteJob(row.job) : false)}
                  onClick={() => onDeleteJob(row.job)}
                  type="button"
                >
                  {t.common.delete}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
