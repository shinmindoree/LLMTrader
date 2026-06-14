"use client";

import { useEffect, useState } from "react";
import type { FocusEvent, MouseEvent } from "react";

import { InfoTooltip } from "@/components/InfoTooltip";
import StrategyParamsEditor from "@/components/StrategyParamsEditor";
import { createJob, createSweep, listFuturesSymbols, preflightJob, preflightSweep } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Job, SweepDimensionSpec, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const BACKTEST_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

type SweepMode = "single" | "sweep";

type SweepParamMeta = {
  path: string;
  factor: number;
  integer?: boolean;
  categorical?: boolean;
};

// Parameters that can be swept. ``factor`` converts the value shown in the
// form (display unit) into the config unit. ``stop_loss_pct`` / ``max_position``
// are entered as % in the UI but stored as fractions in the config.
// ``categorical`` params (interval, strategy_path) only support the values mode
// and are passed through as strings without a factor.
const SWEEP_PARAMS: SweepParamMeta[] = [
  { path: "leverage", factor: 1, integer: true },
  { path: "initial_balance", factor: 1 },
  { path: "commission", factor: 1 },
  { path: "slippage_bps", factor: 1 },
  { path: "stop_loss_pct", factor: 0.01 },
  { path: "max_position", factor: 0.01 },
  { path: "max_pyramid_entries", factor: 1, integer: true },
  { path: "fixed_notional", factor: 1 },
  { path: "interval", factor: 1, categorical: true },
  { path: "strategy_path", factor: 1, categorical: true },
];

const SWEEP_PARAM_BY_PATH: Record<string, SweepParamMeta> = Object.fromEntries(
  SWEEP_PARAMS.map((p) => [p.path, p]),
);

function isCategoricalPath(path: string): boolean {
  return Boolean(SWEEP_PARAM_BY_PATH[path]?.categorical);
}

function strategyBasename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function optionsForPath(
  path: string,
  strategies: StrategyInfo[],
): { value: string; label: string }[] {
  if (path === "interval") {
    return BACKTEST_INTERVALS.map((v) => ({ value: v, label: v }));
  }
  if (path === "strategy_path") {
    return strategies.map((s) => ({
      value: s.path,
      label: s.name || strategyBasename(s.path),
    }));
  }
  return [];
}

type SweepDimDraft = {
  path: string;
  mode: "range" | "values";
  start: string;
  end: string;
  step: string;
  values: string;
};

function emptyDimDraft(usedPaths: string[]): SweepDimDraft {
  const available = SWEEP_PARAMS.find((p) => !usedPaths.includes(p.path));
  const path = available?.path ?? SWEEP_PARAMS[0].path;
  return {
    path,
    mode: isCategoricalPath(path) ? "values" : "range",
    start: "",
    end: "",
    step: "",
    values: "",
  };
}

function parseValuesList(raw: string): number[] {
  return raw
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n));
}

