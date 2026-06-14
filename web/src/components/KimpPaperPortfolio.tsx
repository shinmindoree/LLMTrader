"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getKimpPaperPortfolio,
  startKimpPaperPortfolio,
  stopKimpPaperPortfolio,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { KimpPaperPortfolioStatus, KimpPaperSlotStatus } from "@/lib/types";

type Props = {
  onSelect: (symbol: string) => void;
  selected: string;
};

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function fmtScore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(3);
}

function fmtKrw(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${sign}₩${(abs / 1e8).toFixed(2)}억`;
  if (abs >= 1e4) return `${sign}₩${(abs / 1e4).toFixed(0)}만`;
  return `${sign}₩${abs.toFixed(0)}`;
}

function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString();
}

function signClass(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "text-[#868993]";
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-rose-400";
  return "text-[#c3c5cc]";
}

export default function KimpPaperPortfolio({ onSelect, selected }: Props) {
  const { t } = useI18n();
  const p = t.hubs.arbitrage.kimp.portfolio;

  const [topN, setTopN] = useState(3);
  const [capital, setCapital] = useState(10_000_000);
  const [candidateLimit, setCandidateLimit] = useState(30);
  const [rerankHours, setRerankHours] = useState(6);
  const [rankDays, setRankDays] = useState(30);

  const [status, setStatus] = useState<KimpPaperPortfolioStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getKimpPaperPortfolio());
    } catch {
      // best-effort
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function onStart() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const s = await startKimpPaperPortfolio({
        top_n: topN,
        capital_per_slot_krw: capital,
        candidate_limit: candidateLimit,
        rerank_hours: rerankHours,
        rank_days: rankDays,
      });
      setStatus(s);
      setMsg(p.startedMsg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : p.actionFailed);
    } finally {
      setBusy(false);
    }
  }

  async function onStop() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const s = await stopKimpPaperPortfolio();
      setStatus(s);
      setMsg(p.stoppedMsg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : p.actionFailed);
    } finally {
      setBusy(false);
    }
  }

  const running = status?.running ?? false;
  const slots = status?.slots ?? [];

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="flex flex-col gap-2 border-b border-[#26272d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-white">{p.title}</div>
          <div className="text-xs text-[#868993]">{p.subtitle}</div>
        </div>
        <span
          className={`inline-flex w-fit items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold ${
            running
              ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400"
              : "border-[#26272d] bg-[#1a1b22] text-[#868993]"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${running ? "bg-emerald-400" : "bg-[#5b5d66]"}`}
          />
          {running ? p.running : p.stopped}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 px-4 py-3 sm:grid-cols-5">
        <NumField label={p.fields.topN} value={topN} onChange={setTopN} min={1} max={10} disabled={running} />
        <NumField
          label={p.fields.capital}
          value={capital}
          onChange={setCapital}
          min={1}
          step={1_000_000}
          disabled={running}
        />
        <NumField
          label={p.fields.candidateLimit}
          value={candidateLimit}
          onChange={setCandidateLimit}
          min={1}
          max={200}
          disabled={running}
        />
        <NumField
          label={p.fields.rerankHours}
          value={rerankHours}
          onChange={setRerankHours}
          min={0.5}
          max={168}
          step={0.5}
          disabled={running}
        />
        <NumField
          label={p.fields.rankDays}
          value={rankDays}
          onChange={setRankDays}
          min={1}
          max={365}
          disabled={running}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 px-4 pb-3">
        {running ? (
          <button
            type="button"
            onClick={onStop}
            disabled={busy}
            className="rounded-md border border-rose-500/30 bg-rose-500/15 px-3 py-1.5 text-xs font-semibold text-rose-400 hover:bg-rose-500/25 disabled:opacity-50"
          >
            {busy ? p.stopping : p.stop}
          </button>
        ) : (
          <button
            type="button"
            onClick={onStart}
            disabled={busy}
            className="rounded-md border border-emerald-500/30 bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-50"
          >
            {busy ? p.starting : p.start}
          </button>
        )}

        {status ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#868993]">
            <span>
              {p.totals.slots}: <span className="text-[#c3c5cc]">{status.n_slots}</span>
            </span>
            <span>
              {p.totals.notional}:{" "}
              <span className="text-[#c3c5cc]">{fmtKrw(status.total_notional_krw)}</span>
            </span>
            <span>
              {p.totals.pnl}:{" "}
              <span className={signClass(status.total_unrealized_pnl_krw)}>
                {fmtKrw(status.total_unrealized_pnl_krw)}
              </span>
            </span>
            <span>
              {p.totals.fee}:{" "}
              <span className="text-[#c3c5cc]">{fmtKrw(status.total_fee_krw)}</span>
            </span>
            <span>
              {p.totals.lastRank}:{" "}
              <span className="text-[#c3c5cc]">{fmtTime(status.last_rank_ts)}</span>
            </span>
            <span>
              {p.totals.nextRank}:{" "}
              <span className="text-[#c3c5cc]">{fmtTime(status.next_rank_ts)}</span>
            </span>
          </div>
        ) : null}
      </div>

      {err ? (
        <div className="mx-4 mb-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-400">
          {err}
        </div>
      ) : null}
      {msg && !err ? (
        <div className="mx-4 mb-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-400">
          {msg}
        </div>
      ) : null}
      {status?.last_error ? (
        <div className="mx-4 mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-400">
          {status.last_error}
        </div>
      ) : null}

      <div className="overflow-x-auto border-t border-[#26272d]">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="bg-[#0e0f14] text-[10px] uppercase tracking-wider text-[#868993]">
            <tr>
              <th className="px-3 py-2">{p.columns.symbol}</th>
              <th className="px-3 py-2 text-right">{p.columns.score}</th>
              <th className="px-3 py-2 text-right">{p.columns.kimp}</th>
              <th className="px-3 py-2 text-right">{p.columns.zscore}</th>
              <th className="px-3 py-2 text-right">{p.columns.notional}</th>
              <th className="px-3 py-2 text-right">{p.columns.pnl}</th>
              <th className="px-3 py-2 text-right">{p.columns.fee}</th>
            </tr>
          </thead>
          <tbody>
            {slots.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-[#868993]">
                  {p.empty}
                </td>
              </tr>
            ) : (
              slots.map((s: KimpPaperSlotStatus) => {
                const isSel = s.symbol === selected;
                return (
                  <tr
                    key={s.symbol}
                    onClick={() => onSelect(s.symbol)}
                    className={`cursor-pointer border-t border-[#1a1b22] tabular-nums transition-colors hover:bg-[#1a1b22] ${
                      isSel ? "bg-[#1a1b22]" : ""
                    }`}
                  >
                    <td className="px-3 py-2 font-medium text-white">{s.symbol}</td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">{fmtScore(s.score)}</td>
                    <td className={`px-3 py-2 text-right ${signClass(s.kimp_pct)}`}>
                      {fmtPct(s.kimp_pct)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">{fmtNum(s.zscore)}</td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">
                      {fmtKrw(s.current_notional_krw)}
                    </td>
                    <td className={`px-3 py-2 text-right ${signClass(s.unrealized_pnl_krw)}`}>
                      {fmtKrw(s.unrealized_pnl_krw)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">
                      {fmtKrw(s.accumulated_fee_krw)}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
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
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
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
        disabled={disabled}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(v);
        }}
        className="w-full rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] focus:border-[#3a3b44] focus:outline-none disabled:opacity-50"
      />
    </label>
  );
}
