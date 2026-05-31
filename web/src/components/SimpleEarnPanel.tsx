"use client";

import { useState } from "react";
import useSWR from "swr";
import { getAutoSweepSettings, setAutoSweepSettings } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { AutoSweepSettings } from "@/lib/types";
import { StrategyCard } from "@/components/StrategyHub";

const REFRESH_MS = 30_000;

function fmtUsdt(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function SimpleEarnPanel() {
  const { t } = useI18n();
  const s = t.settingsPage;
  const m = t.hubs.yield.simpleEarn;

  const { data, isLoading, mutate } = useSWR<AutoSweepSettings>(
    "autoSweepSettings",
    () => getAutoSweepSettings(),
    { refreshInterval: REFRESH_MS },
  );

  const [enabled, setEnabled] = useState(false);
  const [futuresBuffer, setFuturesBuffer] = useState<string>("200");
  const [sweepThreshold, setSweepThreshold] = useState<string>("50");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [hydrated, setHydrated] = useState(false);

  if (data && !hydrated) {
    setEnabled(data.enabled);
    setFuturesBuffer(String(data.futures_buffer_usdt));
    setSweepThreshold(String(data.sweep_threshold_usdt));
    setHydrated(true);
  }

  const mainnetRequired = data?.mainnet_required ?? false;
  const keysConfigured = data?.keys_configured ?? false;
  const blocked = mainnetRequired || !keysConfigured;

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const buf = Number.parseFloat(futuresBuffer);
      const thr = Number.parseFloat(sweepThreshold);
      if (!Number.isFinite(buf) || !Number.isFinite(thr) || buf < 0 || thr < 0) {
        throw new Error("Invalid number");
      }
      const result = await setAutoSweepSettings({
        enabled,
        futures_buffer_usdt: buf,
        sweep_threshold_usdt: thr,
      });
      await mutate(result, { revalidate: false });
      setMessage({ type: "success", text: s.autoSweepSaved });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessage({ type: "error", text: `${s.autoSweepSaveFailed}: ${msg}` });
    } finally {
      setSaving(false);
    }
  };

  const statusLabel = (action: string | null | undefined): string => {
    if (action === "subscribed") return s.autoSweepStatusSubscribed;
    if (action === "redeemed") return s.autoSweepStatusRedeemed;
    if (action === "noop") return s.autoSweepStatusNoop;
    if (action === "error") return s.autoSweepStatusError;
    return action ?? "—";
  };

  const futures = data?.futures_usdt ?? null;
  const earn = data?.earn_usdt ?? null;
  const hasBalances = futures !== null || earn !== null;
  const total = (futures ?? 0) + (earn ?? 0);
  const earnPct = total > 0 ? Math.round(((earn ?? 0) / total) * 100) : 0;
  const futuresPct = 100 - earnPct;

  return (
    <StrategyCard
      name={m.name}
      badge={m.badge}
      desc={m.desc}
      active={data?.enabled ?? false}
      statusLabel={(data?.enabled ?? false) ? t.hubs.statusActive : s.autoSweepOff}
    >
      {/* Monitoring */}
      <div className="rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[#868993]">{m.monitorTitle}</h3>
          <span className="text-[10px] text-[#555]">{m.refreshNote}</span>
        </div>

        {hasBalances ? (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <div className="text-[11px] text-[#868993]">{m.totalManaged}</div>
                <div className="mt-0.5 text-base font-semibold text-[#d1d4dc]">{fmtUsdt(total)}</div>
              </div>
              <div>
                <div className="text-[11px] text-[#868993]">{m.inFutures}</div>
                <div className="mt-0.5 text-base font-semibold text-[#d1d4dc]">{fmtUsdt(futures)}</div>
              </div>
              <div>
                <div className="text-[11px] text-[#868993]">{m.inEarn}</div>
                <div className="mt-0.5 text-base font-semibold text-[#f0b90b]">{fmtUsdt(earn)}</div>
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between text-[11px] text-[#868993]">
                <span>{m.allocation}</span>
                <span>{m.deployedRatio}: {earnPct}%</span>
              </div>
              <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-[#2a2e39]">
                <div className="h-full bg-[#363a45]" style={{ width: `${futuresPct}%` }} />
                <div className="h-full bg-[#f0b90b]" style={{ width: `${earnPct}%` }} />
              </div>
              <div className="mt-1.5 flex items-center gap-4 text-[10px] text-[#868993]">
                <span className="inline-flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-[#363a45]" />
                  {m.inFutures} {futuresPct}%
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-[#f0b90b]" />
                  {m.inEarn} {earnPct}%
                </span>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-[#2a2e39] pt-3 text-xs sm:grid-cols-3">
              <div>
                <div className="text-[#868993]">{s.autoSweepLastRun}</div>
                <div className="mt-0.5 text-[#d1d4dc]">
                  {data?.last_run_at ? new Date(data.last_run_at).toLocaleString() : "—"}
                </div>
              </div>
              <div>
                <div className="text-[#868993]">{s.autoSweepLastAction}</div>
                <div className="mt-0.5 text-[#d1d4dc]">{statusLabel(data?.last_action)}</div>
              </div>
              {data?.last_error && (
                <div className="col-span-2 sm:col-span-1">
                  <div className="text-[#868993]">Error</div>
                  <div className="mt-0.5 text-[#ef5350]">{data.last_error}</div>
                </div>
              )}
            </div>
          </>
        ) : (
          <p className="text-xs text-[#868993]">{m.noData}</p>
        )}
      </div>

      {/* Settings */}
      <div className="mt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <p className="text-sm font-medium text-[#d1d4dc]">{s.autoSweepLabel}</p>
            <p className="mt-1 text-xs text-[#868993]">{s.autoSweepDesc}</p>
            <p className={`mt-2 text-xs font-medium ${enabled ? "text-[#f0b90b]" : "text-[#555]"}`}>
              {enabled ? s.autoSweepOn : s.autoSweepOff}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            onClick={() => !blocked && setEnabled((v) => !v)}
            disabled={blocked}
            className={[
              "relative mt-1 inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#2962ff]",
              blocked ? "cursor-not-allowed opacity-40" : "",
              enabled ? "bg-[#f0b90b]" : "bg-[#2a2e39]",
            ].join(" ")}
          >
            <span
              className={[
                "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform duration-200",
                enabled ? "translate-x-5" : "translate-x-0",
              ].join(" ")}
            />
          </button>
        </div>

        {mainnetRequired && (
          <p className="mt-3 rounded border border-[#5a3a1a] bg-[#2a1a0a] px-3 py-2 text-xs text-[#f0b90b]">
            {s.autoSweepMainnetRequired}
          </p>
        )}
        {!mainnetRequired && !keysConfigured && (
          <p className="mt-3 rounded border border-[#5a3a1a] bg-[#2a1a0a] px-3 py-2 text-xs text-[#f0b90b]">
            {s.autoSweepKeysRequired}
          </p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs text-[#868993]">
            {s.autoSweepFuturesBuffer}
            <input
              type="number"
              min={0}
              step="1"
              value={futuresBuffer}
              onChange={(e) => setFuturesBuffer(e.target.value)}
              disabled={blocked}
              className="mt-1 w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] disabled:opacity-50"
            />
          </label>
          <label className="block text-xs text-[#868993]">
            {s.autoSweepSweepThreshold}
            <input
              type="number"
              min={0}
              step="1"
              value={sweepThreshold}
              onChange={(e) => setSweepThreshold(e.target.value)}
              disabled={blocked}
              className="mt-1 w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] disabled:opacity-50"
            />
          </label>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || isLoading || blocked}
            className="rounded bg-[#2962ff] px-4 py-1.5 text-sm font-medium text-white hover:bg-[#2456e6] disabled:opacity-50"
          >
            {saving ? s.autoSweepSaving : s.autoSweepSave}
          </button>
          {message && (
            <span className={`text-xs ${message.type === "success" ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
              {message.text}
            </span>
          )}
        </div>
      </div>
    </StrategyCard>
  );
}
