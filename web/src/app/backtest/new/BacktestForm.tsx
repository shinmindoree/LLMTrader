"use client";

import { useEffect, useState } from "react";
import type { FocusEvent, MouseEvent } from "react";

import { InfoTooltip } from "@/components/InfoTooltip";
import StrategyParamsEditor from "@/components/StrategyParamsEditor";
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

const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

function estimateCandleCount(startDate: string, endDate: string, interval: string): number | null {
  const startParts = parseDateInputValue(startDate);
  const endParts = parseDateInputValue(endDate);
  if (!startParts || !endParts) return null;
  const startMs = new Date(startParts.year, startParts.month - 1, startParts.day, 0, 0, 0, 0).getTime();
  const endMs = new Date(endParts.year, endParts.month - 1, endParts.day, 23, 59, 59, 999).getTime();
  if (Number.isNaN(startMs) || Number.isNaN(endMs) || startMs > endMs) return null;
  const intervalMs = INTERVAL_MS[interval];
  if (!intervalMs) return null;
  return Math.floor((endMs - startMs + 1) / intervalMs);
}

function formatPolicyMessages(title: string, items: string[]): string {
  if (items.length === 0) return title;
  return `${title}\n${items.map((item, idx) => `${idx + 1}. ${item}`).join("\n")}`;
}

export type BacktestInitialConfig = {
  strategyPath?: string;
  symbol?: string;
  interval?: string;
  leverage?: number;
  initialBalance?: number;
  commission?: number;
  stopLossPct?: number;
  stopLossEnabled?: boolean;
  maxPyramidEntries?: number;
  startDate?: string;
  endDate?: string;
  strategyParams?: Record<string, unknown>;
};

export function BacktestForm({
  strategies,
  onCreated,
  onSubmittingChange,
  onClose,
  initialConfig,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
  onSubmittingChange?: (submitting: boolean) => void;
  onClose?: () => void;
  initialConfig?: BacktestInitialConfig;
}) {
  const defaults = loadExecutionDefaults();
  const { t } = useI18n();
  const [strategyPath, setStrategyPath] = useState(initialConfig?.strategyPath ?? strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(initialConfig?.symbol ?? defaults.symbol);
  const [interval, setInterval] = useState(initialConfig?.interval ?? defaults.interval);
  const [leverage, setLeverage] = useState<number | string>(initialConfig?.leverage ?? 1);
  const [initialBalance, setInitialBalance] = useState<number | string>(initialConfig?.initialBalance ?? 1000);
  const [commission, setCommission] = useState<number | string>(initialConfig?.commission ?? 0.0004);
  const [stopLossPct, setStopLossPct] = useState<number | string>(initialConfig?.stopLossPct ?? 0.05);
  const [stopLossEnabled, setStopLossEnabled] = useState(initialConfig?.stopLossEnabled ?? true);
  const [maxPyramidEntries, setMaxPyramidEntries] = useState<number | string>(initialConfig?.maxPyramidEntries ?? 0);
  const now = new Date();
  const [startDate, setStartDate] = useState(() => initialConfig?.startDate ?? formatDateInputValue(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)));
  const [endDate, setEndDate] = useState(() => initialConfig?.endDate ?? formatDateInputValue(now));
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>(initialConfig?.strategyParams ?? {});
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
      const config: Record<string, unknown> = {
        symbol,
        interval,
        leverage,
        initial_balance: initialBalance,
        commission,
        stop_loss_pct: stopLossEnabled ? stopLossPct : 0,
        max_pyramid_entries: maxPyramidEntries,
        start_ts: startTs,
        end_ts: endTs,
      };
      if (Object.keys(strategyParams).length > 0) {
        config.strategy_params = strategyParams;
      }

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
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc]">{symbol}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{interval}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{leverage}x</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{startDate} → {endDate}</span>
        {(() => {
          const count = estimateCandleCount(startDate, endDate, interval);
          if (count == null || count <= 0) return null;
          return (
            <span className="rounded bg-[#2962ff]/15 px-2 py-1 text-xs font-medium text-[#2962ff]">
              ~{count.toLocaleString()} {t.jobConfig.candles}
            </span>
          );
        })()}
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
            onChange={(e) => { setStrategyPath(e.target.value); setStrategyParams({}); }}
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
            onChange={(e) => setLeverage(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (leverage === "" || isNaN(Number(leverage))) setLeverage(1); else setLeverage(Math.min(20, Math.max(1, Number(leverage)))); }}
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
            onChange={(e) => setInitialBalance(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (initialBalance === "" || isNaN(Number(initialBalance))) setInitialBalance(1000); }}
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
            onChange={(e) => setCommission(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (commission === "" || isNaN(Number(commission))) setCommission(0.0004); }}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 flex items-center gap-2 text-xs text-[#868993]">
            <span><>{t.form.stopLoss}<InfoTooltip text={t.form.tooltipStopLoss} /></></span>
            <button
              type="button"
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                stopLossEnabled ? "bg-[#2962ff]" : "bg-[#363a45]"
              }`}
              onClick={() => setStopLossEnabled(!stopLossEnabled)}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
                  stopLossEnabled ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>
            <span className="text-[10px] text-[#868993]">{stopLossEnabled ? "거래설정" : "전략코드"}</span>
          </div>
          {stopLossEnabled && (
          <input
            id="stop-loss"
            name="stop-loss"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.1"
            value={stopLossPct === "" ? "" : Number(stopLossPct) * 100}
            onChange={(e) => setStopLossPct(e.target.value === "" ? "" : Number(e.target.value) / 100)}
            onBlur={() => { if (stopLossPct === "" || isNaN(Number(stopLossPct))) setStopLossPct(0.05); }}
          />
          )}
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
            onChange={(e) => setMaxPyramidEntries(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (maxPyramidEntries === "" || isNaN(Number(maxPyramidEntries))) setMaxPyramidEntries(0); }}
          />
        </label>
      </div>

      {/* Strategy Parameters */}
      {strategyPath && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-medium text-[#868993]">전략 파라미터</p>
          <StrategyParamsEditor
            strategyPath={strategyPath}
            values={strategyParams}
            onChange={setStrategyParams}
            disabled={submitting}
          />
        </div>
      )}

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