// Categorical values are kept as a comma-separated list of raw strings so that
// strategy paths (which contain slashes) survive round-tripping intact.
function parseStringList(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const trimmed = part.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

function countDimRuns(dim: SweepDimDraft): number {
  if (isCategoricalPath(dim.path)) {
    return parseStringList(dim.values).length;
  }
  if (dim.mode === "values") {
    return new Set(parseValuesList(dim.values)).size;
  }
  const start = Number(dim.start);
  const end = Number(dim.end);
  const step = Number(dim.step);
  if (![start, end, step].every(Number.isFinite) || step <= 0 || end < start) {
    return 0;
  }
  return Math.floor((end - start) / step + 1e-9) + 1;
}

function buildDimensionSpec(dim: SweepDimDraft): SweepDimensionSpec {
  if (isCategoricalPath(dim.path)) {
    return {
      path: dim.path,
      mode: "values",
      values: parseStringList(dim.values),
    };
  }
  const meta = SWEEP_PARAM_BY_PATH[dim.path] ?? { path: dim.path, factor: 1 };
  const f = meta.factor;
  if (dim.mode === "values") {
    return {
      path: dim.path,
      mode: "values",
      values: parseValuesList(dim.values).map((v) => v * f),
    };
  }
  return {
    path: dim.path,
    mode: "range",
    start: Number(dim.start) * f,
    end: Number(dim.end) * f,
    step: Number(dim.step) * f,
  };
}

// Convert resolved sweep dimensions (config units) back into form drafts
// (display units) so an existing sweep can be re-run with edits.
function resolvedDimsToDrafts(
  dims: { path: string; values: (number | string)[] }[],
): SweepDimDraft[] {
  return dims
    .filter((d) => SWEEP_PARAM_BY_PATH[d.path] && d.values.length > 0)
    .map((d) => {
      if (isCategoricalPath(d.path)) {
        return {
          path: d.path,
          mode: "values" as const,
          start: "",
          end: "",
          step: "",
          values: d.values.map((v) => String(v)).join(", "),
        };
      }
      const meta = SWEEP_PARAM_BY_PATH[d.path];
      const displayValues = d.values.map((v) => {
        const dv = Number(v) / meta.factor;
        return meta.integer ? Math.round(dv) : Number(dv.toFixed(10));
      });
      return {
        path: d.path,
        mode: "values" as const,
        start: "",
        end: "",
        step: "",
        values: displayValues.join(", "),
      };
    });
}

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
  slippageBps?: number;
  stopLossPct?: number;
  stopLossEnabled?: boolean;
  maxPosition?: number;
  maxPyramidEntries?: number;
  fixedNotional?: number | null;
  startDate?: string;
  endDate?: string;
  strategyParams?: Record<string, unknown>;
};

