"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { runKimpBacktest, runKimpUniverseBacktest } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type {
  KimpBacktestResponse,
  KimpBacktestTrade,
  KimpHedgeMode,
  KimpUniverseBacktestItem,
  KimpUniverseBacktestResponse,
} from "@/lib/types";

type Props = {
  symbol: string;
  onSelect: (symbol: string) => void;
  availableSymbols?: string[];
};

type IntervalChoice = "auto" | 1 | 5 | 15 | 30 | 60 | 240;

type SharedConfig = {
  days: number;
  grossCap: number;
  fullBuildZ: number;
  flatZ: number;
  hedgeMode: KimpHedgeMode;
  includeFunding: boolean;
  intervalMin: IntervalChoice;
};

const DEFAULT_CONFIG: SharedConfig = {
  days: 30,
  grossCap: 10_000_000,
  fullBuildZ: -2.0,
  flatZ: 0.5,
  hedgeMode: "quantity",
  includeFunding: true,
  intervalMin: "auto",
};

const INTERVAL_CHOICES: IntervalChoice[] = ["auto", 1, 5, 15, 30, 60, 240];

function intervalLabel(v: IntervalChoice): string {
  if (v === "auto") return "Auto";
  if (v === 60) return "1h";
  if (v === 240) return "4h";
  return `${v}m`;
}

