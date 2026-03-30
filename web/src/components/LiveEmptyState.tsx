"use client";

import Link from "next/link";

import { useI18n } from "@/lib/i18n";
import type { StrategyInfo, BinanceKeysStatus } from "@/lib/types";

/* ── Check icons ── */

function CheckCircle() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="shrink-0">
      <circle cx="9" cy="9" r="8" fill="#26a69a" opacity=".15" />
      <path d="M5.5 9.5l2 2 5-5" stroke="#26a69a" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function WarnCircle() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="shrink-0">
      <circle cx="9" cy="9" r="8" fill="#efb74d" opacity=".15" />
      <path d="M9 6v3M9 11.5v.5" stroke="#efb74d" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function EmptyCircle() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="shrink-0">
      <circle cx="9" cy="9" r="8" stroke="#363a45" strokeWidth="1.5" strokeDasharray="3 3" />
    </svg>
  );
}

export interface LiveEmptyStateProps {
  strategies: StrategyInfo[];
  keysStatus: BinanceKeysStatus | undefined;
  hasBacktestHistory: boolean;
  onOpenForm: () => void;
  activeCount: number;
  maxSlots: number;
}

export function LiveEmptyState({
  strategies,
  keysStatus,
  hasBacktestHistory,
  onOpenForm,
  activeCount,
  maxSlots,
}: LiveEmptyStateProps) {
  const { t } = useI18n();
  const ob = t.live.onboarding;

  const hasStrategies = strategies.length > 0;
  const keysOk = keysStatus?.configured ?? false;

  const checks = [
    { ok: true, label: ob.checkAccount },
    {
      ok: hasStrategies,
      label: hasStrategies
        ? ob.checkStrategiesCount.replace("{count}", String(strategies.length))
        : ob.checkStrategies,
      warn: false,
    },
    {
      ok: keysOk,
      label: ob.checkKeys,
      action: !keysOk ? { href: "/settings", label: ob.checkKeysAction } : undefined,
      warn: !keysOk,
    },
    {
      ok: hasBacktestHistory,
      label: ob.checkBacktest,
      action: !hasBacktestHistory ? { href: "/backtest", label: ob.checkBacktestAction } : undefined,
      warn: false,
    },
  ];

  const allReady = checks.every((c) => c.ok);

  return (
    <div className="space-y-6">
      {/* ── Hero card ── */}
      <div className="rounded-xl border border-[#2a2e39] bg-gradient-to-br from-[#1e222d] to-[#181c25] p-6 text-center">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[#26a69a]/10">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#26a69a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12,6 12,12 16,14" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-[#d1d4dc]">{ob.title}</h2>
        <p className="mt-1 text-sm text-[#868993] max-w-md mx-auto">{ob.subtitle}</p>

        <button
          type="button"
          onClick={onOpenForm}
          disabled={activeCount >= maxSlots}
          className="mt-5 rounded-lg bg-[#26a69a] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#1e8c82] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {ob.ctaStart}
        </button>
      </div>

      {/* ── Prerequisite checklist ── */}
      <div className="rounded-xl border border-[#2a2e39] bg-[#1e222d] p-5">
        <h3 className="mb-4 text-sm font-medium text-[#d1d4dc]">
          {allReady ? ob.readyMessage : ob.notReadyMessage}
        </h3>
        <ul className="space-y-3">
          {checks.map((c, i) => (
            <li key={i} className="flex items-center gap-3 text-sm">
              {c.ok ? <CheckCircle /> : c.warn ? <WarnCircle /> : <EmptyCircle />}
              <span className={c.ok ? "text-[#d1d4dc]" : "text-[#868993]"}>{c.label}</span>
              {c.action && (
                <Link
                  href={c.action.href}
                  className="ml-auto shrink-0 rounded bg-[#2962ff]/10 px-2.5 py-1 text-xs font-medium text-[#2962ff] hover:bg-[#2962ff] hover:text-white transition-colors"
                >
                  {c.action.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* ── 3-step guide ── */}
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { num: "1", title: ob.step1Title, desc: ob.step1Desc, accent: "text-[#2962ff]" },
          { num: "2", title: ob.step2Title, desc: ob.step2Desc, accent: "text-[#26a69a]" },
          { num: "3", title: ob.step3Title, desc: ob.step3Desc, accent: "text-[#e040fb]" },
        ].map((step) => (
          <div key={step.num} className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
            <div className={`text-lg font-bold ${step.accent}`}>{step.num}</div>
            <div className="mt-1 text-sm font-medium text-[#d1d4dc]">{step.title}</div>
            <div className="mt-0.5 text-xs text-[#868993]">{step.desc}</div>
          </div>
        ))}
      </div>

      {/* ── Safety tip ── */}
      <div className="flex items-start gap-3 rounded-lg border border-[#26a69a]/20 bg-[#1a2e2a] px-4 py-3">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="mt-0.5 shrink-0">
          <path d="M8 1l6.5 12H1.5L8 1z" stroke="#26a69a" strokeWidth="1.2" strokeLinejoin="round" />
          <path d="M8 6v3M8 11v.5" stroke="#26a69a" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
        <p className="text-xs text-[#26a69a]/90">{ob.safetyTip}</p>
      </div>
    </div>
  );
}
