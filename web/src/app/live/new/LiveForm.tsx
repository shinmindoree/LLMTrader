"use client";

import { useEffect, useState } from "react";

import { InfoTooltip } from "@/components/InfoTooltip";
import { createJob, listFuturesSymbols, preflightJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Job, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const LIVE_INTERVALS = ["1m", "5m", "15m", "1h"] as const;

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function loadExecutionDefaults(): { symbol: string; interval: string; applied: boolean } {
  if (typeof window === "undefined") {
    return { symbol: "BTCUSDT", interval: "1m", applied: false };
  }
  try {
    const raw = window.localStorage.getItem(EXECUTION_DEFAULTS_KEY);
    if (!raw) return { symbol: "BTCUSDT", interval: "1m", applied: false };
    const data = JSON.parse(raw) as Record<string, unknown>;
    const symbol = typeof data.symbol === "string" && data.symbol.trim()
      ? data.symbol.trim().toUpperCase()
      : "BTCUSDT";
    const intervalCandidate = typeof data.interval === "string" ? data.interval.trim() : "";
    const interval = LIVE_INTERVALS.includes(intervalCandidate as (typeof LIVE_INTERVALS)[number])
      ? intervalCandidate
      : "1m";
    return { symbol, interval, applied: true };
  } catch {
    return { symbol: "BTCUSDT", interval: "1m", applied: false };
  }
}

function formatPolicyMessages(title: string, items: string[]): string {
  if (items.length === 0) return title;
  return `${title}\n${items.map((item, idx) => `${idx + 1}. ${item}`).join("\n")}`;
}

const inputCls =
  "w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors";

export function LiveForm({
  strategies,
  onCreated,
  onSubmittingChange,
  onClose,
  activeCount,
  maxSlots,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
  onSubmittingChange?: (submitting: boolean) => void;
  onClose?: () => void;
  activeCount: number;
  maxSlots: number;
}) {
  const defaults = loadExecutionDefaults();
  const { t } = useI18n();
  const [strategyPath, setStrategyPath] = useState(strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(defaults.symbol);
  const [interval, setInterval] = useState(defaults.interval);
  const [leverage, setLeverage] = useState(1);
  const [maxPosition, setMaxPosition] = useState(0.5);
  const [dailyLossLimit, setDailyLossLimit] = useState(500);
  const [stopLossPct, setStopLossPct] = useState(0.05);
  const [stoplossCooldownCandles, setStoplossCooldownCandles] = useState(0);
  const [maxPyramidEntries, setMaxPyramidEntries] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [futuresSymbols, setFuturesSymbols] = useState<string[]>([]);

  const slotsAvailable = maxSlots - activeCount;
  const canCreate = slotsAvailable > 0;

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

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    onSubmittingChange?.(true);
    try {
      const config = {
        streams: [
          {
            symbol,
            interval,
            leverage,
            max_position: maxPosition,
            daily_loss_limit: dailyLossLimit,
            stop_loss_pct: stopLossPct,
            max_consecutive_losses: 0,
            stoploss_cooldown_candles: stoplossCooldownCandles,
            max_pyramid_entries: maxPyramidEntries,
          },
        ],
      };

      const preflight = await preflightJob({ type: "LIVE", config });
      if (!preflight.ok) {
        const msg = formatPolicyMessages(t.form.runBlocked, preflight.blockers);
        setError(msg);
        return;
      }
      if (preflight.warnings.length > 0) {
        const proceed = window.confirm(
          formatPolicyMessages(t.form.highRiskWarnings, preflight.warnings),
        );
        if (!proceed) return;
      }

      const job = await createJob({
        type: "LIVE",
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
        <p className="mb-4 whitespace-pre-wrap rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}

      {!canCreate && (
        <p className="mb-4 rounded border border-[#efb74d]/30 bg-[#2d2718]/50 px-3 py-2 text-xs text-[#efb74d]">
          {t.live.slotsFullMessage.replace("{maxSlots}", String(maxSlots))}
        </p>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc]">{symbol}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{interval}</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">{leverage}x</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">SL {(stopLossPct * 100).toFixed(1)}%</span>
      </div>

      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          {t.form.defaultsAppliedLive}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">{t.form.strategy}</div>
          <select
            className={inputCls}
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
            className={inputCls}
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
            className={inputCls}
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          >
            {LIVE_INTERVALS.map((itv) => (
              <option key={itv} value={itv} className="bg-[#131722]">
                {itv}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.leverage}<InfoTooltip text={t.form.tooltipLeverage} /></></div>
          <input
            className={inputCls}
            type="number"
            value={leverage}
            min={1}
            max={10}
            onChange={(e) => setLeverage(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.maxPosition}<InfoTooltip text={t.form.tooltipMaxPosition} /></></div>
          <input
            className={inputCls}
            type="number"
            step="0.01"
            value={maxPosition}
            min={0.01}
            max={1}
            onChange={(e) => setMaxPosition(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.dailyLossLimit}<InfoTooltip text={t.form.tooltipDailyLossLimit} /></></div>
          <input
            className={inputCls}
            type="number"
            value={dailyLossLimit}
            min={0}
            onChange={(e) => setDailyLossLimit(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.stopLoss}<InfoTooltip text={t.form.tooltipStopLoss} /></></div>
          <input
            className={inputCls}
            type="number"
            step="0.1"
            value={stopLossPct * 100}
            onChange={(e) => setStopLossPct(Number(e.target.value) / 100)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.stopLossCooldown}<InfoTooltip text={t.form.tooltipCooldown} /></></div>
          <input
            className={inputCls}
            type="number"
            value={stoplossCooldownCandles}
            min={0}
            max={1000}
            onChange={(e) => setStoplossCooldownCandles(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.maxPyramid}<InfoTooltip text={t.form.tooltipPyramidLive} /></></div>
          <input
            className={inputCls}
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
          className="rounded bg-[#ef5350] px-4 py-2 text-sm text-white hover:bg-[#d32f2f] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          onClick={onSubmit}
          disabled={submitting || !canCreate}
        >
          {canCreate
            ? `${t.live.startLive} — ${symbol}@${interval}`
            : `${t.live.slotsFull} (${activeCount}/${maxSlots})`}
        </button>
      </div>
    </div>
  );
}