function intervalFromMin(min: number | null | undefined): string {
  if (min == null || !Number.isFinite(min)) return "—";
  if (min === 60) return "1h";
  if (min === 240) return "4h";
  return `${min}m`;
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function fmtKrw(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${sign}₩${(abs / 1e8).toFixed(2)}억`;
  if (abs >= 1e4) return `${sign}₩${(abs / 1e4).toFixed(0)}만`;
  return `${sign}₩${abs.toFixed(0)}`;
}

function fmtKrwWithCount(v: number | null | undefined, count: number | null | undefined, unit: string): string {
  return `${fmtKrw(v)} / ${count ?? 0}${unit}`;
}

function fmtTime(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtScore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(3);
}

function signClass(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "text-[#868993]";
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-rose-400";
  return "text-[#c3c5cc]";
}

export default function KimpBacktestPanel({ symbol, onSelect, availableSymbols = [] }: Props) {
  const { t } = useI18n();
  const b = t.hubs.arbitrage.kimp.backtest;
  const selectedSymbol = symbol.trim().toUpperCase() || "BTC";

  const [tab, setTab] = useState<"single" | "universe">("single");
  const [cfg, setCfg] = useState<SharedConfig>(DEFAULT_CONFIG);
  const [limit, setLimit] = useState(20);
  const [concurrency, setConcurrency] = useState(4);

  const [singleBusy, setSingleBusy] = useState(false);
  const [singleRes, setSingleRes] = useState<KimpBacktestResponse | null>(null);
  const [singleErr, setSingleErr] = useState<string | null>(null);

  const [uniBusy, setUniBusy] = useState(false);
  const [uniRes, setUniRes] = useState<KimpUniverseBacktestResponse | null>(null);
  const [uniErr, setUniErr] = useState<string | null>(null);

  function patch<K extends keyof SharedConfig>(key: K, value: SharedConfig[K]) {
    setCfg((c) => ({ ...c, [key]: value }));
  }

  async function onRunSingle() {
    setSingleBusy(true);
    setSingleErr(null);
    try {
      const res = await runKimpBacktest({
        symbol: selectedSymbol,
        days: cfg.days,
        price_source: "candles",
        rate_mode: "usdt",
        include_funding: cfg.includeFunding,
        interval_min: cfg.intervalMin === "auto" ? null : cfg.intervalMin,
        gross_cap_krw: cfg.grossCap,
        full_build_z: cfg.fullBuildZ,
        flat_z: cfg.flatZ,
        hedge_mode: cfg.hedgeMode,
      });
      setSingleRes(res);
      if (!res.success) setSingleErr(res.error ?? b.noData);
    } catch (e) {
      setSingleErr(e instanceof Error ? e.message : String(e));
      setSingleRes(null);
    } finally {
      setSingleBusy(false);
    }
  }

  async function onRunUniverse() {
    setUniBusy(true);
    setUniErr(null);
    try {
      const res = await runKimpUniverseBacktest({
        limit,
        days: cfg.days,
        rate_mode: "usdt",
        include_funding: cfg.includeFunding,
        gross_cap_krw: cfg.grossCap,
        full_build_z: cfg.fullBuildZ,
        flat_z: cfg.flatZ,
        hedge_mode: cfg.hedgeMode,
        concurrency,
      });
      setUniRes(res);
      if (!res.success) setUniErr(res.error ?? b.noData);
    } catch (e) {
      setUniErr(e instanceof Error ? e.message : String(e));
      setUniRes(null);
    } finally {
      setUniBusy(false);
    }
  }

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="flex flex-col gap-2 border-b border-[#26272d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-white">{b.title}</div>
          <div className="text-xs text-[#868993]">{b.subtitle}</div>
        </div>
        <div className="inline-flex rounded-md border border-[#26272d] bg-[#0e0f14] p-0.5">
          {(["single", "universe"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setTab(mode)}
              className={`rounded px-2.5 py-1 text-[11px] ${
                tab === mode
                  ? "bg-[#22232b] text-white"
                  : "text-[#868993] hover:text-[#c3c5cc]"
              }`}
            >
              {b.tabs[mode]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 px-4 py-3 sm:grid-cols-4">
        <NumField
          label={b.fields.days}
          value={cfg.days}
          onChange={(v) => patch("days", v)}
          min={1}
          max={365}
        />
        <NumField
          label={b.fields.grossCap}
          value={cfg.grossCap}
          onChange={(v) => patch("grossCap", v)}
          min={1}
          step={1_000_000}
        />
        <NumField
          label={b.fields.fullBuildZ}
          value={cfg.fullBuildZ}
          onChange={(v) => patch("fullBuildZ", v)}
          step={0.1}
        />
        <NumField
          label={b.fields.flatZ}
          value={cfg.flatZ}
          onChange={(v) => patch("flatZ", v)}
          step={0.1}
        />
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-[#868993]">
            {b.fields.hedgeMode}
          </span>
          <select
            value={cfg.hedgeMode}
            onChange={(e) => patch("hedgeMode", e.target.value as KimpHedgeMode)}
            className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] focus:border-[#3a3b44] focus:outline-none"
          >
            <option value="quantity">{b.hedgeModes.quantity}</option>
            <option value="delta">{b.hedgeModes.delta}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-[#868993]">
            {b.fields.interval}
          </span>
          <select
            value={String(cfg.intervalMin)}
            onChange={(e) => {
              const raw = e.target.value;
              patch("intervalMin", raw === "auto" ? "auto" : (Number(raw) as IntervalChoice));
            }}
            className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] focus:border-[#3a3b44] focus:outline-none"
          >
            {INTERVAL_CHOICES.map((choice) => (
              <option key={String(choice)} value={String(choice)}>
                {choice === "auto" ? b.fields.intervalAuto : intervalLabel(choice)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-[#868993]">
            {b.fields.includeFunding}
          </span>
          <button
            type="button"
            onClick={() => patch("includeFunding", !cfg.includeFunding)}
            className={`rounded-md border px-2 py-1 text-xs ${
              cfg.includeFunding
                ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400"
                : "border-[#26272d] bg-[#0e0f14] text-[#868993]"
            }`}
          >
            {cfg.includeFunding ? "ON" : "OFF"}
          </button>
        </label>
      </div>

      {tab === "single" ? (
        <SinglePanel
          busy={singleBusy}
          res={singleRes}
          err={singleErr}
          onRun={onRunSingle}
          labels={b}
          symbol={selectedSymbol}
          onSelect={onSelect}
          availableSymbols={availableSymbols}
          capitalBase={cfg.grossCap}
        />
      ) : (
        <UniversePanel
          busy={uniBusy}
          res={uniRes}
          err={uniErr}
          limit={limit}
          concurrency={concurrency}
          onLimit={setLimit}
          onConcurrency={setConcurrency}
          onRun={onRunUniverse}
          onSelect={onSelect}
          selected={selectedSymbol}
          labels={b}
        />
      )}
    </div>
  );
}

type Labels = ReturnType<typeof useI18n>["t"]["hubs"]["arbitrage"]["kimp"]["backtest"];

function SinglePanel({
  busy,
  res,
  err,
  onRun,
  labels,
  symbol,
  onSelect,
  availableSymbols,
  capitalBase,
}: {
  busy: boolean;
  res: KimpBacktestResponse | null;
  err: string | null;
  onRun: () => void;
  labels: Labels;
  symbol: string;
  onSelect: (symbol: string) => void;
  availableSymbols: string[];
  capitalBase: number;
}) {
  const m = res?.success ? res.metrics ?? null : null;
  const trades = res?.success ? res.trades ?? [] : [];
  const [view, setView] = useState<"chart" | "trades">("chart");
  return (
    <div className="border-t border-[#26272d] px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onRun}
          disabled={busy}
          className="rounded-md border border-[#26272d] bg-[#1a1b22] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#22232b] disabled:opacity-50"
        >
          {busy ? labels.running : labels.run}
        </button>
        <SymbolSearch
          value={symbol}
          onSelect={onSelect}
          options={availableSymbols}
          labels={labels}
        />
      </div>

      {err ? (
        <div className="mt-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-400">
          {err}
        </div>
      ) : null}

      {m ? (
        <>
          <div className="mt-3 inline-flex rounded-md border border-[#26272d] bg-[#0e0f14] p-0.5">
            {(["chart", "trades"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                className={`rounded px-2.5 py-1 text-[11px] ${
                  view === v
                    ? "bg-[#22232b] text-white"
                    : "text-[#868993] hover:text-[#c3c5cc]"
                }`}
              >
                {v === "chart" ? labels.view.chart : `${labels.view.trades} (${trades.length})`}
              </button>
            ))}
          </div>

          {view === "chart" ? (
            <>
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                <Metric label={labels.metrics.totalReturn} value={fmtPct(m.total_return_pct)} cls={signClass(m.total_return_pct)} />
                <Metric label={labels.metrics.netProfit} value={fmtKrw(m.net_profit_krw)} cls={signClass(m.net_profit_krw)} />
                <Metric label={labels.metrics.kimpPnl} value={fmtKrw(m.kimp_pnl_krw)} cls={signClass(m.kimp_pnl_krw)} />
                <Metric
                  label={labels.metrics.funding}
                  value={fmtKrwWithCount(m.funding_income_krw, m.funding_event_count, labels.metrics.eventUnit)}
                  cls={signClass(m.funding_income_krw)}
                />
                <Metric label={labels.metrics.feeDrag} value={fmtKrw(m.fee_drag_krw)} cls="text-[#868993]" />
                <Metric label={labels.metrics.completedTrades} value={String(m.completed_trades)} />
                <Metric label={labels.metrics.entriesExits} value={`${m.n_entries}/${m.n_exits}`} />
                <Metric label={labels.metrics.mdd} value={fmtPct(m.max_drawdown_pct)} cls="text-rose-400" />
                <Metric label={labels.metrics.timeInMarket} value={fmtPct(m.time_in_market_pct)} />
                <Metric label={labels.metrics.avgKimp} value={fmtPct(m.avg_kimp_pct)} />
                <Metric label={labels.metrics.finalKimp} value={fmtPct(m.final_kimp_pct)} />
                <Metric label={labels.metrics.bars} value={String(m.n_bars)} />
                <Metric label={labels.metrics.interval} value={intervalFromMin(res?.interval_min)} />
              </div>
              <div className="mt-3">
                <KimpTradeChart
                  trades={trades}
                  capitalBase={capitalBase}
                  symbol={symbol}
                  labels={labels}
                />
              </div>
            </>
          ) : (
            <TradesTable trades={trades} symbol={symbol} labels={labels} />
          )}
        </>
      ) : !err ? (
        <div className="mt-3 text-xs text-[#868993]">{labels.empty}</div>
      ) : null}
    </div>
  );
}

function UniversePanel({
  busy,
  res,
  err,
  limit,
  concurrency,
  onLimit,
  onConcurrency,
  onRun,
  onSelect,
  selected,
  labels,
}: {
  busy: boolean;
  res: KimpUniverseBacktestResponse | null;
  err: string | null;
  limit: number;
  concurrency: number;
  onLimit: (v: number) => void;
  onConcurrency: (v: number) => void;
  onRun: () => void;
  onSelect: (symbol: string) => void;
  selected: string;
  labels: Labels;
}) {
  const items = res?.items ?? [];
  return (
    <div className="border-t border-[#26272d] px-4 py-3">
      <div className="flex flex-wrap items-end gap-3">
        <NumField label={labels.fields.limit} value={limit} onChange={onLimit} min={1} max={200} />
        <NumField
          label={labels.fields.concurrency}
          value={concurrency}
          onChange={onConcurrency}
          min={1}
          max={8}
        />
        <button
          type="button"
          onClick={onRun}
          disabled={busy}
          className="rounded-md border border-[#26272d] bg-[#1a1b22] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#22232b] disabled:opacity-50"
        >
          {busy ? labels.ranking : labels.rankAll}
        </button>
        {res?.success ? (
          <span className="text-[11px] text-[#868993]">
            {labels.summary
              .replace("{ok}", String(res.n_ok))
              .replace("{total}", String(res.n_symbols))}
          </span>
        ) : null}
      </div>

      <div className="mt-1 text-[10px] text-[#5b5d66]">{labels.scoreHint}</div>

      {err ? (
        <div className="mt-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-400">
          {err}
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="bg-[#0e0f14] text-[10px] uppercase tracking-wider text-[#868993]">
              <tr>
                <th className="px-3 py-2">{labels.rankColumns.rank}</th>
                <th className="px-3 py-2">{labels.rankColumns.symbol}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.score}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.totalReturn}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.funding}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.mdd}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.sharpe}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.bars}</th>
                <th className="px-3 py-2 text-right">{labels.rankColumns.status}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it: KimpUniverseBacktestItem, idx) => {
                const ok = it.metrics != null;
                const isSel = it.symbol === selected;
                return (
                  <tr
                    key={it.symbol}
                    onClick={() => onSelect(it.symbol)}
                    className={`cursor-pointer border-t border-[#1a1b22] tabular-nums transition-colors hover:bg-[#1a1b22] ${
                      isSel ? "bg-[#1a1b22]" : ""
                    }`}
                  >
                    <td className="px-3 py-2 text-[#868993]">{idx + 1}</td>
                    <td className="px-3 py-2 font-medium text-white">{it.symbol}</td>
                    <td className="px-3 py-2 text-right font-medium text-[#c3c5cc]">
                      {fmtScore(it.score)}
                    </td>
                    <td className={`px-3 py-2 text-right ${signClass(it.metrics?.total_return_pct)}`}>
                      {fmtPct(it.metrics?.total_return_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right ${signClass(it.metrics?.funding_income_krw)}`}>
                      {fmtKrwWithCount(
                        it.metrics?.funding_income_krw,
                        it.metrics?.funding_event_count,
                        labels.metrics.eventUnit,
                      )}
                    </td>
                    <td className="px-3 py-2 text-right text-rose-400">
                      {fmtPct(it.metrics?.max_drawdown_pct)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">
                      {fmtNum(it.metrics?.sharpe)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">{it.n_bars}</td>
                    <td className="px-3 py-2 text-right">
                      {ok ? (
                        <span className="text-emerald-400">{labels.ok}</span>
                      ) : (
                        <span className="text-rose-400" title={it.error ?? ""}>
                          {labels.failed}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : !err && !busy ? (
        <div className="mt-3 text-xs text-[#868993]">{labels.empty}</div>
      ) : null}
    </div>
  );
}

function Metric({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="rounded-md border border-[#26272d] bg-[#0e0f14] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-[#868993]">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${cls ?? "text-white"}`}>
        {value}
      </div>
    </div>
  );
}

function NumField({
  label,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider text-[#868993]">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(v);
        }}
        className="w-full rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] focus:border-[#3a3b44] focus:outline-none"
      />
    </label>
  );
}

function SymbolSearch({
  value,
  onSelect,
  options,
  labels,
}: {
  value: string;
  onSelect: (symbol: string) => void;
  options: string[];
  labels: Labels;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    const uniq = Array.from(new Set(options.map((s) => s.toUpperCase())));
    const list = q ? uniq.filter((s) => s.includes(q)) : uniq;
    return list.slice(0, 50);
  }, [options, query]);

  const commit = (sym: string) => {
    const s = sym.trim().toUpperCase();
    if (!s) return;
    onSelect(s);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={boxRef} className="relative">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-[#868993]">{labels.tabs.single}:</span>
        <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-xs font-semibold text-emerald-400">
          {value}
        </span>
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(filtered[0] ?? query);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder={labels.search.placeholder}
          className="w-40 rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] focus:border-[#3a3b44] focus:outline-none"
        />
      </div>
      {open ? (
        <div className="absolute z-30 mt-1 max-h-56 w-48 overflow-auto rounded-md border border-[#26272d] bg-[#13141a] py-1 shadow-lg">
          {filtered.length > 0 ? (
            filtered.map((sym) => (
              <button
                key={sym}
                type="button"
                onClick={() => commit(sym)}
                className={`block w-full px-3 py-1.5 text-left text-xs hover:bg-[#1a1b22] ${
                  sym === value.toUpperCase() ? "text-emerald-400" : "text-[#c3c5cc]"
                }`}
              >
                {sym}
              </button>
            ))
          ) : (
            <div className="px-3 py-1.5 text-xs text-[#868993]">
              {query.trim()
                ? labels.search.useCustom.replace("{symbol}", query.trim().toUpperCase())
                : labels.search.empty}
            </div>
          )}
          {query.trim() && !filtered.includes(query.trim().toUpperCase()) ? (
            <button
              type="button"
              onClick={() => commit(query)}
              className="mt-1 block w-full border-t border-[#26272d] px-3 py-1.5 text-left text-xs text-[#42a5f5] hover:bg-[#1a1b22]"
            >
              {labels.search.useCustom.replace("{symbol}", query.trim().toUpperCase())}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

type TradePoint = {
  index: number;
  timestamp: number;
  entryTs: number;
  pnl: number;
  equity: number;
  returnPct: number;
  symbol: string;
  exitReason: string;
};

function KimpTradeChart({
  trades,
  capitalBase,
  symbol,
  labels,
}: {
  trades: KimpBacktestTrade[];
  capitalBase: number;
  symbol: string;
  labels: Labels;
}) {
  const c = labels.chart;
  const points: TradePoint[] = useMemo(() => {
    return trades.reduce<{ acc: TradePoint[]; cum: number }>(
      (state, tr) => {
        const cum = state.cum + tr.net_pnl_krw;
        state.acc.push({
          index: tr.index,
          timestamp: tr.exit_t,
          entryTs: tr.entry_t,
          pnl: tr.net_pnl_krw,
          equity: capitalBase + cum,
          returnPct: tr.return_pct,
          symbol,
          exitReason: tr.exit_reason,
        });
        return { acc: state.acc, cum };
      },
      { acc: [], cum: 0 },
    ).acc;
  }, [trades, capitalBase, symbol]);

  const [hovered, setHovered] = useState<TradePoint | null>(null);
  const [tipPos, setTipPos] = useState({ x: 0, y: 0 });

  const width = 900;
  const height = 320;
  const padding = 36;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  const totalRange = Math.max(1, points.length - 1);
  const [visibleRange, setVisibleRange] = useState<[number, number] | null>(null);
  const visibleRangeRef = useRef<[number, number] | null>(null);
  useEffect(() => {
    visibleRangeRef.current = visibleRange;
  }, [visibleRange]);

  const [lastLen, setLastLen] = useState(points.length);
  if (lastLen !== points.length) {
    setVisibleRange(null);
    setLastLen(points.length);
  }

  const svgRef = useRef<SVGSVGElement>(null);
  const panRef = useRef<{
    pointerId: number;
    startClientX: number;
    startVStart: number;
    startVEnd: number;
    moved: boolean;
  } | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  const clampRange = useCallback(
    (start: number, end: number): [number, number] => {
      const minSpan = Math.min(1, totalRange);
      const span = Math.max(minSpan, Math.min(totalRange, end - start));
      let s = start;
      let e = s + span;
      if (s < 0) {
        s = 0;
        e = s + span;
      }
      if (e > totalRange) {
        e = totalRange;
        s = e - span;
      }
      if (s < 0) s = 0;
      return [s, e];
    },
    [totalRange],
  );

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleWheel = (e: WheelEvent) => {
      if (points.length < 2) return;
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      if (rect.width <= 0) return;
      const xVB = ((e.clientX - rect.left) / rect.width) * width;
      const xPlot = Math.max(padding, Math.min(width - padding, xVB));
      const xRel = plotWidth > 0 ? (xPlot - padding) / plotWidth : 0;
      const [cs, ce] = visibleRangeRef.current ?? [0, totalRange];
      const range = Math.max(ce - cs, 0.0001);
      const idxAtCursor = cs + xRel * range;
      const zoomFactor = e.deltaY < 0 ? 0.85 : 1.18;
      const minRange = Math.min(2, totalRange);
      const newRange = Math.max(minRange, Math.min(totalRange, range * zoomFactor));
      let ns = idxAtCursor - xRel * newRange;
      let ne = ns + newRange;
      [ns, ne] = clampRange(ns, ne);
      setVisibleRange([ns, ne]);
    };
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, [points.length, totalRange, plotWidth, clampRange]);

  if (points.length === 0) {
    return (
      <div className="rounded-md border border-[#26272d] bg-[#0e0f14] px-4 py-6 text-center text-xs text-[#868993]">
        {c.noData}
      </div>
    );
  }

  const [vStartRaw, vEndRaw] = visibleRange ?? [0, totalRange];
  const [vStart, vEnd] = clampRange(vStartRaw, vEndRaw);
  const visibleSpan = Math.max(vEnd - vStart, 0.0001);
  const isZoomed =
    visibleRange !== null && (vStart > 0.0001 || vEnd < totalRange - 0.0001);
  const step = plotWidth / Math.max(visibleSpan, 1);
  const barWidth = Math.max(2, Math.min(24, step * 0.6));

  const xForIndex = (idx: number) => padding + ((idx - vStart) / visibleSpan) * plotWidth;

  const visibleStartIdx = Math.max(0, Math.floor(vStart));
  const visibleEndIdx = Math.min(points.length - 1, Math.ceil(vEnd));
  const visiblePoints = points.slice(visibleStartIdx, visibleEndIdx + 1);
  const maxAbsPnl = Math.max(...visiblePoints.map((p) => Math.abs(p.pnl)), 1);
  const yZero = padding + plotHeight / 2;
  const pnlScale = plotHeight / (2 * maxAbsPnl);

  const eqValues = visiblePoints.map((p) => p.equity);
  const eqMin = eqValues.length ? Math.min(...eqValues) : 0;
  const eqMax = eqValues.length ? Math.max(...eqValues) : 1;
  const eqRange = Math.max(eqMax - eqMin, 1);

  const yPnl = (v: number) => yZero - v * pnlScale;
  const yEq = (v: number) => padding + ((eqMax - v) / eqRange) * plotHeight;

  const linePath = visiblePoints
    .map((p, idx) => {
      const x = xForIndex(visibleStartIdx + idx);
      const y = yEq(p.equity);
      return `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0 || points.length < 2) return;
    panRef.current = {
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startVStart: vStart,
      startVEnd: vEnd,
      moved: false,
    };
    setHovered(null);
    try {
      (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const state = panRef.current;
    if (!state || state.pointerId !== e.pointerId) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0) return;
    const pxPerUnit = (rect.width * (plotWidth / width)) / visibleSpan;
    if (pxPerUnit <= 0) return;
    const dx = e.clientX - state.startClientX;
    if (!state.moved && Math.abs(dx) > 3) {
      state.moved = true;
      setIsPanning(true);
    }
    if (!state.moved) return;
    const deltaIdx = -dx / pxPerUnit;
    let ns = state.startVStart + deltaIdx;
    let ne = state.startVEnd + deltaIdx;
    [ns, ne] = clampRange(ns, ne);
    setVisibleRange([ns, ne]);
  };

  const finishPan = (e: React.PointerEvent<SVGSVGElement>) => {
    const state = panRef.current;
    if (!state || state.pointerId !== e.pointerId) return;
    panRef.current = null;
    setIsPanning(false);
    try {
      (e.currentTarget as SVGSVGElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const resetZoom = () => setVisibleRange(null);
  const cursorClass = isPanning ? "cursor-grabbing" : "cursor-grab";

  return (
    <div
      className="relative rounded-md border border-[#26272d] bg-[#0e0f14] p-4"
      onMouseLeave={() => setHovered(null)}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#868993]">
        <div>
          <span className="mr-3 inline-flex items-center gap-2">
            <span className="h-2 w-2 rounded-sm bg-[#26a69a]" /> {c.pnlLegend}
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="h-0.5 w-4 rounded-full bg-[#42a5f5]" /> {c.equityLegend}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span>
            {isZoomed
              ? `${visiblePoints.length}/${points.length} ${c.positions}`
              : `${points.length} ${c.positions}`}
            {` · ${c.pnlRange} ±${fmtKrw(maxAbsPnl)}`}
          </span>
          {isZoomed ? (
            <button
              type="button"
              onClick={resetZoom}
              className="rounded border border-[#26272d] bg-[#1a1b22] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[#c3c5cc] hover:bg-[#22232b]"
            >
              {c.resetZoom}
            </button>
          ) : (
            <span className="text-[10px] text-[#5b5d66]">{c.zoomHint}</span>
          )}
        </div>
      </div>

      {hovered ? (
        <div
          className="pointer-events-none fixed z-50 min-w-[200px] rounded border border-[#26272d] bg-[#1a1b22] px-3 py-2 text-xs shadow-lg"
          style={{ left: tipPos.x + 12, top: tipPos.y + 12 }}
        >
          <ul className="space-y-1 text-[#c3c5cc]">
            <li>#{hovered.index} · {hovered.symbol}</li>
            <li>{c.tip.entry}: {fmtTime(hovered.entryTs)}</li>
            <li>{c.tip.exit}: {fmtTime(hovered.timestamp)}</li>
            <li>
              {c.tip.pnl}:{" "}
              <span className={hovered.pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}>
                {fmtKrw(hovered.pnl)} ({fmtPct(hovered.returnPct)})
              </span>
            </li>
            <li>{c.tip.equity}: {fmtKrw(hovered.equity)}</li>
            <li>
              {c.tip.reason}:{" "}
              {hovered.exitReason === "period_end"
                ? labels.trades.reasons.period_end
                : labels.trades.reasons.target}
            </li>
          </ul>
        </div>
      ) : null}

      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className={`w-full touch-none select-none ${cursorClass}`}
        role="img"
        aria-label="Kimp trade PnL and equity chart"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
        onDoubleClick={resetZoom}
      >
        <defs>
          <clipPath id="kimp-chart-plot">
            <rect x={padding} y={padding} width={plotWidth} height={plotHeight} />
          </clipPath>
        </defs>
        <rect x={padding} y={padding} width={plotWidth} height={plotHeight} fill="#0b0c10" />
        <line x1={padding} y1={yZero} x2={width - padding} y2={yZero} stroke="#26272d" strokeWidth={1} />
        <g clipPath="url(#kimp-chart-plot)">
          {visiblePoints.map((p, idx) => {
            const xCenter = xForIndex(visibleStartIdx + idx);
            const y = yPnl(p.pnl);
            const barHeight = Math.max(2, Math.abs(y - yZero));
            const yTop = p.pnl >= 0 ? y : yZero;
            const color = p.pnl >= 0 ? "#26a69a" : "#ef5350";
            return (
              <rect
                key={`bar-${p.index}`}
                x={xCenter - barWidth / 2}
                y={yTop}
                width={barWidth}
                height={barHeight}
                fill={color}
                rx={2}
                onMouseEnter={(e) => {
                  if (isPanning) return;
                  setHovered(p);
                  setTipPos({ x: e.clientX, y: e.clientY });
                }}
                onMouseMove={(e) => {
                  if (isPanning) return;
                  setTipPos({ x: e.clientX, y: e.clientY });
                }}
              />
            );
          })}
          <path d={linePath} fill="none" stroke="#42a5f5" strokeWidth={2} />
          {visiblePoints.map((p, idx) => {
            const x = xForIndex(visibleStartIdx + idx);
            const y = yEq(p.equity);
            return (
              <g
                key={`pt-${p.index}`}
                onMouseEnter={(e) => {
                  if (isPanning) return;
                  setHovered(p);
                  setTipPos({ x: e.clientX, y: e.clientY });
                }}
                onMouseMove={(e) => {
                  if (isPanning) return;
                  setTipPos({ x: e.clientX, y: e.clientY });
                }}
              >
                <circle cx={x} cy={y} r={7} fill="transparent" />
                <circle cx={x} cy={y} r={3} fill="#42a5f5" />
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

function TradesTable({
  trades,
  symbol,
  labels,
}: {
  trades: KimpBacktestTrade[];
  symbol: string;
  labels: Labels;
}) {
  if (trades.length === 0) {
    return <div className="mt-3 text-xs text-[#868993]">{labels.trades.empty}</div>;
  }
  const c = labels.trades.columns;
  const wins = trades.filter((tr) => tr.net_pnl_krw > 0).length;
  const winRate = (wins / trades.length) * 100;
  const avgNet = trades.reduce((s, tr) => s + tr.net_pnl_krw, 0) / trades.length;

  const reasonLabel = (r: string): string =>
    r === "period_end" ? labels.trades.reasons.period_end : labels.trades.reasons.target;

  function downloadCsv() {
    const headers = [
      "index", "entry_time", "exit_time", "entry_kimp_pct", "exit_kimp_pct",
      "notional_krw", "kimp_pnl_krw", "funding_income_krw", "funding_events",
      "fee_krw", "net_pnl_krw", "return_pct", "holding_bars", "exit_reason",
    ];
    const rows = trades.map((tr) => [
      tr.index,
      new Date(tr.entry_t).toISOString(),
      new Date(tr.exit_t).toISOString(),
      tr.entry_kimp_pct.toFixed(4),
      tr.exit_kimp_pct.toFixed(4),
      tr.notional_krw.toFixed(0),
      tr.kimp_pnl_krw.toFixed(0),
      tr.funding_income_krw.toFixed(0),
      tr.funding_events,
      tr.fee_krw.toFixed(0),
      tr.net_pnl_krw.toFixed(0),
      tr.return_pct.toFixed(4),
      tr.holding_bars,
      tr.exit_reason,
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kimp_trades_${symbol}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2.5 py-1 text-[11px] text-[#c3c5cc]">
          {labels.trades.count.replace("{n}", String(trades.length))}
        </span>
        <span className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2.5 py-1 text-[11px] text-[#868993]">
          {labels.trades.winRate}:{" "}
          <span className={winRate >= 50 ? "text-emerald-400" : "text-rose-400"}>
            {fmtPct(winRate, 1)}
          </span>{" "}
          ({wins}/{trades.length})
        </span>
        <span className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2.5 py-1 text-[11px] text-[#868993]">
          {labels.trades.avgNet}: <span className={signClass(avgNet)}>{fmtKrw(avgNet)}</span>
        </span>
        <button
          type="button"
          onClick={downloadCsv}
          className="ml-auto rounded-md border border-[#26272d] bg-[#1a1b22] px-2.5 py-1 text-[11px] text-[#c3c5cc] hover:bg-[#22232b]"
        >
          {labels.trades.csv}
        </button>
      </div>
      <div className="max-h-[420px] overflow-auto rounded-md border border-[#26272d]">
        <table className="w-full min-w-[920px] text-left text-xs">
          <thead className="sticky top-0 bg-[#0e0f14] text-[10px] uppercase tracking-wider text-[#868993]">
            <tr>
              <th className="px-3 py-2">{c.idx}</th>
              <th className="px-3 py-2">{c.entryTime}</th>
              <th className="px-3 py-2">{c.exitTime}</th>
              <th className="px-3 py-2 text-right">{c.entryKimp}</th>
              <th className="px-3 py-2 text-right">{c.exitKimp}</th>
              <th className="px-3 py-2 text-right">{c.notional}</th>
              <th className="px-3 py-2 text-right">{c.kimpPnl}</th>
              <th className="px-3 py-2 text-right">{c.funding}</th>
              <th className="px-3 py-2 text-right">{c.fee}</th>
              <th className="px-3 py-2 text-right">{c.netPnl}</th>
              <th className="px-3 py-2 text-right">{c.returnPct}</th>
              <th className="px-3 py-2 text-right">{c.holding}</th>
              <th className="px-3 py-2">{c.reason}</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((tr) => (
              <tr key={tr.index} className="border-t border-[#1a1b22] tabular-nums hover:bg-[#1a1b22]">
                <td className="px-3 py-2 text-[#868993]">{tr.index}</td>
                <td className="px-3 py-2 text-[#c3c5cc]">{fmtTime(tr.entry_t)}</td>
                <td className="px-3 py-2 text-[#c3c5cc]">{fmtTime(tr.exit_t)}</td>
                <td className="px-3 py-2 text-right text-[#c3c5cc]">{fmtPct(tr.entry_kimp_pct)}</td>
                <td className="px-3 py-2 text-right text-[#c3c5cc]">{fmtPct(tr.exit_kimp_pct)}</td>
                <td className="px-3 py-2 text-right text-[#868993]">{fmtKrw(tr.notional_krw)}</td>
                <td className={`px-3 py-2 text-right ${signClass(tr.kimp_pnl_krw)}`}>{fmtKrw(tr.kimp_pnl_krw)}</td>
                <td className={`px-3 py-2 text-right ${signClass(tr.funding_income_krw)}`}>
                  {fmtKrw(tr.funding_income_krw)} / {tr.funding_events}
                </td>
                <td className="px-3 py-2 text-right text-[#868993]">{fmtKrw(tr.fee_krw)}</td>
                <td className={`px-3 py-2 text-right font-semibold ${signClass(tr.net_pnl_krw)}`}>{fmtKrw(tr.net_pnl_krw)}</td>
                <td className={`px-3 py-2 text-right ${signClass(tr.return_pct)}`}>{fmtPct(tr.return_pct)}</td>
                <td className="px-3 py-2 text-right text-[#868993]">{tr.holding_bars}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] ${
                      tr.exit_reason === "period_end"
                        ? "bg-amber-500/15 text-amber-400"
                        : "bg-emerald-500/15 text-emerald-400"
                    }`}
                  >
                    {reasonLabel(tr.exit_reason)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
