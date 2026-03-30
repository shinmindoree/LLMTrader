"use client";

import Link from "next/link";

import { useI18n } from "@/lib/i18n";
import type { StrategyInfo } from "@/lib/types";

/** Pre-configured quick-start backtest templates. */
const QUICK_TEMPLATES = [
  {
    strategyName: "rsi_oversold_bounce_long_strategy.py",
    label: "RSI Oversold Bounce",
    symbol: "BTCUSDT",
    interval: "1h",
  },
  {
    strategyName: "ema_crossover_long_strategy.py",
    label: "EMA Crossover",
    symbol: "ETHUSDT",
    interval: "15m",
  },
  {
    strategyName: "macd_crossover_long_strategy.py",
    label: "MACD Crossover",
    symbol: "BTCUSDT",
    interval: "4h",
  },
] as const;

/* ── SVG icon helpers (inline to avoid extra deps) ── */

function CandleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="32" height="32" viewBox="0 0 32 32" fill="none">
      <rect x="6" y="8" width="4" height="16" rx="1" fill="#2962ff" opacity=".7" />
      <line x1="8" y1="4" x2="8" y2="8" stroke="#2962ff" strokeWidth="1.5" />
      <line x1="8" y1="24" x2="8" y2="28" stroke="#2962ff" strokeWidth="1.5" />
      <rect x="14" y="12" width="4" height="10" rx="1" fill="#26a69a" opacity=".7" />
      <line x1="16" y1="6" x2="16" y2="12" stroke="#26a69a" strokeWidth="1.5" />
      <line x1="16" y1="22" x2="16" y2="28" stroke="#26a69a" strokeWidth="1.5" />
      <rect x="22" y="10" width="4" height="14" rx="1" fill="#ef5350" opacity=".7" />
      <line x1="24" y1="4" x2="24" y2="10" stroke="#ef5350" strokeWidth="1.5" />
      <line x1="24" y1="24" x2="24" y2="28" stroke="#ef5350" strokeWidth="1.5" />
    </svg>
  );
}

function BotIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="32" height="32" viewBox="0 0 32 32" fill="none">
      <rect x="6" y="10" width="20" height="14" rx="4" stroke="#2962ff" strokeWidth="1.5" />
      <circle cx="12" cy="17" r="2" fill="#2962ff" />
      <circle cx="20" cy="17" r="2" fill="#2962ff" />
      <path d="M16 4v6" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="16" cy="3" r="1.5" fill="#2962ff" />
      <path d="M12 22v4M20 22v4" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function ChartIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="32" height="32" viewBox="0 0 32 32" fill="none">
      <rect x="4" y="4" width="24" height="24" rx="3" stroke="#2962ff" strokeWidth="1.5" />
      <polyline points="8,22 13,16 18,19 24,10" fill="none" stroke="#26a69a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="13" cy="16" r="1.5" fill="#26a69a" />
      <circle cx="18" cy="19" r="1.5" fill="#26a69a" />
      <circle cx="24" cy="10" r="1.5" fill="#26a69a" />
    </svg>
  );
}

function ArrowRight() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="hidden sm:block shrink-0 text-[#363a45]">
      <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ZapIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" className="shrink-0">
      <path d="M8.5 1L3 9h4.5l-1 6L13 7H8.5l1-6z" />
    </svg>
  );
}

export interface BacktestEmptyStateProps {
  strategies: StrategyInfo[];
  onOpenForm: () => void;
}

