"use client";

import { useState } from "react";

import { runKimpBacktest, runKimpUniverseBacktest } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type {
  KimpBacktestEquityPoint,
  KimpBacktestResponse,
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
  zWindow: number;
  leverage: number;
  hedgeMode: KimpHedgeMode;
  includeFunding: boolean;
};

const DEFAULT_CONFIG: SharedConfig = {
  days: 30,
  grossCap: 10_000_000,
  fullBuildZ: -2.0,
  flatZ: 0.5,
  zWindow: 720,
  leverage: 1.0,
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
        leverage: cfg.leverage,
        z_window_points: cfg.zWindow,
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
        leverage: cfg.leverage,
        z_window_points: cfg.zWindow,
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
        <NumField
          label={b.fields.zWindow}
          value={cfg.zWindow}
          onChange={(v) => patch("zWindow", v)}
          min={10}
          max={43200}
        />
        <NumField
          label={b.fields.leverage}
          value={cfg.leverage}
          onChange={(v) => patch("leverage", v)}
          min={1}
          max={10}
          step={0.5}
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
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            <Metric label={labels.metrics.totalReturn} value={fmtPct(m.total_return_pct)} cls={signClass(m.total_return_pct)} />
            <Metric label={labels.metrics.netProfit} value={fmtKrw(m.net_profit_krw)} cls={signClass(m.net_profit_krw)} />
            <Metric label={labels.metrics.funding} value={fmtKrw(m.funding_income_krw)} cls={signClass(m.funding_income_krw)} />
            <Metric label={labels.metrics.mdd} value={fmtPct(m.max_drawdown_pct)} cls="text-rose-400" />
            <Metric label={labels.metrics.sharpe} value={fmtNum(m.sharpe)} />
            <Metric label={labels.metrics.rebalances} value={String(m.n_rebalances)} />
            <Metric label={labels.metrics.feeDrag} value={fmtKrw(m.fee_drag_krw)} cls="text-[#868993]" />
            <Metric label={labels.metrics.timeInMarket} value={fmtPct(m.time_in_market_pct)} />
            <Metric label={labels.metrics.avgKimp} value={fmtPct(m.avg_kimp_pct)} />
            <Metric label={labels.metrics.finalKimp} value={fmtPct(m.final_kimp_pct)} />
            <Metric label={labels.metrics.bars} value={String(m.n_bars)} />
          </div>
          <div className="mt-3">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-[#868993]">
              {labels.equityTitle}
            </div>
            <EquityCurve data={res?.equity_curve ?? []} />
          </div>
          <div className="mt-3">
            <div className="mb-1 flex items-center gap-3 text-[10px] uppercase tracking-wider text-[#868993]">
              <span>{labels.overlayTitle}</span>
              <span className="flex items-center gap-1 normal-case">
                <span className="inline-block h-0.5 w-3 bg-[#38bdf8]" />
                {labels.kimpLegend}
              </span>
              <span className="flex items-center gap-1 normal-case">
                <span className="inline-block h-0.5 w-3 bg-[#f59e0b]" />
                {labels.zLegend}
              </span>
            </div>
            <KimpZOverlay data={res?.equity_curve ?? []} />
          </div>
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
                      {fmtKrw(it.metrics?.funding_income_krw)}
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

function EquityCurve({ data }: { data: KimpBacktestEquityPoint[] }) {
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

  return (
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
    </svg>
  );
}

function buildLine(
  values: (number | null)[],
  sx: (i: number) => number,
  sy: (v: number) => number,
): string {
  let path = "";
  let started = false;
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      started = false;
      return;
    }
    const cmd = started ? "L" : "M";
    path += `${cmd}${sx(i).toFixed(1)},${sy(v).toFixed(1)} `;
    started = true;
  });
  return path.trim();
}

function KimpZOverlay({ data }: { data: KimpBacktestEquityPoint[] }) {
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
  const sx = (i: number) => pad + (i / (data.length - 1)) * innerW;

  const kimps = data.map((d) => d.kimp_pct);
  const minK = Math.min(...kimps, 0);
  const maxK = Math.max(...kimps, 0);
  const rangeK = maxK - minK || 1;
  const syK = (v: number) => pad + innerH - ((v - minK) / rangeK) * innerH;

  const zs = data.map((d) => d.zscore);
  const finiteZ = zs.filter((z): z is number => z != null && Number.isFinite(z));
  const absZ = finiteZ.length ? Math.max(2, ...finiteZ.map((z) => Math.abs(z))) : 2;
  const syZ = (v: number) => pad + innerH - ((v + absZ) / (2 * absZ)) * innerH;

  const kimpPath = buildLine(kimps, sx, syK);
  const zPath = buildLine(zs, sx, syZ);
  const zeroY = syK(0);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
      style={{ height }}
      preserveAspectRatio="none"
    >
      <line
        x1={pad}
        y1={zeroY}
        x2={width - pad}
        y2={zeroY}
        stroke="#3f3f46"
        strokeWidth={0.5}
        strokeDasharray="3 3"
      />
      {zPath ? <path d={zPath} fill="none" stroke="#f59e0b" strokeWidth={1} opacity={0.85} /> : null}
      {kimpPath ? <path d={kimpPath} fill="none" stroke="#38bdf8" strokeWidth={1.5} /> : null}
    </svg>
  );
}
