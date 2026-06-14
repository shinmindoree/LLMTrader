"use client";

import { useState } from "react";

import { runKimpBacktest, runKimpUniverseBacktest } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type {
  KimpBacktestEquityPoint,
  KimpBacktestResponse,
  KimpBacktestTrade,
  KimpHedgeMode,
  KimpUniverseBacktestItem,
  KimpUniverseBacktestResponse,
} from "@/lib/types";

type Props = {
  symbol: string;
  onSelect: (symbol: string) => void;
};

type SharedConfig = {
  days: number;
  grossCap: number;
  fullBuildZ: number;
  flatZ: number;
  hedgeMode: KimpHedgeMode;
  includeFunding: boolean;
};

const DEFAULT_CONFIG: SharedConfig = {
  days: 30,
  grossCap: 10_000_000,
  fullBuildZ: -2.0,
  flatZ: 0.5,
  hedgeMode: "quantity",
  includeFunding: true,
};

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

export default function KimpBacktestPanel({ symbol, onSelect }: Props) {
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
}: {
  busy: boolean;
  res: KimpBacktestResponse | null;
  err: string | null;
  onRun: () => void;
  labels: Labels;
  symbol: string;
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
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-[#868993]">
          {labels.tabs.single}:{" "}
          <span className="font-semibold text-emerald-400">{symbol}</span>
        </div>
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
              </div>
              <div className="mt-3">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-[#868993]">
                  {labels.equityTitle}
                </div>
                <EquityCurve data={res?.equity_curve ?? []} trades={trades} labels={labels} />
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

function EquityCurve({
  data,
  trades = [],
  labels,
}: {
  data: KimpBacktestEquityPoint[];
  trades?: KimpBacktestTrade[];
  labels?: Labels;
}) {
  if (data.length < 2) {
    return (
      <div className="flex h-[120px] items-center justify-center text-xs text-[#5b5d66]">—</div>
    );
  }
  const width = 640;
  const height = 120;
  const pad = 8;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const eq = data.map((d) => d.equity_krw);
  const minE = Math.min(...eq);
  const maxE = Math.max(...eq);
  const range = maxE - minE || 1;
  const base = eq[0];

  const sx = (i: number) => pad + (i / (data.length - 1)) * innerW;
  const sy = (v: number) => pad + innerH - ((v - minE) / range) * innerH;

  const baseY = sy(base);
  const pts = data.map((d, i) => ({ x: sx(i), y: sy(d.equity_krw) }));
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1].x.toFixed(1)},${baseY.toFixed(1)} L${pts[0].x.toFixed(1)},${baseY.toFixed(1)} Z`;

  const final = eq[eq.length - 1];
  const up = final >= base;
  const stroke = up ? "#22c55e" : "#ef4444";
  const fill = up ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)";

  const nearestIdx = (tMs: number): number => {
    let lo = 0;
    let hi = data.length - 1;
    let best = 0;
    let bestDiff = Infinity;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const diff = data[mid].t - tMs;
      if (Math.abs(diff) < bestDiff) {
        bestDiff = Math.abs(diff);
        best = mid;
      }
      if (diff < 0) lo = mid + 1;
      else if (diff > 0) hi = mid - 1;
      else return mid;
    }
    return best;
  };

  const markers = trades.flatMap((tr) => {
    const ei = nearestIdx(tr.entry_t);
    const xi = nearestIdx(tr.exit_t);
    return [
      { x: sx(ei), y: pts[ei].y, kind: "entry" as const },
      { x: sx(xi), y: pts[xi].y, kind: "exit" as const },
    ];
  });

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        preserveAspectRatio="none"
      >
        <line
          x1={pad}
          y1={baseY}
          x2={width - pad}
          y2={baseY}
          stroke="#3f3f46"
          strokeWidth={0.5}
          strokeDasharray="3 3"
        />
        <path d={area} fill={fill} />
        <path d={line} fill="none" stroke={stroke} strokeWidth={1.5} />
        {markers.map((mk, i) => (
          <circle
            key={i}
            cx={mk.x}
            cy={mk.y}
            r={2.4}
            fill={mk.kind === "entry" ? "#22c55e" : "#ef4444"}
            stroke="#0e0f14"
            strokeWidth={0.6}
          />
        ))}
      </svg>
      {labels && trades.length > 0 ? (
        <div className="mt-1 flex items-center gap-3 text-[10px] text-[#868993]">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            {labels.trades.entryMarker}
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-rose-500" />
            {labels.trades.exitMarker}
          </span>
        </div>
      ) : null}
    </>
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
