"use client";

import { useCallback, useEffect, useState } from "react";

import { getKimpArbStatus, startKimpArb, stopKimpArb } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { KimpArbMode, KimpArbitrageStatusResponse } from "@/lib/types";

type Props = {
  symbol: string;
};

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtRatio(v: number | null | undefined, digits = 2): string {
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

function fmtQty(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function signClass(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "text-[#868993]";
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-rose-400";
  return "text-[#c3c5cc]";
}

export default function KimpBotControl({ symbol }: Props) {
  const { t } = useI18n();
  const c = t.hubs.arbitrage.kimp.bot;

  const [mode, setMode] = useState<KimpArbMode>("paper");
  const [status, setStatus] = useState<KimpArbitrageStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getKimpArbStatus();
      setStatus(s);
      if (s.running) setMode(s.mode);
    } catch {
      // status is best-effort
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
      const s = await startKimpArb({ symbol: symbol.trim().toUpperCase() || "BTC", mode });
      setStatus(s);
      setMsg(mode === "paper" ? c.startedPaper : c.startedLive);
    } catch (e) {
      setErr(e instanceof Error ? e.message : c.actionFailed);
    } finally {
      setBusy(false);
    }
  }

  async function onStop() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const s = await stopKimpArb();
      setStatus(s);
      setMsg(c.stoppedMsg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : c.actionFailed);
    } finally {
      setBusy(false);
    }
  }

  const running = status?.running ?? false;

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="flex flex-col gap-2 border-b border-[#26272d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-white">{c.title}</div>
          <div className="text-xs text-[#868993]">{c.subtitle}</div>
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
          {running ? c.running : c.stopped}
        </span>
      </div>

      <div className="flex flex-col gap-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wider text-[#868993]">
              {c.modeLabel}
            </span>
            <div className="inline-flex rounded-md border border-[#26272d] bg-[#0e0f14] p-0.5">
              {(["paper", "live"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={running}
                  onClick={() => setMode(m)}
                  className={`rounded px-3 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50 ${
                    mode === m
                      ? "bg-[#22232b] text-white"
                      : "text-[#868993] hover:text-[#c3c5cc]"
                  }`}
                >
                  {c.modes[m]}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 text-[11px] text-[#868993]">
            {mode === "paper" ? c.paperHint : c.liveHint}
          </div>

          {running ? (
            <button
              type="button"
              onClick={onStop}
              disabled={busy}
              className="rounded-md border border-rose-500/30 bg-rose-500/15 px-3 py-1.5 text-xs font-semibold text-rose-400 hover:bg-rose-500/25 disabled:opacity-50"
            >
              {busy ? c.stopping : c.stop}
            </button>
          ) : (
            <button
              type="button"
              onClick={onStart}
              disabled={busy}
              className="rounded-md border border-emerald-500/30 bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-50"
            >
              {busy ? c.starting : c.start}
            </button>
          )}
        </div>

        {err ? (
          <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-400">
            {err}
          </div>
        ) : null}
        {msg && !err ? (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-400">
            {msg}
          </div>
        ) : null}

        {status ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            <Stat label={c.status.mode} value={c.modes[status.mode]} />
            <Stat label={c.status.symbol} value={status.symbol ?? "—"} />
            <Stat label={c.status.kimp} value={fmtPct(status.kimp_pct)} cls={signClass(status.kimp_pct)} />
            <Stat label={c.status.zscore} value={fmtRatio(status.zscore)} />
            <Stat label={c.status.targetNotional} value={fmtKrw(status.target_notional_krw)} />
            <Stat label={c.status.currentNotional} value={fmtKrw(status.current_notional_krw)} />
            <Stat label={c.status.upbitQty} value={fmtQty(status.upbit_long_qty)} />
            <Stat label={c.status.binanceQty} value={fmtQty(status.binance_short_qty)} />
            <Stat
              label={c.status.unrealizedPnl}
              value={fmtKrw(status.unrealized_pnl_krw)}
              cls={signClass(status.unrealized_pnl_krw)}
            />
            <Stat label={c.status.fee} value={fmtKrw(status.accumulated_fee_krw)} cls="text-[#868993]" />
            <Stat label={c.status.margin} value={fmtRatio(status.binance_margin_ratio)} />
          </div>
        ) : null}

        {status?.last_error ? (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-400">
            {status.last_error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="rounded-md border border-[#26272d] bg-[#0e0f14] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-[#868993]">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${cls ?? "text-white"}`}>
        {value}
      </div>
    </div>
  );
}