export function BacktestForm({
  strategies,
  onCreated,
  onCreatedSweep,
  onSubmittingChange,
  onClose,
  initialConfig,
  initialMode,
  initialSweepDimensions,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
  onCreatedSweep?: (sweepId: string) => void;
  onSubmittingChange?: (submitting: boolean) => void;
  onClose?: () => void;
  initialConfig?: BacktestInitialConfig;
  initialMode?: SweepMode;
  initialSweepDimensions?: { path: string; values: (number | string)[] }[];
}) {
  const defaults = loadExecutionDefaults();
  const { t } = useI18n();
  const [strategyPath, setStrategyPath] = useState(initialConfig?.strategyPath ?? strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(initialConfig?.symbol ?? defaults.symbol);
  const [interval, setInterval] = useState(initialConfig?.interval ?? defaults.interval);
  const [leverage, setLeverage] = useState<number | string>(initialConfig?.leverage ?? 1);
  const [initialBalance, setInitialBalance] = useState<number | string>(initialConfig?.initialBalance ?? 1000);
  const [commission, setCommission] = useState<number | string>(initialConfig?.commission ?? 0.0004);
  const [slippageBps, setSlippageBps] = useState<number | string>(initialConfig?.slippageBps ?? 0);
  const [stopLossPct, setStopLossPct] = useState<number | string>(initialConfig?.stopLossPct ?? 0.05);
  const [stopLossEnabled, setStopLossEnabled] = useState(initialConfig?.stopLossEnabled ?? true);
  const [maxPositionPct, setMaxPositionPct] = useState<number | string>(
    initialConfig?.maxPosition !== undefined ? Math.round(initialConfig.maxPosition * 100) : 100,
  );
  const [maxPyramidEntries, setMaxPyramidEntries] = useState<number | string>(initialConfig?.maxPyramidEntries ?? 0);
  const [fixedNotional, setFixedNotional] = useState<number | string>(
    initialConfig?.fixedNotional !== undefined && initialConfig?.fixedNotional !== null
      ? initialConfig.fixedNotional
      : "",
  );
  const now = new Date();
  const [startDate, setStartDate] = useState(() => initialConfig?.startDate ?? formatDateInputValue(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)));
  const [endDate, setEndDate] = useState(() => initialConfig?.endDate ?? formatDateInputValue(now));
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>(initialConfig?.strategyParams ?? {});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [futuresSymbols, setFuturesSymbols] = useState<string[]>([]);
  const [mode, setMode] = useState<SweepMode>(initialMode ?? "single");
  const [dims, setDims] = useState<SweepDimDraft[]>(() => {
    if (initialSweepDimensions && initialSweepDimensions.length > 0) {
      const drafts = resolvedDimsToDrafts(initialSweepDimensions);
      if (drafts.length > 0) return drafts;
    }
    return [{ path: "leverage", mode: "range", start: "1", end: "5", step: "1", values: "" }];
  });

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

  const buildConfig = (): Record<string, unknown> => {
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
      slippage_bps: slippageBps === "" ? 0 : Number(slippageBps),
      stop_loss_pct: stopLossEnabled ? stopLossPct : 0,
      max_position: Math.min(1, Math.max(0.01, (Number(maxPositionPct) || 100) / 100)),
      max_pyramid_entries: maxPyramidEntries,
      start_ts: startTs,
      end_ts: endTs,
    };
    const fixedNotionalNum =
      fixedNotional === "" || fixedNotional === null ? null : Number(fixedNotional);
    if (fixedNotionalNum !== null && !Number.isNaN(fixedNotionalNum) && fixedNotionalNum > 0) {
      config.fixed_notional = fixedNotionalNum;
    }
    if (Object.keys(strategyParams).length > 0) {
      config.strategy_params = strategyParams;
    }
    return config;
  };

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    onSubmittingChange?.(true);
    try {
      const config = buildConfig();

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

  const totalSweepRuns = dims.reduce(
    (acc, d) => acc * Math.max(0, countDimRuns(d)),
    dims.length > 0 ? 1 : 0,
  );

  const onSubmitSweep = async () => {
    setError(null);
    if (dims.length === 0) {
      setError(t.sweep.noDimensions);
      return;
    }
    for (const d of dims) {
      if (countDimRuns(d) <= 0) {
        setError(t.sweep.noDimensions);
        return;
      }
    }
    setSubmitting(true);
    onSubmittingChange?.(true);
    try {
      const baseConfig = buildConfig();
      const dimensions = dims.map(buildDimensionSpec);

      const preflight = await preflightSweep({ base_config: baseConfig, dimensions });
      if (!preflight.ok) {
        setError(formatPolicyMessages(t.form.runBlocked, preflight.blockers));
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

      const res = await createSweep({
        strategy_path: strategyPath,
        base_config: baseConfig,
        dimensions,
      });
      onCreatedSweep?.(res.sweep_id);
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
      <div className="mb-4 inline-flex rounded border border-[#2a2e39] bg-[#131722] p-0.5">
        <button
          type="button"
          className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
            mode === "single" ? "bg-[#2962ff] text-white" : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
          onClick={() => setMode("single")}
        >
          {t.sweep.modeSingle}
        </button>
        <button
          type="button"
          className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
            mode === "sweep" ? "bg-[#2962ff] text-white" : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
          onClick={() => setMode("sweep")}
        >
          {t.sweep.modeSweep}
        </button>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc]">{symbol}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{interval}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{leverage}x</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">Pos {maxPositionPct}%</span>
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
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.slippageBps}<InfoTooltip text={t.form.tooltipSlippageBps} /></></div>
          <input
            id="slippage-bps"
            name="slippage-bps"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.5"
            min={0}
            value={slippageBps}
            onChange={(e) => setSlippageBps(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (slippageBps === "" || isNaN(Number(slippageBps)) || Number(slippageBps) < 0) setSlippageBps(0); }}
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
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.maxPosition}<InfoTooltip text={t.form.tooltipMaxPosition} /></></div>
          <div className="relative">
            <input
              id="max-position"
              name="max-position"
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 pr-8 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
              type="number"
              step="1"
              min={1}
              max={100}
              value={maxPositionPct}
              onChange={(e) => setMaxPositionPct(e.target.value === "" ? "" : Number(e.target.value))}
              onBlur={() => { if (maxPositionPct === "" || isNaN(Number(maxPositionPct))) setMaxPositionPct(100); else setMaxPositionPct(Math.min(100, Math.max(1, Math.round(Number(maxPositionPct))))); }}
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#868993]">%</span>
          </div>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">
            <>
              {t.form.fixedNotional}
              <InfoTooltip text={t.form.tooltipFixedNotional} />
            </>
          </div>
          <input
            id="fixed-notional"
            name="fixed-notional"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            min={0}
            step={1}
            placeholder={t.form.fixedNotionalPlaceholder}
            value={fixedNotional}
            onChange={(e) => setFixedNotional(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => {
              if (fixedNotional !== "" && (isNaN(Number(fixedNotional)) || Number(fixedNotional) < 0)) {
                setFixedNotional("");
              }
            }}
          />
        </label>
      </div>

      {mode === "sweep" && (
        <div className="mt-5 rounded border border-[#2962ff]/30 bg-[#131722] p-4">
          <div className="mb-1 flex items-center justify-between">
            <p className="text-sm font-medium text-[#d1d4dc]">{t.sweep.title}</p>
            <span
              className={`rounded px-2 py-1 text-xs font-medium ${
                totalSweepRuns > 100
                  ? "bg-[#ef5350]/15 text-[#ef5350]"
                  : "bg-[#2962ff]/15 text-[#2962ff]"
              }`}
            >
              {t.sweep.totalRuns}: {totalSweepRuns} ({t.sweep.maxRuns} 100)
            </span>
          </div>
          <p className="mb-3 text-xs text-[#868993]">{t.sweep.description}</p>

          <div className="space-y-3">
            {dims.map((dim, idx) => {
              const usedPaths = dims.filter((_, i) => i !== idx).map((d) => d.path);
              const runs = countDimRuns(dim);
              const categorical = isCategoricalPath(dim.path);
              const categoricalOptions = categorical
                ? optionsForPath(dim.path, strategies)
                : [];
              const selectedValues = categorical ? parseStringList(dim.values) : [];
              return (
                <div
                  key={idx}
                  className="flex flex-wrap items-end gap-2 rounded border border-[#2a2e39] bg-[#0e1117] p-3"
                >
                  <label className="text-xs">
                    <div className="mb-1 text-[#868993]">{t.sweep.parameter}</div>
                    <select
                      className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                      value={dim.path}
                      onChange={(e) => {
                        const path = e.target.value;
                        setDims((prev) =>
                          prev.map((d, i) => {
                            if (i !== idx) return d;
                            const wasCategorical = isCategoricalPath(d.path);
                            const nowCategorical = isCategoricalPath(path);
                            if (wasCategorical !== nowCategorical) {
                              return {
                                ...d,
                                path,
                                mode: nowCategorical ? "values" : "range",
                                start: "",
                                end: "",
                                step: "",
                                values: "",
                              };
                            }
                            return { ...d, path };
                          }),
                        );
                      }}
                    >
                      {SWEEP_PARAMS.map((p) => (
                        <option
                          key={p.path}
                          value={p.path}
                          disabled={usedPaths.includes(p.path)}
                          className="bg-[#131722]"
                        >
                          {t.sweep.paramLabels[p.path as keyof typeof t.sweep.paramLabels] ?? p.path}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!categorical && (
                    <label className="text-xs">
                      <div className="mb-1 text-[#868993]">{t.sweep.mode}</div>
                      <select
                        className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                        value={dim.mode}
                        onChange={(e) => {
                          const m = e.target.value as "range" | "values";
                          setDims((prev) =>
                            prev.map((d, i) => (i === idx ? { ...d, mode: m } : d)),
                          );
                        }}
                      >
                        <option value="range" className="bg-[#131722]">{t.sweep.modeRange}</option>
                        <option value="values" className="bg-[#131722]">{t.sweep.modeValues}</option>
                      </select>
                    </label>
                  )}

                  {categorical ? (
                    <div className="flex-1 text-xs">
                      <div className="mb-1 text-[#868993]">{t.sweep.options}</div>
                      <div className="flex flex-wrap gap-1.5">
                        {categoricalOptions.length === 0 ? (
                          <span className="text-[10px] text-[#868993]">
                            {t.sweep.noOptions}
                          </span>
                        ) : (
                          categoricalOptions.map((opt) => {
                            const selected = selectedValues.includes(opt.value);
                            return (
                              <button
                                key={opt.value}
                                type="button"
                                title={opt.value}
                                className={`rounded border px-2 py-1 text-xs transition-colors ${
                                  selected
                                    ? "border-[#2962ff] bg-[#2962ff]/15 text-[#2962ff]"
                                    : "border-[#2a2e39] bg-[#131722] text-[#868993] hover:border-[#2962ff]/50"
                                }`}
                                onClick={() => {
                                  const next = selected
                                    ? selectedValues.filter((v) => v !== opt.value)
                                    : [...selectedValues, opt.value];
                                  setDims((prev) =>
                                    prev.map((d, i) =>
                                      i === idx ? { ...d, values: next.join(", ") } : d,
                                    ),
                                  );
                                }}
                              >
                                {opt.label}
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>
                  ) : dim.mode === "range" ? (
                    <>
                      <label className="text-xs">
                        <div className="mb-1 text-[#868993]">{t.sweep.start}</div>
                        <input
                          className="w-20 rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                          type="number"
                          value={dim.start}
                          onChange={(e) =>
                            setDims((prev) =>
                              prev.map((d, i) => (i === idx ? { ...d, start: e.target.value } : d)),
                            )
                          }
                        />
                      </label>
                      <label className="text-xs">
                        <div className="mb-1 text-[#868993]">{t.sweep.end}</div>
                        <input
                          className="w-20 rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                          type="number"
                          value={dim.end}
                          onChange={(e) =>
                            setDims((prev) =>
                              prev.map((d, i) => (i === idx ? { ...d, end: e.target.value } : d)),
                            )
                          }
                        />
                      </label>
                      <label className="text-xs">
                        <div className="mb-1 text-[#868993]">{t.sweep.step}</div>
                        <input
                          className="w-20 rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                          type="number"
                          value={dim.step}
                          onChange={(e) =>
                            setDims((prev) =>
                              prev.map((d, i) => (i === idx ? { ...d, step: e.target.value } : d)),
                            )
                          }
                        />
                      </label>
                    </>
                  ) : (
                    <label className="flex-1 text-xs">
                      <div className="mb-1 text-[#868993]">{t.sweep.values}</div>
                      <input
                        className="w-full min-w-[12rem] rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                        type="text"
                        placeholder={t.sweep.valuesHint}
                        value={dim.values}
                        onChange={(e) =>
                          setDims((prev) =>
                            prev.map((d, i) => (i === idx ? { ...d, values: e.target.value } : d)),
                          )
                        }
                      />
                    </label>
                  )}

                  <span className="ml-auto whitespace-nowrap text-[10px] text-[#868993]">
                    {runs} {t.sweep.run}
                  </span>
                  <button
                    type="button"
                    className="rounded border border-[#2a2e39] px-2 py-1.5 text-xs text-[#868993] hover:border-[#ef5350] hover:text-[#ef5350] transition-colors"
                    onClick={() => setDims((prev) => prev.filter((_, i) => i !== idx))}
                  >
                    {t.sweep.removeDimension}
                  </button>
                </div>
              );
            })}
          </div>

          {dims.length < SWEEP_PARAMS.length && (
            <button
              type="button"
              className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-3 py-1.5 text-xs text-[#d1d4dc] hover:border-[#2962ff] transition-colors"
              onClick={() =>
                setDims((prev) => [...prev, emptyDimDraft(prev.map((d) => d.path))])
              }
            >
              + {t.sweep.addDimension}
            </button>
          )}

          {totalSweepRuns > 100 && (
            <p className="mt-3 text-xs text-[#ef5350]">{t.sweep.runsExceeded}</p>
          )}
        </div>
      )}

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
        {mode === "sweep" ? (
          <button
            className="rounded bg-[#2962ff] px-4 py-2 text-sm text-white hover:bg-[#1e53d5] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            onClick={onSubmitSweep}
            disabled={submitting || totalSweepRuns === 0 || totalSweepRuns > 100}
          >
            {submitting ? t.sweep.creating : `${t.sweep.createSweep} (${totalSweepRuns})`}
          </button>
        ) : (
          <button
            className="rounded bg-[#2962ff] px-4 py-2 text-sm text-white hover:bg-[#1e53d5] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            onClick={onSubmit}
            disabled={submitting}
          >
            {t.backtest.startBacktest}
          </button>
        )}
      </div>
    </div>
  );
}
