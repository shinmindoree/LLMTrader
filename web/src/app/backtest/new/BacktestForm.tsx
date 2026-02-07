"use client";

import { useState } from "react";
import type { FocusEvent, MouseEvent } from "react";

import { createJob, preflightJob } from "@/lib/api";
import type { Job, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const BACKTEST_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function loadExecutionDefaults(): { symbol: string; interval: string; applied: boolean } {
  if (typeof window === "undefined") {
    return { symbol: "BTCUSDT", interval: "1h", applied: false };
  }
  try {
    const raw = window.localStorage.getItem(EXECUTION_DEFAULTS_KEY);
    if (!raw) return { symbol: "BTCUSDT", interval: "1h", applied: false };
    const data = JSON.parse(raw) as Record<string, unknown>;
    const symbol = typeof data.symbol === "string" && data.symbol.trim()
      ? data.symbol.trim().toUpperCase()
      : "BTCUSDT";
    const intervalCandidate = typeof data.interval === "string" ? data.interval.trim() : "";
    const interval = BACKTEST_INTERVALS.includes(intervalCandidate as (typeof BACKTEST_INTERVALS)[number])
      ? intervalCandidate
      : "1h";
    return { symbol, interval, applied: true };
  } catch {
    return { symbol: "BTCUSDT", interval: "1h", applied: false };
  }
}

function formatDateInputValue(date: Date): string {
  const yyyy = String(date.getFullYear());
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function parseDateInputValue(value: string): { year: number; month: number; day: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  return { year, month, day };
}

function formatPolicyMessages(title: string, items: string[]): string {
  if (items.length === 0) return title;
  return `${title}\n${items.map((item, idx) => `${idx + 1}. ${item}`).join("\n")}`;
}

export function BacktestForm({
  strategies,
  onCreated,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
}) {
  const defaults = loadExecutionDefaults();
  const [strategyPath, setStrategyPath] = useState(strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(defaults.symbol);
  const [interval, setInterval] = useState(defaults.interval);
  const [leverage, setLeverage] = useState(1);
  const [initialBalance, setInitialBalance] = useState(1000);
  const [commission, setCommission] = useState(0.0004);
  const [stopLossPct, setStopLossPct] = useState(0.05);
  const now = new Date();
  const [startDate, setStartDate] = useState(() => formatDateInputValue(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)));
  const [endDate, setEndDate] = useState(() => formatDateInputValue(now));
  const [error, setError] = useState<string | null>(null);

  const openDatePicker = (event: FocusEvent<HTMLInputElement> | MouseEvent<HTMLInputElement>) => {
    const input = event.currentTarget as HTMLInputElement & { showPicker?: () => void };
    if (typeof input.showPicker === "function") {
      input.showPicker();
    }
  };

  const onSubmit = async () => {
    setError(null);
    try {
      const startParts = parseDateInputValue(startDate);
      const endParts = parseDateInputValue(endDate);
      if (!startParts || !endParts) {
        throw new Error("Invalid date range: please use YYYY-MM-DD");
      }
      const startTs = new Date(startParts.year, startParts.month - 1, startParts.day, 0, 0, 0, 0).getTime();
      const endTs = new Date(endParts.year, endParts.month - 1, endParts.day, 23, 59, 59, 999).getTime();
      if (Number.isNaN(startTs) || Number.isNaN(endTs)) {
        throw new Error("Invalid date range");
      }
      if (startTs > endTs) {
        throw new Error("Start date must be on or before end date");
      }
      const config = {
        symbol,
        interval,
        leverage,
        initial_balance: initialBalance,
        commission,
        stop_loss_pct: stopLossPct,
        start_ts: startTs,
        end_ts: endTs,
      };

      const preflight = await preflightJob({
        type: "BACKTEST",
        config,
      });
      if (!preflight.ok) {
        const msg = formatPolicyMessages("Run blocked. Please update your settings.", preflight.blockers);
        setError(msg);
        return;
      }
      if (preflight.warnings.length > 0) {
        const proceed = window.confirm(
          formatPolicyMessages("Warnings detected. Do you want to continue?", preflight.warnings),
        );
        if (!proceed) {
          return;
        }
      }

      const job = await createJob({
        type: "BACKTEST",
        strategy_path: strategyPath,
        config,
      });
      if (!job?.job_id || !isUuid(job.job_id)) {
        throw new Error(`Invalid run reference returned: ${String(job?.job_id)}`);
      }
      onCreated?.(job);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-5">
      {error ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}
      <p className="mb-4 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        Even if you include trading settings in your strategy prompt (symbol/interval/leverage),
        this form's values are used for execution.
      </p>
      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          Defaults were pre-filled from your recent strategy request. Update them if needed.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Strategy</div>
          <select
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={strategyPath}
            onChange={(e) => setStrategyPath(e.target.value)}
          >
            {strategies.map((s) => (
              <option key={s.path} value={s.path} className="bg-[#131722]">
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Symbol</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Interval</div>
          <select
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          >
            {BACKTEST_INTERVALS.map((itv) => (
              <option key={itv} value={itv} className="bg-[#131722]">
                {itv}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Start Date</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="date"
            value={startDate}
            max={endDate}
            onFocus={openDatePicker}
            onClick={openDatePicker}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">End Date</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="date"
            value={endDate}
            min={startDate}
            max={formatDateInputValue(new Date())}
            onFocus={openDatePicker}
            onClick={openDatePicker}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Leverage</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={leverage}
            min={1}
            max={20}
            onChange={(e) => setLeverage(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Initial Balance (USDT)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={initialBalance}
            min={100}
            onChange={(e) => setInitialBalance(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Commission</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.0001"
            value={commission}
            onChange={(e) => setCommission(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">StopLoss (%)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.1"
            value={stopLossPct * 100}
            onChange={(e) => setStopLossPct(Number(e.target.value) / 100)}
          />
        </label>
      </div>

      <button
        className="mt-5 rounded bg-[#2962ff] px-4 py-2 text-sm text-white hover:bg-[#1e53d5] transition-colors"
        onClick={onSubmit}
      >
        Run Backtest
      </button>
    </div>
  );
}
