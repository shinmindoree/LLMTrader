"use client";

import { useEffect, useState } from "react";
import type { FocusEvent, MouseEvent } from "react";

import { InfoTooltip } from "@/components/InfoTooltip";
import { createJob, listFuturesSymbols, preflightJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
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
  onSubmittingChange,
  onClose,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
  onSubmittingChange?: (submitting: boolean) => void;
  onClose?: () => void;
}) {
  const defaults = loadExecutionDefaults();
  const { t } = useI18n();
  const [strategyPath, setStrategyPath] = useState(strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(defaults.symbol);
  const [interval, setInterval] = useState(defaults.interval);
  const [leverage, setLeverage] = useState(1);
  const [initialBalance, setInitialBalance] = useState(1000);
  const [commission, setCommission] = useState(0.0004);
  const [stopLossPct, setStopLossPct] = useState(0.05);
  const [maxPyramidEntries, setMaxPyramidEntries] = useState(0);
  const now = new Date();
  const [startDate, setStartDate] = useState(() => formatDateInputValue(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)));
  const [endDate, setEndDate] = useState(() => formatDateInputValue(now));
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [futuresSymbols, setFuturesSymbols] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    listFuturesSymbols()
      .then((items) => {
        if (cancelled) return;
        setFuturesSymbols(items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const openDatePicker = (event: FocusEvent<HTMLInputElement> | MouseEvent<HTMLInputElement>) => {
    const input = event.currentTarget as HTMLInputElement & { showPicker?: () => void };
    if (typeof input.showPicker === "function") {
      input.showPicker();
    }
  };

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    onSubmittingChange?.(true);
    try {
      const startParts = parseDateInputValue(startDate);
      const endParts = parseDateInputValue(endDate);
      if (!startParts || !endParts) {
        throw new Error(t.form.invalidDateRange);
      }
      const startTs = new Date(startParts.year, startParts.month - 1, startParts.day, 0, 0, 0, 0).getTime();
      const endTs = new Date(endParts.year, endParts.month - 1, endParts.day, 23, 59, 59, 999).getTime();
      if (Number.isNaN(startTs) || Number.isNaN(endTs)) {
        throw new Error(t.form.invalidDate);
      }
      if (startTs > endTs) {
        throw new Error(t.form.startBeforeEnd);
      }
      const config = {
        symbol,
        interval,
        leverage,
        initial_balance: initialBalance,
        commission,
        stop_loss_pct: stopLossPct,
        max_pyramid_entries: maxPyramidEntries,
        start_ts: startTs,
        end_ts: endTs,
      };

      const preflight = await preflightJob({
        type: "BACKTEST",
        config,
      });
      if (!preflight.ok) {
        const msg = formatPolicyMessages(t.form.runBlocked, preflight.blockers);
        setError(msg);
        return;
      }
      if (preflight.warnings.length > 0) {
        const proceed = window.confirm(
          formatPolicyMessages(t.form.warningsDetected, preflight.warnings),
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
    } finally {
      setSubmitting(false);
      onSubmittingChange?.(false);
    }
  };

  return (
    <div>
      {error ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}
      <div className="mb-4 flex flex-wrap gap-2">
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc]">{symbol}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{interval}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{leverage}x</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{startDate} → {endDate}</span>
      </div>
      <p className="mb-4 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        {t.form.formNotice}
      </p>
      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          {t.form.defaultsApplied}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">{t.form.strategy}</div>
          <select
            id="strategy"
            name="strategy"
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
          <div className="mb-1 text-xs text-[#868993]">{t.form.symbol}</div>
          <input
            id="symbol"
            name="symbol"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            list="futures-symbol-options"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase().replace(/\s+/g, ""))}
            onBlur={(e) => setSymbol(e.target.value.trim().toUpperCase())}
          />
          <datalist id="futures-symbol-options">
            {futuresSymbols.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">{t.form.interval}</div>
          <select
            id="interval"
            name="interval"
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
          <div className="mb-1 text-xs text-[#868993]">{t.form.startDate}</div>
          <input
            id="start-date"
            name="start-date"
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
          <div className="mb-1 text-xs text-[#868993]">{t.form.endDate}</div>
          <input
            id="end-date"
            name="end-date"
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
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.leverage}<InfoTooltip text={t.form.tooltipLeverage} /></></div>
          <input
            id="leverage"
            name="leverage"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={leverage}
            min={1}
            max={20}
            onChange={(e) => setLeverage(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.initialBalance}<InfoTooltip text={t.form.tooltipInitialBalance} /></></div>
          <input
            id="initial-balance"
            name="initial-balance"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={initialBalance}
            min={100}
            onChange={(e) => setInitialBalance(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.commission}<InfoTooltip text={t.form.tooltipCommission} /></></div>
          <input
            id="commission"
            name="commission"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.0001"
            value={commission}
            onChange={(e) => setCommission(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.stopLoss}<InfoTooltip text={t.form.tooltipStopLoss} /></></div>
          <input
            id="stop-loss"
            name="stop-loss"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.1"
            value={stopLossPct * 100}
            onChange={(e) => setStopLossPct(Number(e.target.value) / 100)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.maxPyramid}<InfoTooltip text={t.form.tooltipPyramid} /></></div>
          <input
            id="max-pyramid"
            name="max-pyramid"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={maxPyramidEntries}
            min={0}
            max={10}
            onChange={(e) => setMaxPyramidEntries(Number(e.target.value))}
          />
        </label>
      </div>

      <div className="mt-5 flex justify-end gap-3">
        {onClose && (
          <button
            type="button"
            className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-2 text-sm text-[#868993] hover:text-[#d1d4dc] hover:border-[#d1d4dc] transition-colors"
            onClick={onClose}
            disabled={submitting}
          >
            {t.common.cancel}
          </button>
        )}
        <button
          className="rounded bg-[#2962ff] px-4 py-2 text-sm text-white hover:bg-[#1e53d5] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          onClick={onSubmit}
          disabled={submitting}
        >
          {t.backtest.startBacktest}
        </button>
      </div>
    </div>
  );
}