export function BacktestEmptyState({ strategies, onOpenForm }: BacktestEmptyStateProps) {
  const { t } = useI18n();
  const ob = t.backtest.onboarding;

  const resolveTemplate = (templateName: string) =>
    strategies.find((s) => s.name === templateName);

  const handleQuickRun = (tmpl: (typeof QUICK_TEMPLATES)[number]) => {
    const strat = resolveTemplate(tmpl.strategyName);
    if (!strat) return;
    // Store defaults so BacktestForm picks them up
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    try {
      window.localStorage.setItem(
        "llmtrader.execution_defaults",
        JSON.stringify({ symbol: tmpl.symbol, interval: tmpl.interval }),
      );
      window.localStorage.setItem(
        "llmtrader.quickrun",
        JSON.stringify({
          strategy: strat.path,
          symbol: tmpl.symbol,
          interval: tmpl.interval,
          start: fmt(start),
          end: fmt(end),
        }),
      );
    } catch { /* ignore */ }
    onOpenForm();
  };

  return (
    <div className="space-y-6">
      {/* ── Hero card ── */}
      <div className="rounded-xl border border-[#2a2e39] bg-gradient-to-br from-[#1e222d] to-[#181c25] p-6 text-center">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[#2962ff]/10">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2962ff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" />
            <polyline points="7,14 11,10 15,13 21,7" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-[#d1d4dc]">{ob.title}</h2>
        <p className="mt-1 text-sm text-[#868993] max-w-md mx-auto">{ob.subtitle}</p>

        <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={onOpenForm}
            className="rounded-lg bg-[#2962ff] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#1e4fd8] transition-colors"
          >
            {ob.ctaStart}
          </button>
          <Link
            href="/strategies"
            className="rounded-lg border border-[#2a2e39] bg-[#1e222d] px-5 py-2.5 text-sm text-[#d1d4dc] hover:border-[#2962ff] transition-colors"
          >
            {ob.ctaStrategies}
          </Link>
        </div>
      </div>

      {/* ── How it works diagram ── */}
      <div className="rounded-xl border border-[#2a2e39] bg-[#1e222d] p-5">
        <h3 className="mb-4 text-sm font-medium text-[#d1d4dc]">{ob.howItWorksTitle}</h3>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 sm:gap-6">
          <div className="flex flex-col items-center text-center w-32">
            <CandleIcon />
            <span className="mt-2 text-xs font-medium text-[#d1d4dc]">{ob.howStep1}</span>
            <span className="text-[10px] text-[#868993]">{ob.howStep1Sub}</span>
          </div>
          <ArrowRight />
          <div className="flex flex-col items-center text-center w-32">
            <BotIcon />
            <span className="mt-2 text-xs font-medium text-[#d1d4dc]">{ob.howStep2}</span>
            <span className="text-[10px] text-[#868993]">{ob.howStep2Sub}</span>
          </div>
          <ArrowRight />
          <div className="flex flex-col items-center text-center w-32">
            <ChartIcon />
            <span className="mt-2 text-xs font-medium text-[#d1d4dc]">{ob.howStep3}</span>
            <span className="text-[10px] text-[#868993]">{ob.howStep3Sub}</span>
          </div>
        </div>
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

      {/* ── Quick-run templates ── */}
      {strategies.length > 0 && (
        <div className="rounded-xl border border-[#2a2e39] bg-[#1e222d] p-5">
          <div className="flex items-center gap-2 mb-1">
            <ZapIcon />
            <h3 className="text-sm font-medium text-[#d1d4dc]">{ob.quickRunTitle}</h3>
          </div>
          <p className="mb-4 text-xs text-[#868993]">{ob.quickRunDesc}</p>
          <div className="grid gap-2 sm:grid-cols-3">
            {QUICK_TEMPLATES.map((tmpl) => {
              const exists = resolveTemplate(tmpl.strategyName);
              return (
                <button
                  key={tmpl.strategyName}
                  type="button"
                  disabled={!exists}
                  onClick={() => handleQuickRun(tmpl)}
                  className="group flex items-center justify-between rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 text-left transition-colors hover:border-[#2962ff] disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <div>
                    <div className="text-sm font-medium text-[#d1d4dc] group-hover:text-[#2962ff] transition-colors">
                      {tmpl.label}
                    </div>
                    <div className="text-[10px] text-[#868993]">
                      {tmpl.symbol} · {tmpl.interval} · {ob.quickRunRecent7d}
                    </div>
                  </div>
                  <span className="ml-3 shrink-0 rounded bg-[#2962ff]/10 px-2.5 py-1 text-xs font-medium text-[#2962ff] group-hover:bg-[#2962ff] group-hover:text-white transition-colors">
                    {ob.quickRunAction}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
