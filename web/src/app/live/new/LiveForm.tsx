"use client";

import { useEffect, useState } from "react";

import useSWR from "swr";
import { InfoTooltip } from "@/components/InfoTooltip";
import StrategyParamsEditor from "@/components/StrategyParamsEditor";
import { createJob, getBinanceAccountSummary, listFuturesSymbols, preflightJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { BinanceAccountSummary, Job, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const LIVE_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"] as const;

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
  const [leverage, setLeverage] = useState<number | string>(1);
  const [maxPositionPct, setMaxPositionPct] = useState<number | string>(50);
  const maxPosition = (Number(maxPositionPct) || 0) / 100;
  const [dailyLossLimit, setDailyLossLimit] = useState<number | string>(500);
  const [stopLossPct, setStopLossPct] = useState<number | string>(0.05);
  const [stoplossCooldownCandles, setStoplossCooldownCandles] = useState<number | string>(0);
  const [maxPyramidEntries, setMaxPyramidEntries] = useState<number | string>(0);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [futuresSymbols, setFuturesSymbols] = useState<string[]>([]);
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>({});

  const slotsAvailable = maxSlots - activeCount;
  const canCreate = slotsAvailable > 0;

  const { data: accountSnapshot } = useSWR<BinanceAccountSummary>(
    "binanceAccountSummary",
    () => getBinanceAccountSummary(),
    { dedupingInterval: 10_000 },
  );

  const totalWallet = accountSnapshot?.total_wallet_balance ?? null;
  const availableBalance = accountSnapshot?.available_balance ?? null;
  const estimatedPosition =
    availableBalance !== null ? availableBalance * maxPosition * leverage : null;

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
      const config: Record<string, unknown> = {
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
      if (Object.keys(strategyParams).length > 0) {
        config.strategy_params = strategyParams;
      }

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
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">Pos {maxPositionPct}%</span>
        <span className="rounded bg-[#131722] px-2 py-1 text-xs text-[#868993]">SL {(stopLossPct * 100).toFixed(1)}%</span>
      </div>

      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          {t.form.defaultsAppliedLive}
        </p>
      ) : null}

      {/* Wallet & Position Size Estimator */}
      {accountSnapshot?.connected && availableBalance !== null && (
        <div className="mb-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
          <div className="mb-2 grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-[#868993]">{t.binanceAccount.totalWallet}</span>
              <div className="mt-0.5 text-sm font-semibold text-[#d1d4dc]">
                {totalWallet !== null ? `${totalWallet.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT` : "-"}
              </div>
            </div>
            <div>
              <span className="text-[#868993]">{t.binanceAccount.availableBalance}</span>
              <div className="mt-0.5 text-sm font-semibold text-[#26a69a]">
                {availableBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT
              </div>
            </div>
          </div>
          <div className="border-t border-[#2a2e39] pt-2">
            <div className="flex items-center gap-1.5 text-xs text-[#868993]">
              <span>예상 진입 포지션</span>
              <InfoTooltip text="가용 잔고 × Max Position × Leverage" />
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-lg font-bold text-[#d1d4dc]">
                {estimatedPosition !== null
                  ? `${estimatedPosition.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`
                  : "-"}
              </span>
              <span className="text-[10px] text-[#868993]">
                ({availableBalance.toLocaleString(undefined, { maximumFractionDigits: 0 })} × {maxPositionPct}% × {leverage}x)
              </span>
            </div>
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">{t.form.strategy}</div>
          <select
            className={inputCls}
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
            max={125}
            onChange={(e) => setLeverage(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (leverage === "" || isNaN(Number(leverage))) setLeverage(1); else setLeverage(Math.min(125, Math.max(1, Math.floor(Number(leverage))))); }}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.maxPosition}<InfoTooltip text={t.form.tooltipMaxPosition} /></></div>
          <div className="relative">
            <input
              className={`${inputCls} pr-8`}
              type="number"
              step="1"
              value={maxPositionPct}
              min={1}
              max={100}
              onChange={(e) => setMaxPositionPct(e.target.value === "" ? "" : Number(e.target.value))}
              onBlur={() => { if (maxPositionPct === "" || isNaN(Number(maxPositionPct))) setMaxPositionPct(50); else setMaxPositionPct(Math.min(100, Math.max(1, Math.round(Number(maxPositionPct))))); }}
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#868993]">%</span>
          </div>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.dailyLossLimit}<InfoTooltip text={t.form.tooltipDailyLossLimit} /></></div>
          <input
            className={inputCls}
            type="number"
            value={dailyLossLimit}
            min={0}
            onChange={(e) => setDailyLossLimit(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (dailyLossLimit === "" || isNaN(Number(dailyLossLimit))) setDailyLossLimit(500); }}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.stopLoss}<InfoTooltip text={t.form.tooltipStopLoss} /></></div>
          <div className="relative">
            <input
              className={`${inputCls} pr-8`}
              type="number"
              step="0.1"
              value={stopLossPct === "" ? "" : Number(stopLossPct) * 100}
              min={0.1}
              max={50}
              onChange={(e) => setStopLossPct(e.target.value === "" ? "" : Number(e.target.value) / 100)}
              onBlur={() => { if (stopLossPct === "" || isNaN(Number(stopLossPct))) setStopLossPct(0.05); else setStopLossPct(Math.min(0.5, Math.max(0.001, Number(stopLossPct)))); }}
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#868993]">%</span>
          </div>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]"><>{t.form.stopLossCooldown}<InfoTooltip text={t.form.tooltipCooldown} /></></div>
          <input
            className={inputCls}
            type="number"
            value={stoplossCooldownCandles}
            min={0}
            max={1000}
            onChange={(e) => setStoplossCooldownCandles(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={() => { if (stoplossCooldownCandles === "" || isNaN(Number(stoplossCooldownCandles))) setStoplossCooldownCandles(0); }}
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
